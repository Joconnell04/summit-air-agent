# Knowledge search 1: embeddings against keywords, and Sonnet for the reply after a lookup

Branch `joco/equipment-rag`. Raw output: `evals/rounds/knowledge-rag-01-data/` (`retrieval.json`, `answers.json`). Script: `voice/retell_llm/knowledge_eval.py`. Run on the server (`summit-air`) from a scratch copy, with its keys; nothing deployed.

**In short.** Embedding search found the right passage for all 10 caller questions and returned nothing for the 3 questions the pages don't cover (keyword search also found all 10, so this set does not separate them). The reply written by Sonnet after the lookup was not better enough to pay for what it costs: its first word came 1.2 to 1.5 s after Haiku's (MEASURED medians 1.95 s against 0.62 s), and no answer from either model carried a framing word. Recommendation: leave `SONNET_HANDOFF` off.

## What ran

- Pages: 5 under `tenants/summit-air/knowledge/` (`hvac-basics`, `heating`, `cooling`, `air-and-ventilation`, `thermostats-and-symptoms`), 74 passages, embedded with `text-embedding-3-small` at 256 dimensions into `lookup-index.json` (199 KB).
- Retrieval: the question embedded at query time, cosine against the passages, a small bonus for shared words, the top one to three kept at cosine 0.50 or more and within 0.06 of the best. Thresholds were set on these same 13 questions, so retrieval numbers below are on the set that tuned them (MEASURED, small sample).
- Answering turn: the real assembled system prompt (32.7 KB) and tool list, the caller's question, a `lookup_hvac_info` call, its result (facts only, no log field) and the hand-off instruction (`prompts/lookup-answer.md`). Both `fast` (Haiku 5.5) and `quality` (Sonnet 5.5) got the identical request, thinking off, as the socket sends it. One question, no earlier conversation.
- Timing: each model, each question, 3 requests. Run 1 carried a note the prompt cache had not seen (the first hand-off on a call pays for the whole prompt); runs 2 and 3 hit the cache (a later one). First token is the first text word, measured from the deployed server, so it includes the network to Anthropic.

## Ten questions

| # | Question | Right passage first? | Haiku first word, cold / warm (ms) | Sonnet first word, cold / warm (ms) | Framing words |
|---|---|---|---|---|---|
| 1 | My radiator is banging every morning. | yes | 825 / 544 | 1666 / 2014 | none |
| 2 | Why is my mini split blinking? | yes | 863 / 542 | 1748 / 2191 | none |
| 3 | How often should I change my filter? | yes | 584 / 527 | 2026 / 1957 | none |
| 4 | Is it normal my boiler makes the pipes clank? | yes | 663 / 568 | 1799 / 1747 | none |
| 5 | My AC runs but the air isn't cold. | yes | 597 / 592 | 2829 / 1727 | none |
| 6 | What's an ERV? | yes | 542 / 564 | 1970 / 2101 | none |
| 7 | There's water on the floor under my boiler. | yes | 642 / 793 | 1768 / 1826 | none |
| 8 | What's the difference between steam heat and hot water heat? | yes | 582 / 623 | 2158 / 2251 | none |
| 9 | My mini split stops blowing heat for a few minutes in the winter and there's steam outside. | yes (defrost) | 511 / 699 | 2336 / 2253 | none |
| 10 | When does the landlord have to give me heat? | yes (Oct 1 to May 31) | 809 / 515 | 1939 / 1906 | none |
| | What are your hours on Saturday? | no facts (right) | | | |
| | How much does a new boiler cost? | no facts (right) | | | |
| | Can you send someone today? | no facts (right) | | | |

Medians over the ten questions (MEASURED): Haiku 620 ms cold, 566 ms warm; Sonnet 1,955 ms cold, 1,986 ms warm. Slowest single request: Haiku 993 ms, Sonnet 2,829 ms. One of 30 Sonnet requests (question 5, cold) passed the 2.5 s timeout, so the live model would have written that reply.

Keyword search (the fallback) also put a right passage first on all 10, so these ten questions do not separate the two. Where they differ: for question 8 keyword returned the general "boiler or furnace" paragraph where embedding returned the steam and hot-water passages that answer it, and for "How much does a new boiler cost?" keyword returned an unrelated boiler paragraph where embedding (at 0.50) returned nothing, which leaves the agent free to say a technician goes over costs. Both returned nothing for the hours and same-day questions once "send", "someone" and "today" were made stop words; before that, keyword returned an exhaust-fan passage for "Can you send someone today?". The case for embedding is the larger library and callers' own wording; ten questions that share words with the pages cannot show it.

## Judging the answers

Accuracy: every answer on both models says what the facts say and adds nothing the facts don't hold (checked line by line against `answers.json`). No price, no repair step, no brand code. Question 9 (defrost) and question 10 (heat season) both repeat the numbers right.

Framing words: none on either model in 60 answers (30 each). The new reply-rule guard (`MENTIONS_SOURCE` in `prompts/assemble.py`) dropped none of them. Both models were given the hand-off instruction; without it, Haiku was not tested here, so the instruction may be doing part of this work.

Length: both ran about three spoken sentences, over the "one or two" asked, because the offer of a technician is a sentence of its own. Haiku averaged 47 words, Sonnet 54.

Sonnet's answers read slightly more like a person: it kept the reasons together ("A thin one, or a home with pets...") and once asked a useful follow-up. It also said "Sorry about that" to a caller who had not been wronged (question 5) and, on question 7, asked about heat instead of offering a technician. Haiku's were plainer and always offered a visit. Neither is wrong.

## Recommendation

`SONNET_HANDOFF` stays off. The better retrieval and the guard are what fix the earlier bugs; the stronger model adds about 1.4 s of silence before the first word (plus the filler line, which is itself a sentence the caller sits through) for answers that are equal on accuracy and framing. If a live call shows Haiku mangling an answer, turn the switch on for a few calls and compare, since the code and its tests are in place (`SONNET_HANDOFF=1`, `voice.sonnet_handoff` in `config/models.yaml`).

## Not measured

- Answers inside a full conversation (this is the turn after the lookup with nothing before it).
- Haiku without the hand-off instruction.
- Phone audio: the numbers above are time to the first text word at the server, not to the first sound.
- Retrieval on questions Jackson or callers actually ask; the 10 were written for this round and the thresholds were set on them.

## Cost (ASSUMED from list price and prompt size)

About 8,000 prompt tokens a request. Sonnet: 10 uncached requests and 20 cached ones, roughly $0.30. Haiku: roughly $0.03. Embeddings: 74 passages and about 30 questions, under $0.01. Total under $0.40 against the $1 limit.
