# Retell, native

A second Retell entry for the single live calls per stack, built the way Retell intends: Retell's own prompt-plus-knowledge-base split, its Agent Handbook, its End Call and Call Transfer, and our tool server behind a short list of functions. The baseline agent (`voice/retell/`, on (678) 336-6730) is untouched. Agent "Summit Air (Retell native)"; `scripts/route_number.py retell_native` puts it on (646) 624-0418.

```bash
RETELL_BRIDGE_URL=https://45-77-105-247.sslip.io TRANSFER_OFFICE_NUMBER=+1... \
  uv run --env-file .env python -m voice.retell_native.push --eval-url https://45-77-105-247.sslip.io/eval
uv run --env-file .env python -m voice.retell_native.review_chats
```

Every setting below is sent through Retell's API (`push.py`); nothing needs the dashboard. A second push sends nothing, which is how to check that Retell kept each field as sent.

| Part | Setting | Why |
|---|---|---|
| Prompt | `prompts/stripped.md` with the tenant's safety lines and sign-off word for word, a short native section, then `{{call_context}}` | One page. The bridge's inbound webhook fills `call_context` with the long prompt's "This call" part (the lookup before hello), so the agent knows the caller without the rest of the long prompt. |
| Knowledge base | The tenant's equipment, FAQ and membership files, plus hours, the five boroughs, payment and visit types from `config.yaml` | Facts a caller asks about, kept out of the prompt. Named by a hash of its content, so an edit makes a new base. The FAQ's areas and hours lines are left out: config is what the tool server enforces, and the areas line still lists three boroughs. |
| Functions | `find_customer`, `get_customer_history`, `check_service_area`, `find_address`, `read_back`, `find_slots`, `book_appointment`, `reschedule_appointment`, `cancel_appointment`, `record_move`, `flag_person`, `record_lead` | Lookups, the read-back, bookings and changes, a callback and a lead, each with when to call it and when not. The lookups, booking and changes say a short line while they run. `get_customer_history` and `record_move` are there because the stripped prompt names them. |
| End Call | Retell's built-in, nothing said during it | It waits for the speech to finish, so the hang-up is Retell's, not the bridge's timer. The server's `end_call` gate is not asked; the prompt keeps the safety sign-off rule. |
| Call Transfer | Retell's built-in cold transfer to `TRANSFER_OFFICE_NUMBER` | Only when the caller asks for a person during office hours. The server's warm-transfer decision (app/transfer.py) is not used. |
| Agent Handbook | conversational personality, natural fillers, high empathy, echo verification, speech normalization, smart matching, AI disclosure, scope boundaries; NATO spelling and the professional preset off | NATO spelling on every street name would sound robotic; the conversational preset replaces the professional one. Voice only: Retell's chat agents ignore it, so the review chats can't show it. |
| Listening | `voice/retell/build.py` SPEECH: accurate transcription, responsiveness 1.0 without dynamic, backchannel on, reminders after 6 s, Max | The settings tested after live call 3 (`voice/retell/turn_probe.py`). |
| Names | The tenant's pronunciation dictionary (Houston Street as HOW-stun) and keyterms, plus the five boroughs and "tune-up" | `tenants/summit-air/names.yaml`. |
| Start | No delay before the greeting, contact memory off, no post-call extraction | Each slate call starts clean. |

The review chats run on a twin chat agent whose functions go to the eval bridge, so they never touch the live schedule.
