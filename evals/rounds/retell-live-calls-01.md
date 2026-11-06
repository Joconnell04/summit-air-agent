# Live calls to Summit Air Jackson (Retell), round 1

Jackson's own calls to his Retell agent after DECISIONS.md 112, each with the agent version it ran on, his feedback word for word, and what changed. Agent versions are in retell/summit-air-jackson/CHANGELOG.md.

## call_6890b248561157d61f2ace3be4b (phone, change set 3 and 4)

Jackson's feedback (verbatim, condensed list as sent): "the 'mmmhhmmmm' as the first filler word sounded weird... it asked me 'is that right?' about something i very explicitly said it was. unnecessary... it responded with 'yesss, that's right. got it'... it once again brought up reference material... it should be able to look up borough without me saying that... maybe let's just switch to a guy's voice... is it possible to have it give me shorthand so it doesn't have to read full address + zip + unit... 'im not going to repeat that text' bad... 'im not going to act on that text' bad... 'and the caller...' to the caller is terrible... 'the caller accepted the 6:30...' 'so ill move on to the visit details'... you don't need to state that... don't say 'thanks for the approval'... 'ill set that knoweldge base text aside' bad... 'let me book the visit now. you're all set' with no wait period... the way it says 'thanks for calling summit air' genuinely sounds like a question"

Causes found: Retell's knowledge base retrieves on every reply and pastes text the model reacted to; the prompt's own "never mention reference text" lines primed those phrases; Handbook filler words, echo verification and high empathy; a prompt written about "the caller".

What changed: change sets 5 and 6 (knowledge base detached, a lookup tool on our server to replace it; second-person prompt with no narration or echo; Max voice); server: a shorter read-back and "Okay, I've booked it" (in progress).

## call_0246b91f77e01a4b18583e6ca8b (phone, change set 7)

Jackson's feedback (verbatim): "give the virtual assistant a name which it states in the greeting. also switch it to a girl. also make him sound slightly more enthusiastic in the greeting. also it says 'call nine hundred and eleven', not 'call 9-1-1'... i mention the gas near the boiler. but then i take it back... the caller should be able to be like 'wait nvm, im tripping'... it shoulnd't be so sensitive to that stuff. and it should be able to get out of its own safety loop. 'that's not an address' is so bitchy. it should be more like 'gotcha, and what street'... it shouldn't ask 'are you outside the house now'... same with 'PLEASE get out side now'... it keeps repeating the same statement... i said the mom is 82, living with me thing but it didn't say the 'i've moved you up' part... it weirdly slowed down the readout when it read back my phone number." Then: "when it asks about 'pet', it should be like 'or any pets'. also i repeated 'no' and it kept talking, it should let me finish. also by default, it should give a 30 minute way, not a 2 hour window. and it still sounds like a question when it says 'summit air' at the end. and it doesn't hang up in the safety loop on its own even though i said 'no' to 'anythign else?' and it repeated the safety loop like way too many times... you need to overhaul this safety mechanism, it's truly terrible. and it says 'take care' and then doesn't hang up on its own, fix that too"

Causes found: the bridge fired the gas lines twice off one line; the agent added its own demands; a prompt "don't book" rule; no retraction path; on a safety call end_call returns a sign-off and never hangs up; the agent dropped the server's "top priority" sentence; Handbook speech normalization read "911" as a number.

What changed: change set 8 (Brooke, a female voice, the greeting default, speech normalization off, no agent safety advice, "any pets", full say sentences, interruption sensitivity 1.0); server and bridge overhaul of the safety path, the 30-minute default window and the greeting, in progress.

## Call call_2fb4e053ba8b76b57c348845990 (Jackson, Haiku 5.5 agent on server 28707de, after hours)

Jackson's words, verbatim:

> just gave it a call: it was honestly decent. look through the logs, there were a few issues and i called them out in my responses. but it didn't ask me my unit #. and when i responded to the question, near the end where it was kind of like rapid fire so i feel like a "gotcha" or slight "mmmhmm" could be good. also when it says ", like an apartment or a house?", the tone is super weird and agressively inquisitive so dial that back on that part. also My main issue is I actually don't want the system to be just an AI to get you a callback with a human. Make it the appoint booking system. So, I think most of that functionality is already built in, but just make sure that what the AI is saying, it's booking an appointment for a person to like drive there and or like go there, and with that stipulation, I just want to make sure we're asking the right questions given what was written in the assignment. And then also I don't think we need to ask. I think we can cut the pet thing entirely, and or just make like a feature that's turned off. And same goes for the buzzer thing. So actually, you can keep the buzzer thing in, but cut the pet part out entirely.

