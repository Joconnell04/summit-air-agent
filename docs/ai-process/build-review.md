# Build review: what needs Jackson

Everything the six build lanes left open, in one place: questions to answer, things only a person can do, and things worth a look. Gathered from every lane's pull request, the decision readouts, the eval lane's smoke-run notes and the open issues. Written at the end of phase 2 (docs/ai-process/phases.md).

## Where things stand

All six lanes are merged to main and `make check` passes with 575 tests. Real phone calls booked jobs on both numbers: (646) 624-0418 on our LiveKit stack and the 678 number on Retell. A gas call on Retell held the safety gate. Phase 2 has one box left, the 10-call live go/no-go (#9). The design trail is up to date (docs/ai-process/design-trail.md).

## 1. Questions to answer (each has a readout)

Answer in chat; each takes a sentence. None blocks the other work.

| # | Question | Recommendation | Readout |
|---|---|---|---|
| #26 | How does a callback a person returned get marked done? The owner view is read-only, so today every person-owed callback shows overdue. | **B.** A background job reads ServiceTitan's call log and marks the callback done when the office called that number back. Falls back to a Done button if the office calls from cell phones. | docs/decisions/26-callback-done.md |
| #27 | What pass rate should block a merge? | **C.** Run the standard suite once on the real prompts and set the floor at that pass rate rounded down to the nearest 5%; it can only go up. Safety stays protected by the must-pass rule regardless. | docs/decisions/27-eval-pass-rate-floor.md |
| #32 | Should the Retell side of the bake-off use Retell's paid faster tier and backup model? | **C.** The 10 bake-off calls on the same setup as our stack, plus about 3 with the extras on, reported apart. Answers "did you give Retell a fair shot?" | docs/decisions/32-retell-speed-extras.md |

Choosing C on #27 means one standard-suite run, about $0.60 for 30 scenarios (ASSUMED from the smoke runs: 8 scenarios cost $0.14 to $0.15, MEASURED).

## 2. Things only you can do

1. **Make a Healthchecks.io check** (free plan) and put its ping URL in the Notion vault as `HEALTHCHECK_URL`. The server's watchdog already pings it when set; without it, nobody gets an email or text if the server goes down. I'll copy it onto the server from the vault.
2. **The 10 live calls (#9).** Call the 646 number yourself: speakerphone, background noise, a slow speaker, interruptions. Pass means every booking or flag is right, no dropped or doubled turns, and replies come in about 1.5 s or faster. Best done after the reply-delay fix (section 4), or the delay alone will fail it.
3. **The bake-off calls** (docs/bakeoffs.md). The same kind of calls on the Retell number, about 10, plus the few with extras if you pick C on #32. The 10 go/no-go calls on 646 can double as the LiveKit half, so the whole thing is about 20 to 23 calls.

## 3. Worth a look (no answer needed unless something seems wrong)

- **Retell can't say the safety lines word for word.** Retell's model speaks them itself, so it paraphrased them (", and call 911" became ". Call 911") and once made up a callback time. Our server still refused to book or hang up until the exact lines were said, so the gate held, but the caller heard something other than the signed-off script. On our stack the server's text is spoken as given. This is a real finding for the bake-off write-up. Source: voice/retell/README.md, "Safety lines are model-spoken".
- **Judgment calls the tool-server lane made**, all marked ASSUMED in tenant config:
  - No heat on its own is High priority; the rules raise it to Urgent (an older person, a baby, medical equipment, under 55 F inside).
  - A caller can cancel by phone only while the job is still Scheduled; once a technician is dispatched, a person handles it.
  - The "this is a business" check only matches wording that can't describe a home, so "my tenants" or "our building" is left to the agent and the account type.
- **ServiceTitan field names are mostly guesses.** The developer portal couldn't be read and there's no sandbox account, so servicetitan_mock/README.md marks what's sourced, what's from third-party docs, and what's ASSUMED. The mock is also stricter than the real thing: real ServiceTitan can overbook; ours refuses.
- **Known limits of the tool server** (app/README.md, "Known limits"). Short list, worth knowing before the interview: it assumes one server process, one owner-view password covers every tenant, and a ZIP the caller only asks about still counts toward out-of-area demand.
- **LangSmith has test data** from the viewer lane: experiment `livekit-fast-demo-demo-1` and two examples starting `demo-`. Safe to delete in LangSmith; I've left them.

## 4. Fixes I'm making next (say so if you'd rather I didn't)

In order. Each comes with a test, and nothing waits on your answers above.

1. **Reply delay (#30).** 2.7 s against a 1.2 s target; nearly all of it is the model's first word on each turn. Checks: whether the prompt cache is being missed, Haiku 4.5 (`fast-prev`) for comparison, and whether the server notes added to caller lines restart the early reply. Small paid test calls only.
2. **"I'm 84" isn't read as an older person.** The age check only matches "84 years old" or "84-year-old", so the eval's elderly no-heat caller was booked High instead of Urgent (spec S-21, a must-get-right rule).
3. **Out-of-area known callers aren't always counted.** When the caller's address on file is outside the area, the agent sometimes declines without checking the ZIP, so it isn't logged. The server should record it at the start of the call instead of trusting the agent to (spec S-28).
4. **First eval run on the real prompts.** Both smoke runs used a stand-in prompt, before the voice lane's prompts landed. The real prompts already cover the other smoke findings (callback times only from tools, commercial leads saved without asking, no questions after goodbye); this run checks they work.
5. **Retell eval runs need a clean mock each time.** Its eval copy of the mock keeps bookings from earlier runs; add a reset.
6. **A shared eval file imports Retell code** (`evals/retell_chat.py` imports `voice.retell`), which docs/architecture.md rules out for shared code. Move the import behind the stack boundary and test the boundary for both stacks.
7. **Outbound calls have never dialed for real.** The "we found an earlier slot" call is tested up to the dial; the dial and the voicemail branch need one real call.

## 5. Housekeeping (say yes and I'll do it)

- Delete the eight merged `build/*` and `followup-evals` branches on GitHub, and the four leftover local worktrees (all clean, nothing unmerged).
- Archive the two finished linked sessions (the LiveKit lane and the fork that answered "what phase are we in").

## 6. Saved for later (walked before V1 is done)

Nine items in docs/later.md, unchanged by the build: Spanish, a side model, the model bake-off, per-call playbooks, warm transfer, scaling past 3 calls at once, confirmation texts, revisiting the delay targets, and swapping ASSUMED latency numbers for measured ones.
