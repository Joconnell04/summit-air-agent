## Socket arm (issue #176)

Kept on this branch until it's folded into `retell/PROMPT-LOG.md` (joco/prompt-log). Same shape: what I changed, why, what happened.

The socket agent ("Summit Air socket (test)", agent_9e575b3fa1457c8d4331a3b80a) runs on our own custom-LLM socket, so this prompt isn't in Retell. It's `retell/arms/socket/prompt.md`, read by the socket on every call, with Claude Haiku 5.5 through the Anthropic API (config/models.yaml `fast`). The server speaks the read-back, the offers, the booking line, the visit questions, the safety lines, the greeting and the goodbye itself, so most of the rules that kept growing on my live agent aren't in this prompt at all.

| Version | Words | "never" | Commit |
|---|---|---|---|
| v1 | 813 | 15 | 468a331 |
| v2 | 855 | 17 | 59052d8 |
| v3 | 883 | 17 | b4b0368 |
| v4 | 965 | 18 | ce89c64 |
| v5 | 1,035 | 19 | a9e098c |

MEASURED: `wc -w` and `grep -oi never` on the file at each commit. Arm D's prompt, which v1 started from, was 828 words with 14.

### v1 (468a331)

Arm D's prompt with one section swapped. On Retell's LLM the model had to copy each tool result's `say` word for word, and it reworded about half of them (21 of 40 in arm D's rounds). On the socket the server says those sentences itself, so the model now sees them as `said_to_caller`, already heard, and only does what `next` says. Kept. On every socket phone call since, the server's sentences went out word for word (MEASURED, socket-buildout-01).

### v2 (59052d8)

The gas test in text runs had the model giving its own safety talk ("Once the gas smell has been checked", "I'm not going to treat this as fine just yet"). I added my own rules from my live agent: never repeat the advice, never talk about whether the smell is dangerous, take a false alarm at its word, go on with booking if they want a visit. Kept, with one line I made up for when they ask if it's dangerous ("I can't say from here, but I can get a technician out."). Not checked on a call yet.

### v3 (b4b0368)

My change set 18 from call call_2fb4e053ba8b76b57c348845990: "And this is for your home, right?" instead of the residential question, a quote books an estimate visit, and callbacks only for billing, complaints, prices or when nothing can be booked. The visit questions moved out of the prompt into the server, which asks them itself. Kept.

### v4 (ce89c64)

Change sets 19 to 22. The assistant is Brian. Most questions open with a short connector that changes each time. The name question depends on what the caller ID matched before hello. Never ask for the ZIP. The goodbye came out of the prompt because the socket says it. Mostly kept. The model still asked for a ZIP once and asked for the system type after I'd said air conditioning (call_046f37112cac691a50019893717).

### v5 (a9e098c)

Change sets 24 and 25, plus the prompt lane's review (#183): never thank them for waiting, one ask to repeat when a reply can't be made out, "Bye." or "You too." after the goodbye, a request to end the call ends it, "$199" is the house number 199, membership answers yes or no first, the renter 311 line, sparks get an Urgent flag, and no card line of its own because the server says the card stop. The two narration rules became one. On call_046f37112cac691a50019893717, "$199" worked. The model still opened with two questions and some narration ("Actually, let's get you booked."), which the socket now cuts in code instead (6c585f3). Being measured in the done-bar runs.