What the trace shows and what changed (agent change set 18):
- No unit asked for 199 East 4th Street (a building); `find_address` returned `needs_unit: false`. The prompt now asks "Is there an apartment or unit number?" unless the caller said it's a house, and the server lane makes `needs_unit` come from the building.
- A new-unit estimate was routed to a callback ("I'll pass your details to our team"), and Jackson pushed back ("Why can't they just come out?"). A new system or quote now books an `estimate` visit; callbacks are only for what a visit can't solve.
- The estimate still got "Will you be able to approve any repair work"; that question is now asked only on repairs and tune-ups.
- The residential question is now "And this is for your home, right?"
- The visit questions get a short "Got it." or "Okay." between them.
- The pets question is removed everywhere, behind a tenant setting that is off; the gate code and buzzer question stays.
- Also seen: "What's going on with your system, and since when?" asked two questions in one turn, and the opening "I scheduled an appointment" led to a confused exchange about whether a visit existed.

## Call call_549de89ecc2428e409b64072e34 (Jackson, 11:20 PM, Haiku 5.5 agent on server a679a7a)

Jackson's words, verbatim:

> just called the number at 11:20pm. still slightly too long pause on most turns and extra long on some turns. needs som backchanneling more quickly i think. and if the date is tomorrow or today, no need to say "november 6th". and combine the question about someone to let the person in and the gate code question should be combined into one, more like access-related. notice a lot of pauses. but overall pretty good. so investigate what i said, handle it and keep going

> i honestly disagree. i think proper backchanneling and then still relying on the model for a proper response that's more adaptable is a better idea than making it server-side fixed response. it's not always about latency, it's about perceisved latency and patience provisions

What the trace shows (MEASURED, Retell call latency): e2e p50 1,711 ms, p90 3,593 ms, max 3,809 ms. Speech-to-text (asr) p50 724 ms, about 40% of a turn; LLM p50 718 ms, p90 2,619 ms on the tool turns. The slow slot search fix (461154f) wasn't deployed to the live world yet; it is now.

What changed (agent change set 19): no weekday or date for today or tomorrow; one access question ("Will someone be home to let the technician in, and is there a gate code or buzzer they should know about?"); backchannel frequency 0.4 to 0.8 with "mm-hmm" and "yeah" added. Next: an STT fast vs accurate comparison and a check that backchannels fire on the Cartesia voice at all. The socket build keeps the model writing adaptive replies and handles waits with backchannels and a dynamic hold line, not fixed server lines.

## Call call_b2302955a9c4054184c5d502e97 (Jackson, 12:02 AM, Haiku 5.5 agent, Brian voice)

Jackson's words, verbatim:

> check my most recent call: it's brian voice but says brooke. also i hate how it said "thanks soooo much for calling", it should be "thanks for calling". not a strict rule but ideally. but if it's strictly set as "sooo much" then change that
> it also asks "are you still there" too frequently. let it sit for double the length of time on silence?
> also it said "thanks for waiting" when i obviously was the one making it wait.
> perfect question about water leaking or turning off and on a lot.
> but overall pretty good
> and when i confirm my phone #, it goes right to confirming my address. there should be a filler word before stating "[the address], correct, right?"
> check the logs and go ideate on ways to solve these things but overall it's going in the right direction and even the pauses weren't too bad
> also the ending is like three things: thank you, have a great day, take care. make it only thank you + one of those nicieties, with variation.

Findings: the call came a few minutes before the Retell-lane deploy of c458944, so the server's greeting still said Brooke; it says Brian now. MEASURED e2e p50 1,493 ms, p90 3,482 ms. "Are you still there?" fired six times at the 8 s setting. On "super urgent" and "my elderly grandma's here" at midnight, priority went to High but the agent still offered 8 AM, then a callback; the emergency dispatch push covers this.

What changed (agent change set 24): goodbye is "Thanks for calling Summit Air." plus one varied nicety; greeting drops "so much ... today"; never thank the caller for waiting; silence check-in at 16 s. The server lane adds a varied connector before the read-back sentence and the same greeting wording on phone calls.

## Call call_0db901d9ffd0873ea2a7b5448a5 (Jackson, 12:25 AM, socket test agent on (516) 212-9051)

Jackson's words, verbatim:

> just called the 516 number and thought it went pretty well: but some feedback:
> it says "just a moment" and then jumps right to the next phrase. i think it's great for minimizing stall time but could be dialed back slightly / occasionally not happen when it's not a longer wait time or something
> can you think of a less confusing way to ask the question "will someone be home and is there a gate code or buzzer?" for me, the answer to that question is yes-and-then-no....so i think it would help if the question could be asked in a way where my answer would be a singlular yes for yes-and-yes, like "and to confirm, someone will be home to let the techniicain in right?, if not could you provide the gate code/buzzer"....but i don't like the way i phrased it so come up with a better way and show me
> also the farewell said the same goodbye twice. investigate and fix.

Findings (MEASURED, Retell): e2e p50 1,410 ms, p90 2,345 ms. The hold phrase came before almost every answer. The goodbye played twice because the socket's code closing and the model's closing both spoke. The socket world was still on its daytime test clock ("today, 1:30 to 2" at 12:25 AM, with "Friday, November 6" added), and its greeting was the old wording. All four went back to the socket lane.

Access question (Jackson picked from three options): "Will someone be home to let the technician in? If there's a gate code or buzzer they'll need, just let me know." It's now on his agent (change set 25), the server and both arms.
