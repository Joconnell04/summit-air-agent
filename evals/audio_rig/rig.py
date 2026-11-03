"""The simulated audio bake-off (DECISIONS.md 026, docs/bakeoffs.md bake-off 6).

    uv run --env-file .env python -m evals.audio_rig --stack elevenlabs [--suite smoke]
        [--repeats 3] [--samples 1] [--max-cost 3.0] [--only id,id] [--no-upload]

The harness's simulated caller writes each line; Deepgram renders it to audio, which streams to
the entrant at real speed, 20 ms at a time, with silence between lines as a phone line has. The
rig marks the end of the caller's speech, waits for the agent's first sound above the
voice-activity threshold (config/models.yaml `audio_rig.vad`) and records the gap, then
transcribes the agent's audio so the caller model can answer what it heard. Code checks and the
grader score the call from the tool server's record, exactly as in the text harness
(evals/run.py `score`), and the results file has the same format, marked `transport: audio`.

Must-pass scenarios run `--repeats` times (three by default), because the decision rule asks for
every must-pass scenario to pass, with the safety lines word for word, on three repeats; the
report won't pick a winner from fewer. `--max-cost` stops the
run when its cost passes that many dollars, and the partial run is still written.
"""
import argparse
import asyncio
import importlib
import sys
import tempfile
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from app.tools import TOOLS
from evals import compare, results, suites
from evals.audio_rig.audio import FRAME_MS, Vad, frames, reply_delay_ms
from evals.audio_rig.line import Listener
from evals.audio_rig.router import Serving, Worlds, build_app
from evals.audio_rig.speech import Deepgram
from evals.caller import SimulatedCaller
from evals.checks import safety_config
from evals.models import Model, Usage, for_profile, load_config
from evals.results import RESULTS_DIR, ScenarioResult, Turn, TurnTiming
from evals.run import LOOP_REPEATS, build_run, score, upload
from evals.scenario import Scenario, load_all
from evals.world import World, resolve_caller, seeded_db

# The parts of a call's cost that are the stack's own; the rest is the rig's.
STACK_PARTS = ("platform", "platform_transcription", "brain_model")
ENTRANTS = ("livekit", "retell", "elevenlabs", "openai_realtime", "gemini_live")


def load_entrant(name: str):
    """`voice.<name>.rig_adapter`, loaded by name so shared code never imports a voice layer."""
    if name not in ENTRANTS:
        raise ValueError(f"unknown entrant {name!r}; known: {', '.join(ENTRANTS)}")
    return importlib.import_module(f"voice.{name}.rig_adapter")


@dataclass
class RigContext:
    """What an adapter gets from the rig: the local router its own processes reach scratch worlds
    through, the model config and the environment."""

    worlds: Worlds
    router_url: str
    config: dict
    env: dict


