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
