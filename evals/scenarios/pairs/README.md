# Pairs

Two callers at the same time on one world (evals/README.md, "Two calls at once"). One YAML file per pair, named `<id>.yaml`; `evals/pairs.py` validates them and a test loads every file and checks its world.

| Field | Meaning |
|---|---|
| `id` | Same as the file name. The callers' results are `<id>-a` and `<id>-b` |
| `flow`, `difficulty`, `traits`, `journeys`, `spec`, `tags`, `time`, `max_turns` | As in a scenario file, shared by both callers |
| `world.job_type` | The job type whose technicians the setup counts |
| `world.fill` | Days to book up before the calls. Each window keeps its urgent hold free |
| `world.keep_open` | `{date, window, technicians}`: windows on those days left with that many technicians a booking below Urgent may take |
| `script` | Lines both simulated callers follow, after each caller's own `script` |
| `callers` | Two callers, each with the scenario fields that differ: `caller_id`, `persona`, `hidden_facts`, `opening`, and optionally `script`, `misheard` |
| `must` | Code checks each call must pass on its own (the checks in `evals/scenarios/README.md`) |
| `should` | What the grader looks for, per call |
| `together` | Checks on the two calls together, below |

## Together checks

| Check | Argument | Passes when |
|---|---|---|
| `one_job_per_technician_window` | `true` | read from the mock: no technician has two live jobs in one window on one day |
| `window_jobs` | `{date, window, count}` | that many of the two calls' jobs are in the window in the mock, each with its own technician |
| `booked_together` | `true` | both calls' first booking after a confirmed read-back reached the tool server at the same moment |
| `taken_refusals` | a number | that many calls were told by `book_appointment` that the window was just taken |

A non-urgent caller can't take a technician held for urgent jobs, but an urgent one can, so a pair about the last opening pins `priority` in its `booking` check: a call the server rates Urgent fails there, with the reason, instead of looking like a double booking.
