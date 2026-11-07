# Reliability loop: phone replays on the socket agent (6 November)

Question: what still goes wrong on real phone audio once the server-led socket agent passes its text runs, and what fixes each miss without a new prompt rule.

Method: each tick replayed or called one journey on the live socket agent, read the call, fixed the cause in the server or the socket, and ran the affected tests. Six ticks, then a review of every change, then an A/B on the one change that was a phrase rule (symptom-questions-ab-01.md). Every fix landed with a test; the commits are named below.

| Tick | What went wrong on the call | Fix | Commit |
|---|---|---|---|
| 1 | "Hold on, let me grab my mail. It's 88 Atlantic Avenue" set a hold, so the answer was treated as an aside and Brian went silent; "Say it again?" got only "Is that all correct?" | A hold that carries an answer is no hold; a repeat request says the address back | 136212e |
| 2 | "It's a house, someone will be home, no gate code" came with the address, and Brian still asked for a unit and whether someone would be home | The unit question drops when the caller said it's a house; the dispatch step asks only what wasn't said | 23e2f3e |
| 3 | The caller started "40" as Brian finished the address question; the socket kept only the words after, so the server heard "Fulton Street" (twice in three calls) | Words said over the end of a question count | e8ce027 |
| 4 | A transfer's hold line opened a call record that never closed, and it blocked a world reset | The hold line's webhook never opens a call | 41a76bf |
| 5 | The catch-all question offered "the banging" to a caller who had just said it was banging | The server writes the whole catch-all, naming what the caller described | 7744478 |
| 6 | Review of ticks 1 to 5: two fixes were too loose, and the how-bad question said "still cooling" on a heating call | Tighter hold and unit rules; the server writes the how-bad question; "ends in 6730" said digit by digit | c992972 |

Result: the symptom wording from ticks 5 and 6 is a word map from what the caller said to what the server says, so it was tested against plain fixed sentences before keeping it: 6 real re-asks against 13 in 96 runs (MEASURED, hand-judged, symptom-questions-ab-01.md). Kept, with the how-bad question skipped when the caller already said whether the system still runs, and no second address check after a confirmed read-back (7e1bc31, DECISIONS.md 133).
