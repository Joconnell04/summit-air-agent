# Server lane handover

The server lane (tool server and mock ServiceTitan fixes, on `joco/stripped`) ends here. The socket lane carries everything below from now on. The branch's local `make check` is the gate; GitHub Actions doesn't start jobs right now (an account billing block, not a code failure).

## Where things live

| What | Code | Tests | Decision |
|---|---|---|---|
| Emergency dispatch | `app/tools.py`: `_dispatch`, `_dispatch_said`, `_alert_on_call`, `_replace_dispatch`, `_after_dispatch`; hooks in `read_back` (new `job_type` argument) and `find_slots`; table `dispatches` in `app/schema.sql`; lines `dispatch_*` in `tenants/summit-air/agent.yaml` | `tests/tool_server/test_ts_dispatch.py` | DECISIONS.md 123 |
| Call labels (Urgent, Routine, No visit) | `app/labels.py`; marked in `find_slots` and `book_appointment`, stored at call end in `app/lifecycle.py` (`end`); table `call_labels`; owner view in `app/owner.py` (`call_label`) and `app/templates/_ui.html` (`label_tag`), `calls.html` (Urgent filter), `call.html` | `tests/tool_server/test_ts_labels.py` | DECISIONS.md 122 |
| Job tags in the CRM | `app/booking.py` (`_tags`), `fsm.label_tag_ids` in the tenant's `agent.yaml`, tag types 804 and 805 in `config.yaml`; the mock stores them in `job_tags` (`servicetitan_mock/schema_added.sql`, `routes/jpm.py`) | `tests/servicetitan_mock/test_st_booking.py` | 122 |
| Capacity cache | `app/fsm/cache.py` (`SlotCache`: ttl, idle drop, background refetch); `warm_capacity` in `app/tools.py`, run at `/calls/start` (`app/main.py`) and by the `cache_warmed` job (`app/jobs.py`); `SLOT_CACHE_SECONDS` in `app/settings.py`; the mock's `Snapshot` in `servicetitan_mock/capacity.py`; `scripts/bench_capacity.py` | `test_ts_capacity_cache.py`, `test_st_capacity_snapshot.py` | |
| Failure grader | `app/failure_grader.py`, `app/checks_view.py`, prompt `prompts/evals/grader.md` (built in the grader lane; this lane only took its merge) | `test_ts_failure_grader.py` | DECISIONS.md 120 |
| Caller-ID line in the prompt | `prompts/assemble.py` (`caller_id_note`, called from `call_notes`) | `tests/voice/test_voice_prompts.py` | |
| Catch-all answer on the job | `_catch_all_answer` in `app/tools.py`, `visit_questions.catch_all_cue` in the tenant, `details.also_noted`, "Caller also noted" in `app/booking.py` (`job_summary`) | `test_ts_booking_flow.py` | |
| Read-back connector | `_connector` in `app/tools.py`, `connectors` in the tenant | `test_ts_dispatch.py` | |

## Open threads

1. **The prompt line for dispatch.** The agent passes `job_type` to `read_back` only once the prompt says so. Without it, read_back reads back as before and nothing dispatches, except through `find_slots` after a read-back that came in below High.
2. **"Thanks for calling." before a live dispatch sentence.** On the coordinator's scripted after-hours call on 339db4c, the dispatch sentence was heard after "Thanks for calling.". The server's connectors are only "Got it.", "Perfect." and "Okay.", and a test pins that, so the words came from the model or the voice layer. Check the call's tool row (`tool_calls.say` for `read_back`) against what was spoken.
3. **Socket mode onto `joco/stripped`.** Bring it in after this push. Dispatch and labels make no assumption about the hosted Retell LLM. Socket mode must speak `read_back`'s `say` word for word, connector included, and follow `next` after a dispatch.
4. **C-82 in `docs/later.md`:** a grader case for a booking left at Normal after the caller named an older person.
5. **lock_ms on `/calls/start`.** The timing is logged, but no live calls had come in to read it when last checked.
6. **DECISIONS numbering.** Main has 121 (socket build) and this branch has 120 (daily grader), 122 (labels) and 123 (dispatch). 120 was reserved for the side-by-side lane's arm write-up, so that write-up needs the next free number.

## Known gaps

- A dispatch for a caller with no name and no account books under "Caller ending 1234". The office fixes the name.
- A unit or number corrected after a dispatch cancels the job and books a new one. If the cancel fails, a High person flag asks the office to cancel it.
- No-visit calls have no job, so their label is only in the tool server's `call_labels`, not in the mock CRM. Inbound calls aren't logged in the mock.
- The access answer after a dispatch reaches the job only through `add_note` or the technician summary. The server doesn't parse it.
- The connector is not said when the caller asked for the address slower (read in parts).
