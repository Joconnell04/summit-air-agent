# Demo 2: gas and carbon monoxide

Video: [videos/02-gas-and-co.mp4](videos/02-gas-and-co.mp4), about 1 min 45 s. Data: the seeded demo day.

**What this proves.** A gas or carbon monoxide mention locks booking until the company's signed-off safety lines are said word for word; a person is flagged; and nobody can edit those lines from a screen.

## 1. "What happens if someone calls and smells gas?"

- **Click.** Today, Needs attention: the 8:20 AM Gas smell row (Urgent). Open it.
- **Point at.** The red step under the caller ("Heard gas smell. Booking is locked until the safety lines are said."), the agent's line, the green "Said the signed-off safety lines, word for word", then What happened: nothing booked.
- **What it proves.** The server's turn check hears the hazard and returns the exact lines; booking and hanging up are refused until the agent's words match them (`app/safety.py`, the safety rows on Rules).
- **Talk track.** "The second they said gas, the system stopped trying to book. It told them to get out, not to touch switches, and to call 911 or the gas company, in the exact words you sign off on. Then it flagged a person to follow up."

## 2. "The fire department cleared it. Can they still get a tech?"

- **Click.** Calls, the 4:30 PM carbon monoxide call.
- **Point at.** The safety lines first, then the caller saying the fire department cleared it, then the last step "logged as a safety override", and Job booked: Safety check, Urgent.
- **What it proves.** Once the lines are said, a caller who insists can book a safety check; it goes in Urgent and is logged as an override (`safety_override` on the booking).
- **Talk track.** "We never argue with someone who's already safe. Lines first, always. If they still want a tech, it books a safety check, Urgent, and it's marked, so you can pull every call where someone pushed past the script."

## 3. "Can the AI skip the safety lines, or change them?"

- **Click.** Bench, Rules, Fixed responses, Safety tab: "Gas smell on the call" and "Carbon monoxide on the call".
- **Run, if asked.** `uv run pytest -v tests/tool_server/test_ts_safety.py`. Name `test_booking_tools_wait_for_the_lines_then_an_insisting_caller_can_book` and `test_gas_alone_a_gas_bill_or_a_gas_furnace_triggers_nothing`.
- **What it proves.** The lines are fixed text in server config; the owner's editor can't reach them (brief 08); the tests run on every change.
- **Talk track.** "These are fixed responses. The agent says them word for word, and they're not on the list of things anyone can change from a screen. And 'my gas bill is huge' doesn't set it off, so normal calls stay normal."

## If Paul pushes

- "How exactly does Retell say your words?" On Retell the bridge hands the exact lines to the live call and the model speaks them; the server counts them as said only if the agent's line matches word for word, so booking and hang-up stay locked until it does (voice/retell/README.md, "Safety lines are model-spoken"). Be honest that the agent may start its normal reply a beat before the lines cut in; that's a measured gap on the list.