class Sender:
    """The caller's microphone: one frame every 20 ms, speech when there is some, silence
    otherwise. A frame is sent once its 20 ms have passed, as a live stream would deliver it."""

    def __init__(self, send, rate: int, clock=time.perf_counter, sleep=asyncio.sleep, queued=None):
        self.send = send
        # Seconds of sent audio still buffered on our side before it goes out, for a line that
        # buffers (a LiveKit audio source); the caller's words reach the entrant that much later.
        self.queued = queued or (lambda: 0.0)
        self.rate = rate
        self.clock = clock
        self.sleep = sleep
        self.queue: deque[bytes] = deque()
        self.silence = b"\0" * (rate * FRAME_MS // 1000 * 2)
        self.stopped = False
        self._spoken: asyncio.Future | None = None
        self._started: float | None = None

    async def run(self) -> None:
        t0 = self.clock()
        k = 0
        while not self.stopped:
            k += 1
            wait = t0 + k * FRAME_MS / 1000 - self.clock()
            if wait > 0:
                await self.sleep(wait)
            speech = bool(self.queue)
            frame = self.queue.popleft() if speech else self.silence
            try:
                await self.send(frame)
            except Exception:  # noqa: BLE001 - the line dropped; the rig sees the hang-up
                self.stopped = True
            now = self.clock() + self.queued()
            if speech and self._started is None:
                self._started = now
            if speech and not self.queue and self._spoken and not self._spoken.done():
                self._spoken.set_result((self._started, now))
        if self._spoken and not self._spoken.done():
            self._spoken.set_result((self._started or self.clock(), self.clock()))

    async def speak(self, pcm: bytes, on_start=None) -> tuple[float, float]:
        """Queue a line and wait for its last frame to go out. Returns when the caller started and
        stopped speaking. `on_start` runs just before the first frame is queued."""
        if self.stopped:
            # The line dropped (the agent hung up) after the caller decided to speak.
            now = self.clock()
            return now, now
        self._spoken = asyncio.get_running_loop().create_future()
        self._started = None
        if on_start:
            on_start()
        self.queue.extend(frames(pcm, self.rate))
        if not self.queue:
            now = self.clock()
            return now, now
        return await self._spoken


def tool_count(trace: dict) -> int:
    """Model tool calls on the record so far; the server logs its own steps there too."""
    return sum(1 for e in trace["events"] if e["type"] == "tool" and e["tool"] in TOOLS)


def newest_agent_timings(trace: dict, seen: int) -> tuple[dict, int]:
    """Stage timings the voice program logged with its newest agent line, if it logged a new
    one since `seen` lines, and the number of agent lines now on the record."""
    lines = [e for e in trace["events"] if e["type"] == "turn" and e["speaker"] == "agent"]
    if len(lines) <= seen:
        return {}, len(lines)
    return lines[-1].get("timings") or {}, len(lines)


def must_repeat(scenario: Scenario) -> bool:
    """The decision rule's three repeats cover every must-pass scenario."""
    return scenario.must_pass


class Rig:
    def __init__(self, entrant, ctx: RigContext, workdir: Path, caller_model: Model,
                 grader: Model, verbose: bool = True):
        self.entrant = entrant
        self.ctx = ctx
        self.workdir = workdir
        self.seed = seeded_db(workdir / "seed")
        self.safety = safety_config()
        self.caller_model = caller_model
        self.grader = grader
        rig = ctx.config["audio_rig"]
        self.vad = Vad(**rig["vad"])
        self.rig_config = rig
        self.verbose = verbose

    def log(self, call_id: str, text: str) -> None:
        if self.verbose:
            print(f"  {call_id[-8:]} {text}", flush=True)

    async def call(self, scenario: Scenario, sample: int = 1) -> ScenarioResult:
        call_id = f"rig-{scenario.id}-{sample}-{uuid.uuid4().hex[:8]}"
        began = time.perf_counter()
        transcript: list[Turn] = []
        timings: list[TurnTiming] = []
        usage = Usage()
        error, server, silent = "", None, 0
        speech = Deepgram(self.rig_config, self.ctx.env.get("DEEPGRAM_API_KEY"))
        line = self.entrant.line()
        listener = Listener(self.vad, rate=self.entrant.in_rate)
        sender = Sender(line.send, self.entrant.in_rate, queued=getattr(line, "queued_s", None))
        sending = None
        caller = SimulatedCaller(scenario, self.caller_model)
        platform: dict[str, float] = {}
        try:
            caller_id = resolve_caller(scenario.caller_id, self.seed[1])
            world = None
            if self.entrant.local_world:
                world = self.ctx.worlds.add(
                    call_id, World(self.workdir / call_id, self.seed, scenario.start_time)
                )
            server = await line.open(call_id, caller_id, world, listener)
            sending = asyncio.create_task(sender.run())
            since = time.perf_counter()
            heard_turn = await listener.turn(self.entrant.signals_done, since)
            greeting = await speech.hear(heard_turn.pcm, heard_turn.rate)
            self.log(call_id, f"agent: {greeting}")
            transcript.append(Turn(role="agent", text=greeting,
                                   said=" ".join(heard_turn.said) or None))
            trace = await asyncio.to_thread(server.trace, call_id)
            tools_so_far = tool_count(trace)
            _, agent_lines = newest_agent_timings(trace, 0)
            said = caller.first_line(greeting)
            recent: list[str] = []
            reason = None
            for _ in range(scenario.max_turns):
                if listener.is_hung_up:
                    reason = "agent_end"
                    break
                pcm = self.vad.trim(await speech.say(said, self.entrant.in_rate),
                                    self.entrant.in_rate)
                transcript.append(Turn(role="caller", text=said))
                self.log(call_id, f"caller: {said}")
                start, end = await sender.speak(pcm, on_start=listener.new_turn)
                if sender.stopped and listener.is_hung_up:
                    reason = "agent_end"
                    break
                heard_turn = await listener.turn(self.entrant.signals_done, end)
                heard = await speech.hear(heard_turn.pcm, heard_turn.rate)
                trace = await asyncio.to_thread(server.trace, call_id)
                lookup = tool_count(trace) > tools_so_far
                tools_so_far = tool_count(trace)
                stages, agent_lines = newest_agent_timings(trace, agent_lines)
                if heard_turn.first_sound is None:
                    silent += 1
                else:
                    timings.append(TurnTiming(
                        kind="lookup" if lookup else "plain",
                        reply_delay_ms=reply_delay_ms(end, heard_turn.first_sound),
                        # The rig times the sound; the model's first token is the voice
                        # program's, when it logs one.
                        model_first_token_ms=stages.get("llm_first_token_ms") or 0,
                        platform_reply_ms=stages.get("reply_ms"),
                        caller_speech_ms=(end - start) * 1000,
                        agent_speech_ms=len(heard_turn.pcm) / 2 / heard_turn.rate * 1000,
                    ))
                delay = (f"{reply_delay_ms(end, heard_turn.first_sound):.0f} ms"
                         if heard_turn.first_sound is not None else "silent")
                self.log(call_id, f"agent ({delay}{', lookup' if lookup else ''}): {heard}")
                if heard or heard_turn.said:
                    transcript.append(Turn(role="agent", text=heard,
                                           said=" ".join(heard_turn.said) or None))
                if heard_turn.hung_up:
                    reason = "agent_end"
                    break
                next_line = await asyncio.to_thread(caller.reply, heard, said)
                if next_line is None:
                    reason = "caller_hangup"
                    break
                said = next_line
                recent = (recent + [said.strip().lower()])[-LOOP_REPEATS:]
                if len(recent) == LOOP_REPEATS and len(set(recent)) == 1:
                    error = "the simulated caller repeated the same line three times"
                    break
            else:
                error = f"the call did not finish within {scenario.max_turns} caller turns"
            sender.stopped = True
            await line.close(reason or "caller_hangup")
            usage.add(caller.usage)
        except Exception as err:  # noqa: BLE001 - a broken rig run is an error, not a fail
            error = f"harness: {type(err).__name__}: {err}"
            sender.stopped = True
            try:
                await line.close("error")
            except Exception as close_err:  # noqa: BLE001
                print(f"{call_id}: closing the line failed: {close_err}", flush=True)
        if sending:
            await asyncio.gather(sending, return_exceptions=True)
        try:
            platform = await line.cost()
        except Exception as err:  # noqa: BLE001
            print(f"{call_id}: platform cost unavailable: {err}", flush=True)
        parts = {**speech.cost(), **platform, "caller_model": caller.usage.cost_usd}
        await speech.aclose()
        usage.add(Usage(cost_usd=sum(v for k, v in parts.items() if k != "caller_model")))
        result = await asyncio.to_thread(
            score, scenario, sample, call_id, server, transcript, timings, usage, error, began,
            safety=self.safety, grader=self.grader, final=self.entrant.final_record,
            agent_cost=sum(v for k, v in parts.items() if k in STACK_PARTS), silent_turns=silent,
        )
        parts["grader"] = max(0.0, result.cost_usd - sum(parts.values()))
        result.cost_parts = {k: round(v, 6) for k, v in parts.items()}
        self.ctx.worlds.drop(call_id)
        return result


def plan(chosen: list[Scenario], repeats: int, samples: int = 1) -> list[tuple[Scenario, int]]:
    """`repeats` runs of each must-pass scenario, `samples` of every other one."""
    return [(s, n) for s in chosen
            for n in range(1, (repeats if must_repeat(s) else samples) + 1)]


async def run_entrant(name: str, chosen: list[Scenario], repeats: int, max_cost: float | None,
                      env: dict, workdir: Path, samples: int = 1) -> tuple[list[ScenarioResult], object]:
    config = load_config()
    worlds = Worlds()
    with Serving(build_app(worlds)) as router:
        ctx = RigContext(worlds, router.url, config, env)
        entrant = load_entrant(name).entrant(ctx)
        rig = Rig(entrant, ctx, workdir, for_profile("offline.caller"),
                  for_profile("offline.grader"))
        done: list[ScenarioResult] = []
        await entrant.start()
        try:
            for scenario, sample in plan(chosen, repeats, samples):
                try:
                    result = await rig.call(scenario, sample)
                except (KeyboardInterrupt, asyncio.CancelledError):
                    print("stopped: the finished calls are still written", flush=True)
                    break
                done.append(result)
                spent = sum(r.cost_usd for r in done)
                delays = ", ".join(f"{t.reply_delay_ms:.0f}" for t in result.turn_timings)
                why = result.error or "; ".join(result.check_failures)
                print(f"{result.key}: {result.outcome} ${result.cost_usd:.3f} "
                      f"(run ${spent:.2f}) delays ms [{delays}] {why}", flush=True)
                if max_cost is not None and spent > max_cost:
                    print(f"stopping: cost ${spent:.2f} passed the cap ${max_cost:.2f}", flush=True)
                    break
        finally:
            await entrant.stop()
    return done, entrant


def stop_on_term(*_):
    # A run stopped with kill still runs its cleanup: the worker, test agents, the tunnel.
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    import os
    import signal

    signal.signal(signal.SIGTERM, stop_on_term)

    parser = argparse.ArgumentParser(description="Run the simulated audio bake-off.")
    parser.add_argument("--stack", required=True, choices=ENTRANTS)
    parser.add_argument("--suite", default="smoke")
    parser.add_argument("--only", default="", help="scenario ids, comma separated")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--samples", type=int, default=1, help="runs of every other scenario")
    parser.add_argument("--max-cost", type=float, default=None)
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--results-dir", default=str(RESULTS_DIR))
    args = parser.parse_args(argv)

    chosen, _ = suites.select(load_all(), args.suite)
    if args.only:
        wanted = set(suites.parse_tags(args.only))
        chosen = [s for s in chosen if s.id in wanted]
    with tempfile.TemporaryDirectory(prefix="summit-rig-") as tmp:
        done, entrant = asyncio.run(
            run_entrant(args.stack, chosen, args.repeats, args.max_cost, dict(os.environ),
                        Path(tmp), args.samples)
        )
    if not done:
        print("no scenario finished", file=sys.stderr)
        return 1
    suite = args.suite if not args.only else "custom"
    run = build_run(suite, [], entrant.name, entrant.profile, done, transport="audio")
    if getattr(entrant, "model", None):
        run.model = entrant.model
    path = results.save(run, Path(args.results_dir))
    print("\n".join(compare.report(run, compare.previous(run, Path(args.results_dir)))))
    print(f"results: {path}")
    if not args.no_upload:
        upload(run, path)
    return 1 if any(s.must_pass_failures for s in run.scenarios) else 0
