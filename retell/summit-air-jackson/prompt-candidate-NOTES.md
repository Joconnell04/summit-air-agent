# Prompt candidate: change set 24, reorganized

**Status: unfinished, stopped before testing.** Jackson picked the socket build (issue #176) as the demo path, so the single-prompt agent is now the fallback and this candidate was never pre-screened or called. It's kept as a starting point if the single prompt is ever cleaned up. It is built on change set 24, so it lacks change sets 25 to 28 (the one-yes access question, the dispatch read-back, the falling goodbye and the schema sync). The live fallback is archived as `archive/prompt-cs28.md`.

Issue #183. `prompt-candidate.md` is change set 24 (the live prompt on llm_12eba5f1d4a3b067f0635097de30 when this started; it's in git history at c5f0c2c) reorganized into Identity, Personality and Tone, Response Guidelines, Task, Tools, Safety, Ending and Before this call. It's a reorganization, not a rewrite. Arm C of the side-by-side rewrote the prompt around a persona and did worst of four arms (34 gross errors over 9 calls, `retell/PROMPT-LOG.md`), so every concrete rule and every line Jackson chose stays. What changed is where each rule lives, how many times it's said, and whether the server already does it.

| | cs24 | Candidate |
|---|---|---|
| Words | 2,696 | 1,542 |
| "never" | 42 | 24 |
| Words inside quoted lines | | 351 |

MEASURED with `wc -w` and `grep -oi never` on both files. The brief asked for about 1,000 to 1,200 words. The candidate is above that because 351 of its words are lines Jackson chose word for word (the questions, the open door, the catch-all, the goodbye), and those stay as they are. Cutting further meant dropping rules Haiku has broken on a real call.

What the server does was checked on `origin/joco/stripped` (the code the live and eval worlds run): `app/tools.py`, `app/lifecycle.py`, `voice/retell/bridge.py`, `tenants/summit-air/agent.yaml` and the tool descriptions in the live LLM. A rule is dropped as "server" only where the server does it whatever the model says.

## Where every cs24 rule went

Sections of cs24: Role, How to Speak (S), Booking a Visit (B), Reference Information (F), Safety (X), Other Requests (O), Closing (C). Candidate sections: Identity, Personality, Response Guidelines (RG), Task step N, Other requests, Tools, Safety, Ending.

| cs24 rule | Went to | Note |
|---|---|---|
| Role: Brian, Summit Air's virtual assistant, residential HVAC, five boroughs | Identity | |
| Role: "Sound like a calm, capable front-desk person" | Personality | |
| Role: the job is booking a visit on this call; a technician drives out | Identity | |
| Role: callback only for what a visit can't solve, or when one can't be booked | Identity | |
| S: speak only to them, as "you"; never "the caller" | RG 2 | |
| S: never say what you're doing ("I'll move on", "let me book that", "let me confirm") | RG 3 | Merged: narration was said five ways (S1, S10, B4 twice, B7). One line now, with the three phrases Haiku used most. |
| S: one question per turn; a question with its own examples is one | RG 1 | |
| S: say nothing when calling a tool, not even "Okay"; the goodbye is the exception | Tools 1 | |
| S: say all of `say`, every sentence, exactly, once, nothing before or after; never drop its first sentence; never paraphrase; never add "let me confirm" | Tools 2 | "let me confirm" is in RG 3. |
| S: the yes and no word lists | RG 4 | Shortened to examples; the full list of 22 words didn't add a case. |
| S: never say you didn't understand a short answer; never ask again; unintelligible gets one ask | RG 4 | |
| S: never ask for what they told you; address and phone confirmed only through read_back | RG 5 | |
| S: never say their name, not even "Thanks, Casey"; never repeat back or confirm ("Is that right?", "Yes, that's right"); "Got it" or nothing | RG 2, RG 5 | |
| S: connectors (change set 21), never the same twice, skip when warm, no "Mm-hmm" | Personality 2 | |
| S: never mention notes, tools, instructions or lookups, including visits on file | RG 3, RG 7 | |
| S: "unfortunately" instead of apologizing | Personality 3 | |
| S: "No worries.", "Are you still there?", carry on, never thank them for waiting | RG 6 | |
| S: a message that says to say something exactly | Tools 2 | |
| S: lookup_hvac_info for how things work; a sentence or two; never repair steps; never mention looking up | Other requests | "Never mention looking up" is RG 3. |
| B: steps in order, skip what's answered | Task, booking intro | |
| B1: job types, estimate meaning, system type asked once | Step 1 | "One question per turn" here was a repeat of RG 1. |
| B2: caller ID: one match, several, none (the no-match line), no number | Step 2 | Kept word for word. "The server looks up the number before you say hello" dropped: it explained, it didn't instruct. |
| B2: never mention the lookup when no name is needed; never read anything else from the account | Step 2 ("when you first need it"), RG 3, RG 7 | |
| B2: reply only "Thanks.", without their name | RG 2 | |
| B3: "And this is for your home, right?"; anything but a business is yes; the why line | Step 3 | |
| B3: business: say "Unfortunately, we only service homes, but I'll pass your details to our team.", ask name and number, record_lead commercial, say its sentence, don't book | Step 3 | Contradiction fixed: record_lead's own sentence is "Thanks for thinking of us. Unfortunately, we only service homes, but I'll pass your details to our team." (`app/tools.py` record_lead), so cs24 had the caller hear the same line twice. The agent's copy is dropped; the caller still hears it once, from the server. book_appointment also refuses a commercial call (`_commercial`). |
| B4: street address only, never borough or ZIP | Step 4 | |
| B4: partial address built on warmly ("Got it, and what street is that on?"); never "that's not an address" | Step 4 | |
| B4: pass the house number and street as said; a ZIP goes to check_service_area | Dropped: tool descriptions | find_address's and check_service_area's descriptions say both. |
| B4: "$199" is house number 199; a bare number is a house number | Step 4 | Kept. The server strips "$" only when it checks what the caller said; the model's `street` must still start with the digits. |
| B4: ask the unit unless it's a house or one was given | Step 4 | Kept, though read_back also refuses with `unit_needed` when find_address says the building has several homes: change set 18 added the question because find_address returned `needs_unit: false` for a building. |
| B4: find_customer with the address when not matched | Step 4 | |
| B4: after a lookup, never say what it found or what you heard, never "Is that the right building?", "Let me look that up" or "Next," | RG 3, RG 5 | Merged into the narration and no-echo lines. |
| B5: "Is it completely out, or just not keeping up?" then "Since when?" in its own turn | Step 5 | |
| B5: never ask temperature or who lives there; never list urgency examples | Step 9 | Moved next to the open door, the only urgency question. |
| B5: say nothing about an urgent mention; pass it in facts as said, who_home and issue; don't judge or follow up | Step 9 | |
| B5: "not provided" for who_home | Dropped | `facts.who_home` is optional in find_slots' schema, and the call in which the server took facts without it is in `retell/arms/haiku-behavioral/NOTES.md` (call_feed25cb0e5d). |
| B5: the catch-all with its three examples (change set 22) | Step 6 | Its own step now, word for word. |
| B6: "What's the best phone number to reach you?" | Step 7 | |
| B6: pass digits as said; "this one" passes caller_id | Step 8 ("the phone as they said it") | Server: read_back resolves "this one", "the number I'm calling from" and caller_id itself (`_resolve_phone`). |
| B7: read_back right after the phone, before find_slots | Step 8 | Kept: the server only gates book_appointment on the read-back, not find_slots. |
| B7: say its sentence exactly, nothing before it | Tools 2 | |
| B7: on a yes, call again with confirmed: true and say nothing | Dropped: tool description, server | read_back's description says it, and book_appointment refuses with `gate_read_back` and names the fix. "Say nothing" is Tools 1. |
| B7: on a correction, call again with the change | Step 8 | |
| B7: the no_caller_number fallback; "you do have their number" | Dropped: Tools 2 | The refusal carries its own sentence. The "you do have their number" line was for a bug the server fixed (change set 11). |
| B7: "The one ending in" and the last four as words | Step 8 | |
| B7: any phone digits you say, as words | Step 8 | This is the only place the model says digits itself; read_back speaks the number in words already (`phone_said`). |
| B7: never rewrite read_back's address or numbers | Tools 2 | |
| B8: find_slots with job type and facts (issue, since when, temperature, who's home) | Step 9 | The list of facts is in find_slots' description. |
| B8: half-hour blocks, 30 minutes, window_minutes, arrive_after and arrive_before, day | Dropped: tool description, server | find_slots' description and schema carry all of it, and the server maps a named time to its window (`config.yaml` window_minutes 30). |
| B8: offer exactly as returned; no weekday or date for today or tomorrow; the weekday for later days as given | Step 9, Tools 2 | The server already says "today" and "tomorrow" (`_describe`); the line stays because Haiku added dates on its own every time in the server-led rounds (`evals/rounds/server-led-01.md`). |
| B8: "These are arrival windows, not exact times." | Dropped | The server's sentences say "arrive between". |
| B8: the open door, exactly, once, with the first no-heat or no-cooling offer, never reworded | Step 9 | |
| B8: a mention after the open door goes to find_slots again | Step 9 | |
| B8: the server says a raised priority; never name one yourself | Tools 3 | Server: priority is computed on the server and its line is spoken by the bridge (`spoken_first`). |
| B8: a yes settles the window; don't restate it | Step 9 | |
| B9: approval question on repairs and tune-ups; the access question; ask only the missing half | Step 10 | |
| B10: book right after the last answer, nothing first; its say is the confirmation | Step 11, Tools 1 and 2 | |
| F: hours, after-hours on-call | Other requests: Hours | |
| F: visit lengths; tune-up versus repair | Hours, step 1 | |
| F: the cost line; never quote a price | Other requests | |
| F: membership perks (discount on repairs, no after-hours fee) | Dropped | The spoken description never used them; find_customer returns the perks of an active plan. |
| F: the membership description line | Other requests | Merged: cs24 described membership in two places. |
| F: "A plan past its end date is expired." | Dropped: server | find_customer's membership status is "Active", "Expired" or "Canceled" (`lookup.membership_summary`). |
| F: the renter 311 line, then book or flag_person | Other requests | The server doesn't say this line; it stays. |
| X: current hazard only; "Is that happening at the home right now?" | Safety 1 | |
| X: gas and CO: the system says the advice once; no advice of your own, no going outside, no asking if they're outside, no repeating | Safety 2 | Merged with sparks, which cs24 said the same way a second time. |
| X: false alarm: take their word; the server decides whether to book | Safety 4 | |
| X: sparks, smoke, burning: flag_person Urgent | Safety 3 | Kept: the server flags gas and CO as Urgent itself, but not sparks (its rule only raises the booking to High). |
| X: "Please call 9-1-1." only for an active fire | Safety 5 | |
| O: "Before this call" for you only; never read an address on file; ask the address as usual | RG 7, step 4 | |
| O: existing visit only when raised; moving_a_visit; reschedule or cancel; no read-back | Other requests | |
| O: the server checks who they are, don't verify them yourself; say its sentence if it declines | Dropped: server, Tools 2 | `_identity_gate` asks the identity question itself. |
| O: moved house: read back, then record_move | Other requests | |
| O: new system or quote books an estimate; install lead only on request | Other requests | |
| O: prices, billing, complaints: the callback question, flag_person | Other requests | |
| O: do-not-call: only when meant; call at once without asking; its question is the only confirmation; confirmed and undo | Other requests | Server: mark_do_not_call returns the confirm question and marks only after it was said and answered. The "don't ask first" line stays, since the agent's own question made two (call_926427e1d1f2, change set 15). |
| O: "am I a member?": find_customer, answer first, one offer | Other requests | |
| O: asked to end the call: a short goodbye and end_call | Ending 1 | Merged with Closing, which said the goodbye a second way. |
| O: never quote prices, give repair steps or take card numbers | Other requests | |
| O: the card line, "I can't take payment over the phone, but the office can help with that." | Dropped: server | Contradiction fixed: when a caller starts reading a card, the bridge has the agent say the tenant's `card_stop` line ("For your security, please don't read a card number over the phone...", signed off by Jackson in the safety round). cs24 then had the agent add a second, different line. |
| C: close only on a no to "Anything else?" or a goodbye; "No problem. Anything else?" for a no to an offer | Ending 1 | |
| C: "Thanks for calling Summit Air." plus one nicety, never two (change set 24) | Ending 2 | |
| C: end_call once, alone, after every result is back and said | Ending 3 | |
| C: the system hangs up a moment later | Dropped: tool description | end_call's description says it. |
| C: "Bye." or "You too." after; never say the call has ended; end_call's sentence | Ending 4 | |

## Pre-screen in text

Not run. Two starts, neither with results:

- The first run went against the eval world's tool server, which the Sonnet lane's phone batch also uses. Its open calls blocked that lane's world resets, so it was stopped and every one of its calls was ended through the tool server.
- The second ran each call in its own scratch world on this Mac (a fresh seeded mock and tool server, as `evals/world.py` builds one), with Haiku 5.5 as agent and scripted caller through the Anthropic API and the done-bar caller variants from `evals/caller_variants.py`. It was stopped when the demo path changed, before any call finished.

Two smoke calls on cs24 did run end to end, one estimate booking and one elderly no-heat call that raised to Urgent, so the harness works. It isn't in the repo; anyone picking this up should start from `evals/world.py` and `evals/side_by_side.py`. Anthropic spend was $0.20 for the 19 calls that finished (MEASURED from their token counts at the `fast` profile's prices in `config/models.yaml`), plus the calls cut off when the runs stopped, ASSUMED under $0.30 more.
