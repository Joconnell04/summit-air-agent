"""Mirror one results file into LangSmith so bake-offs can be compared side by side there.

Viewer only. The results JSON stays the source of truth, nothing here runs during a call, and with
no LANGSMITH_API_KEY set this does nothing. Usage: python -m evals.langsmith_sync <results.json>
"""
import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from evals.results import ResultsError, Run, ScenarioResult, load

DATASET_NAME = "summit-air-scenarios"

# Fixed, so the same scenario or run maps to the same LangSmith id on every sync.
NAMESPACE = uuid.UUID("5a1e5a1e-0000-4000-8000-5a1e5a1e0001")


def experiment_name(run: Run) -> str:
    return f"{run.stack}-{run.profile}-{run.suite}-{run.run_id}"


def example_id(scenario_id: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"example/{scenario_id}")


def run_id_for(run: Run, scenario_id: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"run/{experiment_name(run)}/{scenario_id}")


def feedback_id_for(rid: uuid.UUID, key: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{rid}/{key}")


def example_inputs(result: ScenarioResult) -> dict[str, Any]:
    tags = [f"flow:{f}" for f in result.flow]
    tags.append(f"difficulty:{result.difficulty}")
    tags += [f"trait:{t}" for t in result.traits]
    return {"persona": result.persona, "tags": tags, "difficulty": result.difficulty}


def feedback_for(result: ScenarioResult) -> dict[str, float]:
    # An error is not a fail, so it gets its own score and no "passed" score: filtering on
    # passed = 0 then finds real failures only.
    if result.outcome == "error":
        verdict = {"errored": 1}
    else:
        verdict = {"passed": int(result.outcome == "pass")}
    return {
        **verdict,
        "must_pass_failed": int(bool(result.must_pass_failures)),
        "grader_score": result.grader_score,
        "reply_delay_p50_ms": result.reply_delay_p50_ms,
        "reply_delay_p90_ms": result.reply_delay_p90_ms,
        "cost_usd": result.cost_usd,
    }


def experiment_metadata(run: Run) -> dict[str, Any]:
    return {
        "stack": run.stack,
        "profile": run.profile,
        "suite": run.suite,
        "hash": run.hash,
        "run_id": run.run_id,
    }


def upsert_dataset(client: Any, run: Run) -> Any:
    if client.has_dataset(dataset_name=DATASET_NAME):
        dataset = client.read_dataset(dataset_name=DATASET_NAME)
    else:
        dataset = client.create_dataset(
            DATASET_NAME, description="Eval scenarios for the Summit Air inbound agent"
        )
    existing = {str(e.id): e for e in client.list_examples(dataset_id=dataset.id)}
    for result in run.scenarios:
        key = str(example_id(result.scenario_id))
        inputs = example_inputs(result)
        if key not in existing:
            client.create_example(
                inputs=inputs,
                dataset_id=dataset.id,
                example_id=key,
                metadata={"scenario_id": result.scenario_id},
            )
        elif existing[key].inputs != inputs:
            client.update_example(key, inputs=inputs)
    return dataset


def upload_experiment(client: Any, run: Run, dataset: Any) -> str:
    name = experiment_name(run)
    if client.has_project(name):
        project = client.read_project(project_name=name)
    else:
        project = client.create_project(
            name, reference_dataset_id=dataset.id, metadata=experiment_metadata(run)
        )
    done = existing_run_ids(client, project.id)
    for result in run.scenarios:
        rid = run_id_for(run, result.scenario_id)
        if str(rid) not in done:
            create_scenario_run(client, run, result, rid, name)
    # Runs are sent in the background; feedback needs them to exist first.
    client.flush()
    # Feedback ids are fixed, so posting every score on every sync fills in whatever a crashed
    # earlier sync missed and replaces, rather than duplicates, what is already there.
    for result in run.scenarios:
        rid = run_id_for(run, result.scenario_id)
        for key, score in feedback_for(result).items():
            client.create_feedback(
                rid, key, score=score, session_id=project.id, feedback_id=feedback_id_for(rid, key)
            )
    return name


def existing_run_ids(client: Any, project_id: Any) -> set[str]:
    """Ids of the root runs already in the experiment, so an earlier partial sync is completed."""

    async def collect() -> set[str]:
        found = set()
        # min_start_time is explicit because the default window is one day, which would hide
        # runs from an earlier day and make every later sync create them again.
        async for r in client.runs.query_v2(
            project_ids=[str(project_id)],
            is_root=True,
            selects=["ID"],
            min_start_time=datetime(2000, 1, 1, tzinfo=UTC),
        ):
            found.add(str(r.id))
        return found

    return asyncio.run(collect())


def create_scenario_run(
    client: Any, run: Run, result: ScenarioResult, rid: uuid.UUID, project_name: str
) -> None:
    timing = {}
    if result.duration_ms is not None:
        timing = {
            "start_time": run.created,
            "end_time": run.created + timedelta(milliseconds=result.duration_ms),
        }
    client.create_run(
        id=rid,
        name=result.scenario_id,
        run_type="chain",
        project_name=project_name,
        reference_example_id=example_id(result.scenario_id),
        inputs=example_inputs(result),
        outputs={
            "outcome": result.outcome,
            "must_pass_failures": result.must_pass_failures,
            "transcript": [t.model_dump() for t in result.transcript],
            "tool_calls": [c.model_dump() for c in result.tool_calls],
        },
        extra={"metadata": {**experiment_metadata(run), "call_id": result.call_id}},
        tags=[f"stack:{run.stack}", f"profile:{run.profile}", f"suite:{run.suite}"],
        **timing,
    )


def connect() -> Any:
    """A LangSmith client, or None (after one line of output) when no key is set."""
    if not os.environ.get("LANGSMITH_API_KEY"):
        print("LangSmith sync skipped: LANGSMITH_API_KEY is not set")
        return None
    from langsmith import Client

    return Client()


def sync(run: Run, client: Any = None) -> str | None:
    """Return the experiment name, or None when LangSmith is off."""
    client = client or connect()
    if client is None:
        return None
    name = upload_experiment(client, run, upsert_dataset(client, run))
    print(f"LangSmith experiment {name} is up to date ({len(run.scenarios)} scenarios)")
    return name


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m evals.langsmith_sync <results.json>", file=sys.stderr)
        return 2
    client = connect()
    if client is None:
        return 0
    try:
        run = load(argv[1])
    except ResultsError as err:
        print(err, file=sys.stderr)
        return 1
    sync(run, client)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
