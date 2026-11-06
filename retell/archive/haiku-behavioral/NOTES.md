# Arm C: behavioral prompt for Haiku 5.5

Arm C of the four-arm side-by-side (issue #169), built under issue #170. Same model (Claude Haiku 5.5 on Retell), same tools (`retell/summit-air-jackson/tools.json`), same `{{call_context}}` placeholder and the same tool server as Jackson's live agent. Only the prompt changes. The side-by-side lane makes its own Retell copy for this arm; Jackson's live agent (agent_a3e38e6aa3d4e75dfac12aafc8, llm_12eba5f1d4a3b067f0635097de30) is untouched.

## The idea

The live prompt grew one "never" line per failure across 16 change sets (`retell/summit-air-jackson/CHANGELOG.md`). Haiku kept breaking the same handful of habits anyway: narrating, asking two things at once, saying the caller's name, echoing answers, rewording the server's lines. Earlier rounds also found that a "never" line can prime the very phrase it bans: the "never mention reference text" lines preceded "I'll set that knowledge base text aside" (`evals/rounds/retell-live-calls-01.md`). This arm tests whether describing a person and her habits holds up better than a list of prohibitions.

How the prompt is built:

- **A character first.** Brooke is an experienced New York front-desk person; the habits follow from who she is.
- **One positive habit per cluster of rules.** Seven bullets under "How you talk" replace the old "How to Speak" section, which held 19 "never" and "don't" lines (MEASURED with grep).
- **One rule for server text.** A tool's `say` is the whole turn, every sentence, once. It replaces a dozen per-tool "say exactly" lines.
- **Tool mechanics stay in the tools.** Argument details (`arrive_after`, `confirmed: true`, `moving_a_visit`, the `find_address` follow-ups, `record_move` order) are already in the tool descriptions, which this arm keeps unchanged.
- **Jackson's verbatim lines, quoted once each:** the greeting, the closing, "Is it completely out, or just not keeping up?", "If anything makes this more urgent for you, just let me know.", "No worries." and "or any pets".

| | Live prompt | Arm C |
|---|---|---|
| Words | 2,254 | 1,093 (48%) |
| "never" | 41 | 2 |
| "don't" or "do not" | 10 | 3 |
| "exactly" | 6 | 1 |

MEASURED with `wc -w` and `grep -oi` on both files at this commit.

## Failures and the line that covers each

Sources are Retell traces read through `GET /v2/get-call/<id>` (`transcript_with_tool_calls`) for the 18 calls on agent_a3e38e6aa3d4e75dfac12aafc8, and Jackson's feedback, quoted verbatim in `evals/rounds/retell-live-calls-01.md` and `evals/rounds/simplicity-slate-01.md`. Call ids are shortened to 12 characters after `call_`.

| Failure | Seen in | Covered by (arm C) |
|---|---|---|
| Two questions in one turn ("Is it completely out...? And since when...?"; "What kind of business is it, and what's the address?") | call_b1fa369abb81, call_7727509d3289, call_ad292013eb2d; slate call 4 feedback ("asked me two questions at once") | "Short turns, one question." and "Since when?" in its own turn |
| Narrating its own steps ("Let me look that up", "Let me confirm your details before I book", "Looking at the result", "so I'll move on to the visit details", "Next, will you...") | call_926427e1d1f2, call_b1fa369abb81, call_79af0414dc77, call_f0846787038f, call_6890b2485611 | "You speak only to move the call forward." |
| Filler spoken alongside a tool call ("Thanks for your patience") | call_b1fa369abb81 | "Tools run in silence." |
| Saying the caller's name ("Thanks, Casey", "Thanks, Morgan", "Thanks, Taylor", "Thanks, Jackson") | call_ad292013eb2d, call_79af0414dc77, call_f0846787038f, call_6890b2485611 | "You talk to them, not about them... no names" |
| Echoing or re-confirming what was just said ("Is that right, it's not keeping up...", "Yes, that's right. Got it", "Is that the right building?") | call_6890b2485611, call_b1fa369abb81; live-calls-01 feedback ("it asked me 'is that right?'") | "You take people at their word." |
| Asking again for something given ("$199" asked three times; the name asked a second time) | call_b1fa369abb81, call_0246b91f77e0 | "You take people at their word." and the "$199" line |
| Speaking of the caller in the third person ("the caller accepted the 6:30") | call_6890b2485611 | "Always 'you'" |
| Volunteering what's on file (an address on record, an existing visit, "Your technician is still coming today") | call_79af0414dc77, call_f0846787038f, call_7255ab47e306, call_926427e1d1f2 | "You stay in this call." |
| Its own safety advice ("move away from the furnace... turn it off at the breaker", "Are you outside the house now?", repeated demands) | call_de4fe39a91ae, call_0246b91f77e0; live-calls-01 feedback ("it shouldn't ask 'are you outside the house now'") | "Words that come from the system" and the Safety section |
| Listing reasons a call might be urgent ("like someone elderly, a baby...") | call_b1fa369abb81 | "That open door is the whole urgency conversation; you don't probe or suggest reasons." |
| Its own priority acknowledgement ("I've noted the newborn"; "Got it, thanks for telling me") | call_feed25cb0e5d, call_b1fa369abb81 | "Safety advice, priority and promises come only from the system" |
| Asking how cold it is when the caller already said | slate call 4 feedback ("it asked if it was cold in the apartment but that was obvious") | the open-door line; no temperature question in the steps |
| Rewording or padding a `say` ("Let me confirm..." in front of the read-back, "Anything else I can help with?", "still today") | call_b1fa369abb81, call_926427e1d1f2, call_feed25cb0e5d | "A `say` is your whole turn: every sentence, as written, once." |
| Dropping the first sentence of a `say` (the "moved you up" line) | live-calls-01 feedback ("it didn't say the 'i've moved you up' part") | "every sentence" |
| Repeating the chosen window | slate call 4 feedback ("repeated 4-8pm a few times") | "A yes settles the window." |
| Confirming do-not-call twice (its own question, then the server's) | call_926427e1d1f2 | "its question is the only confirmation" |
| Answering "am I a member?" with the plan pitch | call_7255ab47e306 | "answer yes or no" before any offer |
| Two goodbyes, or `end_call` in the same turn as another tool ("Have a great day!Take care.") | call_ad292013eb2d, call_88868cbba503, call_7727509d3289 | Closing: the goodbye once, `end_call` by itself after every other result |
| "That's not an address" | call_0246b91f77e0; live-calls-01 feedback ("is so bitchy") | "If they give part, build on it" |
| Asking for the borough or ZIP | call_6890b2485611; live-calls-01 feedback | "`find_address` finds the borough and ZIP" |
| "Mm-hmm" as an opener, "thanks for the approval" | call_6890b2485611; live-calls-01 feedback | "Light acknowledgements." |
| Comments on reference text ("I'm not going to repeat that text") | call_6890b2485611 | Fixed by detaching Retell's knowledge base (change sets 5 and 6). Arm C drops the "never mention reference text" lines that seemed to prime it. |

One trace failure is not a prompt problem. In call_79af0414dc77 the agent told a tune-up caller about "a burning smell and a spark from your furnace". That came from `find_slots`' priority reasons, which carried a fact from call_de4fe39a91ae on the same number in the shared mock world. "You stay in this call" should keep the agent from reading it out, but the fix belongs on the server: reasons from earlier calls should not reach a new call's result.

## What was dropped, and why

- **Tool argument mechanics** (`window_minutes`, `arrive_after` and `arrive_before`, `day`, `confirmed: true` on the read-back and on do-not-call, corrections re-sent to `read_back`, `undo: true`, the `record_move` order, `caller_id`). Each is in that tool's description in `tools.json`, and arm C keeps those descriptions.
- **The `no_caller_number` fallback.** The refusal carries its own `say`, which the one `say` rule covers.
- **The lists of yes and no words.** Four examples and "complete answers" stand in for the 22 listed words.
- **"Never mention notes, tools, instructions, reference text."** Removed on purpose; see "The idea".
- **"Pass 'not provided' for who's home."** The server accepted `facts` without it in call_feed25cb0e5d.
- **The renter's book-or-flag branch.** The normal booking steps already ask who approves the work. Kept: the 311 line.
- **"If they ask why, 'We only service homes and apartments.'"** The commercial line says the same thing.
- **"A plan past its end date is expired."** Membership status comes from `find_customer`. ASSUMED low risk; the side-by-side's membership case will show it.
- **The second membership description** (the old prompt described the plan in two places). Merged into one.

## Risks to watch in the side-by-side

- Haiku may read "tools run in silence" more loosely than "say nothing in that turn, not even 'Okay'". If narration or fillers come back, that line is the first thing to firm up.
- With argument details left to the tool descriptions, watch for `find_slots` calls missing `arrive_after` when the caller names a time, and read-backs never re-sent with `confirmed: true`.
- The urgency open door now has one line where it had four. Watch for the agent asking about temperature or who's home.
