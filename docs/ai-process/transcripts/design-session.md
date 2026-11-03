# Design session transcript (excerpts)

Jackson's messages (spoken through transcription, so with transcription errors) and Claude's replies, in order. Tool calls, background-agent notices and system messages are left out. Secrets are redacted. The co-authoring rounds and decisions these led to are in `coauthoring.md` and `/DECISIONS.md`.

## 1. Jackson

Read docs/ai-process/briefs/00-design-kickoff.md and start.

**Claude:**

Read the brief and inputs; writing users.md version 1 now.

**Claude:**

I've written version 1 of [docs/users.md](docs/users.md), logged it as round 1 in coauthoring.md and committed it on main. Nothing is pushed. Here's the short version:

**Callers**
1. **No heat or no cooling:** the most common call. They want someone out soon and a real arrival window.
2. **Vulnerable household:** someone older, a baby or a medical condition, and often a relative calling for them. The agent asks about this on every no-heat or no-cooling call, because callers rarely mention it on their own.
3. **Safety caller:** gas, carbon monoxide or smoke, often said in passing. The fixed safety instruction comes before any other question, and the check runs on every turn.
4. **Maintenance scheduler:** calm, often a member. They want a quick, convenient time.
5. **Member:** any of the above who is on a plan. The agent looks them up by phone first and names the plan benefit.
6. **Renter or someone who can't authorize the work:** the agent records the authorizer separately from the caller. How a tenant with no heat is handled gets decided in call journeys.
7. **Repeat caller:** the agent finds the open job first and never creates a duplicate.
8. **Commercial caller:** spotted early, declined politely, never booked. The grey zone (small landlords, two-family houses) also gets decided in call journeys.
9. **Not a customer call:** wrong numbers, spam and people testing the bot. The agent ends these quickly and stays in role.

There's also a list of traits that can apply to any caller: upset, hard of hearing, Spanish-speaking, two people on the line, or asking "are you a robot?"

**Staff:** the owner (Paul's role), the dispatcher, the technician (what they need on the job), and an after-hours on-call person. The on-call person is an assumption.

**When needs conflict:** a caller in danger comes first, then a broken system, then the owner's business, then staff time.

What would you change? Once you're happy with it, say lock.

## 2. Jackson

Okay, so I'm just speaking and here's some feedback and considerations and stuff for the user's markdown file. So firstly, just do a little bit of background research on how these HVAC companies operate. We need to know their business, to know their users. You know, what does an on-call person do? Are they just like the person answering the phone? Do they know the HVAC people? You know, it's a 40-person company, so you can make some assumptions around that, but make sure we really know the different people involved on this. Also think through if we should implement some form of like playbook layer, you know, or like tool call thing or something, like a playbook. So that way it's like, if there's a surge in this zip code and a phone number from that zip code is calling, you know, maybe we know not to try to upsell them on that call because we know it's probably going to be related to a surge. I'm not sure if that's the best idea, but just something to consider and ideate on. Yeah, like vulnerable household. I like your wants and worries for all of these. That's a good way to do that. But also through this, you know, maybe take some notes or whatever on ways we can actually start to capture these things, like vulnerable household. Like, are there keywords that we can expect a vulnerable household will say that deterministically would mean we change our playbook or approach or something or label as priority? You know, I say there's probably not too many like 100% deterministic things we can build in, but maybe there's like a sentiment score or something like that that we can build in without breaking the latency budget. Not really sure. Another thing to ideate on. I'm not saying it just to tell you what to do. I'm saying it to just surface an idea I'm having. And make sure to ask around, are there vulnerable people? Like, don't make that weird. There needs to be some good like language use, like normal language. And make sure we're not implementing some like potentially damning system for stuff related to three, the safety caller. I.e., there should be like proper checks to ensure that we're not like hanging up on a safety caller or something like that. I don't know. Make sure you simplify language too. For example, for maintenance scheduler, you say, in a surge, these jobs yield to urgent ones. That is confusing wording and makes no sense to the reader. Definitely members expect to be recognized to get the plan's perks. And there should be a proper tracking, acknowledgement, and like playbookization around like repeat callers. Like if somebody's calling like 5 times within the same minute, like that's something that should be properly tracked and applied within the conversation or something. Yeah, so for commercial stuff, yeah, just make sure you filter them out properly. Don't add them in. Actually, for commercial callers, we should be tracking that a company called in case it ever becomes useful. We just shouldn't be like making it a big deal or spending a lot via tool usage on that, like, or whatever. But if anybody calls, we should be tracking something around that. Yeah, for number 9, not a customer call. That's good considerations to have. Make sure your handling for other languages is solid too, without requiring the need for like a million more agents or anything. Yeah, so anyway, take into account all the feedback. It's not too much. Make sure you do proper research on the industry and what these actual different stakeholders and people look for, you know, things like common requests for HVAC companies, especially like those in New York and stuff like that. Yeah, and then next. So do that and then slash next and lock

**Claude:**

I've added your ideas to considerations: C-32 playbooks, C-33 asking who's home, plus new points on commercial logging, vulnerability signals, safety no-dead-ends, languages and repeat callers. Two research agents are still running (issues #11 and #12). I'll revise users.md once they report back.

**Claude:**

First research report is in (shop roles, booking rates, memberships). Still waiting on the NYC callers one.

**Claude:**

The shop operations research is in and I've read it. It points to separate call-takers and dispatchers, a service manager, comfort advisors who handle replacement sales, and an on-call tech who keeps the after-hours phone for a week at a time. I'm still waiting on the NYC callers research before I revise users.md.

**Claude:**

Both research reports are in. Revising users.md now.

**Claude:**

Users is revised from your feedback and the research, and now locked at round 2. Everything is pushed to main: round 2 with your words logged verbatim in coauthoring.md, the lock, and the ticked step in phases.md.

**What changed in [docs/users.md](docs/users.md)**
- **Staff roles are now based on research.** At a 40-tech shop the person answering the phone and the dispatcher are different people. A service manager runs the techs, and comfort advisors sell replacement systems. The on-call person is a technician who keeps the after-hours phone for a week at a time and decides whether to go out tonight. That last point is still an assumption, though it's the most common setup. Each role now says what it judges success by.
- **New section on how the agent tells callers apart.**
  - Facts the server already has: the phone number, membership, open jobs, and whether the ZIP has a surge.
  - A fixed phrase list that can only raise priority.
  - A small score that reads each caller turn on the side. It runs outside the reply path, so it doesn't add delay.

  The server uses these to pick a playbook. For example, a caller from a ZIP with a surge gets the short path and no upsell.
- **Vulnerable household:** the agent asks once, in plain words: "Is anyone at home who's older, very young, or has a health condition the heat would affect?"
- **Safety caller:** gas and carbon monoxide get separate scripts, with wording taken from Con Edison, National Grid and NYC Health. The end-call and spam paths can't close a call with a safety signal until the script has been said and a person flagged. A call that drops after a safety signal gets a callback.
- **Repeat callers:** calls from the same number are linked. A caller who got cut off picks up where they left off, and someone who keeps calling with no progress goes to a person.
- **Commercial callers** are never booked. One line (company, contact, what they needed) is saved with the normal end-of-call record, with no extra lookups.
- **Languages:** one agent handles Spanish on the call, and other languages get a callback.
- **Also new:** a caller type for replacement and install quotes, which are common in NYC because of the state Clean Heat program. The renter section now reflects the NYC heat law, and the tune-up surge line is rewritten in plain words.

Your playbook and repeat-caller ideas went into [docs/considerations.md](docs/considerations.md) as C-32 and C-33, with new points on five existing items. The research is in [docs/explorations/001-hvac-shop-operations.md](docs/explorations/001-hvac-shop-operations.md) and [docs/explorations/002-nyc-callers.md](docs/explorations/002-nyc-callers.md).

**/next**
1. Phase 1, Design: users is done, call journeys is next.
2. Needs you:
   - #4 voice platform (LiveKit vs Retell).
   - #5 pipeline shape and model profiles.
   - #6 eval design.
   - #9 live go/no-go, which needs ten real calls.

   None of these is due yet.
3. No explorations are waiting. #11 and #12 are folded into users.md and closed. I'll ask their open questions during call journeys, where the answers change what gets written: which three counties, whether Spanish is live in v1, whether to give tenants a heat-law line, whether the agent books install estimates, and who signs off the safety wording.
4. Unless there's anything else, next is call journeys.

## 3. Jackson

I'm going to give you more feedback and stuff, but just another consideration to throw on to the ideas or consideration stuff is if we could use Jev or Laya or a decisioning model at any point without breaking the latency budget to make the system better. That's just something I want to throw out there. Yeah, and regarding what I said about like a zip code based surge, I'm not really confident that's like how that really works, but that was just an idea to throw down. So every time I have an idea to throw down, it doesn't mean just go and implement verbatim what I said or based on your interpretation. It's just like look into stuff and whatnot. But I do think Jev or decisioning model would be a good way to show the interviewer that I'm up to date on those types of things and am constantly looking for the best ways to do stuff. Yeah, make sure that the fix list of phrases to raise priority isn't like overly ambitious and stuff. And make sure that the safety caller wording is not too weird or like overly formal. Should still sound like a human, properly explain things without going overly too technical, stuff like that. Yeah, and regarding the repeat callers, I think that's like just a good point to approach this work with in general is like item potency. So keep that in mind. And this system shouldn't be overly specific to New York City, but like obviously it should be somewhat specific. Like it should have proper context on each of the boroughs and neighborhoods and stuff like that, if it makes sense. But it shouldn't be like mentioning state programs unless it's like super relevant. Okay, so now I'm going to look through issue number 4 and I'll get back to you with more feedback on that. Okay, actually, this is not how I want this work to go. Because you made an issue, like all these issues in GitHub, but at least from my understanding right now, I can't even view those in the Claude app. And that's what I want to do. That's going to really streamline my workflow if I can just look at the issue right here on split screen mode, looking at a file or something. And, you know, I'm just going to pick on number four, the voice platform one, for example. I want to see the question, you know, the options, and like the purpose and reasoning and associated trade-offs as like, oh, you know, 1 to 3 sentences. And then I want to see context. Like, what am I, like, in this situation, it should be the rundown of the options and what I'm trading off by picking one or the other and stuff like that. And then your recommendation and why, like 2 sentences. And then I'll read it and tell you what I want to do via like chat, like here. But if you want to assign yourself as the agent to that GitHub issue and continue using GitHub issues, I'm very supportive of that. But I want these things properly like researched in the background and then surfaced to me with the information so I can read it in just a few minutes and then tell you what I want. So make sure that happens for all the issues and you implement that new system. Yeah, and then let's go through the issues before we get to the next phase and subphase.

**Claude:**

Considerations are updated. Next I'm starting background research on decisions #4, #5 and #6, then I'll apply your users feedback.

**Claude:**

Now logging round 3 and wiring the readout system into the repo's working agreements.

**Claude:**

Round 3 is pushed. While the three readouts research, I'm drafting a proposed triage for #10 so you can react to it rather than start from blank.

**Claude:**

Your feedback is applied and the readout system is in place. Research on #4, #5 and #6 is still running in the background.

**users.md round 3** (pushed; I've kept it locked)
- **ZIP surge:** taken out as a design. It's now consideration C-32, marked as an unverified idea.
- **Fixed phrase list:** cut to gas, carbon monoxide, smoke or a burning smell, and someone on oxygen or other medical equipment. Softer cues like age, a baby or an illness go to the classifier or a decision model.
- **Safety wording:** rewritten as two short lines a calm person would say. Gas: "Okay, that comes first. Please get everyone out of the house now, and don't flip any switches... We'll sort out the heat after you're safe." Then the agent ends the call so the caller can get outside.
- **Local context:** boroughs, neighborhoods, housing types and how people give addresses come from config, not the prompt. A city or state program only comes up when it changes what the caller should do, so the Clean Heat line is gone.

**New considerations**
- **C-34 idempotent by default:** every booking, flag, text and lead is keyed to the call and the caller, so nothing happens twice.
- **C-35 decision models:** Laya, OpenAI Decisions and "Jev". I couldn't tell what "Jev" refers to, so the #5 researcher is trying to identify it.
- **C-36 local context:** service-area knowledge comes from config, without hard-coding NYC.

**Readouts for decisions**
- Each decision now gets a file in `docs/decisions/NN-<slug>.md` that you can open in split view. It has the question, each option in 1 to 3 sentences, the trade-offs in context, and my recommendation in about 2 sentences. Supporting research sits below that on the page, so you can skip it.
- This is now a repo rule in CLAUDE.md, the needs-decision issue template and `/next`. GitHub issues stay as the tracker and link to the file.
- I saved two memories: your mid-step ideas are things to look into, not decisions, and the readout format.

**Ready now:** [docs/decisions/10-considerations-triage.md](docs/decisions/10-considerations-triage.md) gives a proposed in, later or out for every consideration. I propose seven items for later and none out. Read it whenever you like and tell me what to change.

**Still researching:** #4 voice platform, #5 pipeline and models, #6 eval design. Each will land in `docs/decisions/`, and I'll tell you as each one is ready. Then we go through them in order: #4, #5, #6, then #10.

**Claude:**

The #4 voice platform and #6 eval design readouts are ready and pushed. #5 (pipeline and models) is still researching.

- [docs/decisions/04-voice-platform.md](docs/decisions/04-voice-platform.md) recommends LiveKit as the main platform with Retell on standby. It adds the $50/mo LiveKit plan, because the free one takes 10 to 20 s to wake an idle agent, and a written rule for switching the demo number to Retell. The idea that Revin uses LiveKit rests on one job post that has since been taken down.
- [docs/decisions/06-eval-design.md](docs/decisions/06-eval-design.md) recommends our own text-mode harness, scored by code checks against the server, with an LLM grader for call quality and about 10 audio calls added after #4 is decided. A quick run costs about $1 and a full run about $15 (ASSUMED).

You can start reading #4 now. I'll tell you when #5 is ready.

**Claude:**

All four readouts are ready and pushed. Each one is linked on its GitHub issue, and you can open them in split view.

1. **[#4 Voice platform](docs/decisions/04-voice-platform.md):** I recommend LiveKit as the main platform, with a Retell number on standby using the same tool server. That means LiveKit's $50/mo plan, plus a written rule for when the demo number switches to Retell. The only evidence that Revin uses LiveKit is one job post, which has since been taken down.
2. **[#5 Pipeline and models](docs/decisions/05-pipeline-and-models.md):** I recommend separate speech-to-text, LLM and text-to-speech stages, and the safety script is spoken straight from code with no model involved. A single speech-to-speech model was no faster than Bobert's measured 1.0 s and can't guarantee the safety wording word for word.
   - "Jev" is TypeSafe AI's hosted decision model.
   - We launch with the fixed phrase list and the server rules. OpenAI Decisions and Jev then get tested in the evals on two questions: whether the caller agreed to the read-back, and whether someone vulnerable is in the home. One gets kept only if it beats the phrase list.
   - Laya is dropped.
3. **[#6 Eval design](docs/decisions/06-eval-design.md):** I recommend our own harness. A simulated caller talks in text to the real tool server, code checks decide pass or fail, and an LLM grader scores how the call felt. About 10 real audio calls get added after #4 is decided. A quick run costs about $1 and a full run about $15 (ASSUMED).
4. **[#10 Considerations triage](docs/decisions/10-considerations-triage.md):** a proposed in, later or out for each of the 36 considerations. Seven are later, none are out.

The #5 readout runs a bit past one screen because it covers two questions: the pipeline and the side models. The order is #4 first, because #5 and #6 partly depend on it.

## 4. Jackson

Okay, now I'm reading through decision number four, the voice platform, and I'm just going to transcribe my thoughts as they come to me. So something I'm keeping in mind is that part of the assignment mentioned choosing the right approach based on the timeline. Given that we have a week and I'm a quick worker who's passionate about the space, I definitely think it's okay to choose the custom stack as long as we can benchmark it to prove that it's like offers actually better performance. And then regarding ensuring we have proper time to develop and test it, that's on me and I'm keeping track of that because we have a week right now. And I think a week is enough. First of all, in documentation, don't assume that the reviewer, which will likely be a human and the human's agent that they're using to review the repository, will have any familiarity with Bobbert. So ensure that you either explain that somewhere or just try not to use the term Bobbert. If I'm thinking correctly, Bobbert was just the test agent we set up for all this before we actually got the take-home assignment. Anyway, now I'm reading through the options for decision four, voice platform. Another consideration I'm just putting out there is template ability and multi-tenant deployment. Because ideally, our solution is a system that can be shaped to the needs of another client without too many customizations outside of those that exist within the template. So going with a custom DeepGram Cartesia Haiku Live Kit Twilio stack is good, but only if we can also make that an easy to modify template for a different use case, like think a roofing company or something like that. Just something to keep in mind and document that we considered. I think I'm going to need a little bit more information on what retail can do regarding simulations and like turn taking. Is it truly the best platform? Because I don't see a point in using a custom DeepGram Cartesia stack if we can just make a better product on retail. And do you think it would make sense to just do both in parallel and then evaluate which one was better? I think that could also be a good way to do it. And what is the purpose of retail's built-in transfer thing? And what does owning the server endpoints for the end call or log handoff thing, like what value does that actually provide us? I don't want to do two phone numbers. I want to do one. That's overcomplicating it. Obviously it should be super reliable and basically never fail, but one phone number, not two. Also, you need to explain to me what VAPI and or Pipecat do. You just gave me the option, but you didn't explain what's worth reviewing there. Can I only get away with using LiveKit if I pay for a $50 a month plan? Because I don't want to do that. Also, if possible, I want to use a phone number from Twilio so that way I can use it with retail and or DeepGram Cartesia. Is that possible? Get the phone number on Twilio and then test both systems, a custom stack or a retail stack. And when I say custom stack, I'm referring to like LiveKit with custom stack integrated. Anyway, give me more details in your chat output, and then I'll decide from there. Also, in your rundown, like, I appreciate the technical details, like, pointed at a TweeML bin that dials LiveKit's SIP endpoint, blah, blah, blah. But given that I'm not super familiar in these, you need to simplify the language so it's easier for me to understand. And you can include technical details in parentheses and stuff like that, but it needs to be like more like explain it like I'm 15 or 20, not a 30-year-old software engineer. And note that latency is only a major concern when we're breaking the real-time barrier. Like if it creates a delay that's long enough for a human to like notice a difference, that's bad. If the delay is like menial, it does not matter. I'm comfortable spending money too, but a $50 a month plan seems kind of high for something like this. Would rather pay for usage-based consumption instead of like flat rate plans, but let me know. But no, I do not approve of option C yet, so give me the rundown.

**Claude:**

**Short answer:** you can run both stacks on one Twilio number, you don't need the $50 LiveKit plan, and building both in parallel then measuring them is a good plan. That's my new recommendation, replacing option C.

**One phone number, both stacks.** Buy one Twilio number. Twilio sends its calls to whichever system you name in its settings (a SIP trunk, basically a forwarding rule for calls over the internet). You can name two destinations in order, for example LiveKit first and Retell second. If the first one errors or doesn't pick up within 4 seconds, Twilio tries the second automatically. To switch which one is main, you change a setting; no new number is needed. The same number can also send the booking confirmation texts.

**The $50 plan isn't needed.** That plan only fixes a slow start (10 to 20 seconds) for agents hosted on LiveKit's own servers. If we run our agent program on our own small server that stays on, it's always ready and doesn't use LiveKit's hosted-agent minutes. That server costs a few dollars a month, billed by usage (ASSUMED, something like Fly.io or Railway). LiveKit's free plan covers the rest: 1,000 minutes a month for calls coming in through Twilio, and 5 calls at once. Demo volume is far below that. One gap: LiveKit doesn't publish what happens when you go over the free limits. At demo volume that won't matter.

**What Retell does well**
- **Turn-taking:** Retell uses its own model to decide when the caller has finished talking. You tune it with dials: how easily the caller can interrupt, how quickly it responds, and whether it says "mm-hm" while listening. It tends to sound polished out of the box. The catch is that you can only turn the dials; you can't see inside it or change how it works.
- **Simulations:** Retell has a fake caller (an AI playing a scripted customer) that runs test calls in text. Another AI then grades whether the call met your written goals, and it can fake your tools. What it can't do, from what we read in its docs, is check that the agent called the right tool with the right details, or that the right booking was saved. Those are the checks that matter most for this assignment.
- **Is it the best?** It's the strongest of the ready-made platforms for getting a good-sounding agent fast. Whether it actually sounds better than our own stack on real phone audio is unknown until we test both. That's the case for a bake-off.

**LiveKit with our own stack.** LiveKit is free, open software for running a voice agent. We write the program that wires together Deepgram (hears the caller), Claude Haiku (decides what to say) and Cartesia (speaks). You can change anything: how long to wait after the caller stops talking, for example longer while they read out a phone number, plus every rule and every log. You also own every problem. Revin's voice team appears to use LiveKit, but that rests on one job post that has since been taken down.

**What "owning the end-call and handoff" buys us.** Retell has built-in actions: one hangs up, and one transfers the call to a person's phone (its built-in transfer). When the AI uses them, Retell acts right away and our server never hears about it. So if the AI decides to hang up on someone who mentioned gas before saying the safety lines, nothing stops it. If every hang-up and handoff goes through our own server instead, the server can refuse until the safety lines were said and staff were alerted. It can also log the call and text the on-call tech. Both platforms can work this way: on Retell we'd skip the built-in actions and use our own versions. I still need to check whether Retell lets our own action hang up the call. On LiveKit it's natural, because we write that code.

**Vapi and Pipecat, and why I'd skip them**
- **Vapi** is a ready-made platform like Retell. You pick your own speech and model vendors in a dashboard and pay about $0.05 a minute on top. It makes the same trade as Retell, with nothing already built on it.
- **Pipecat** is free, open software for building voice agents, like LiveKit, made by the company Daily. It makes the same trade as LiveKit, but with less sign that Revin uses it.

**Latency, in the terms you set.** In normal conversation people leave about 0.2 seconds between turns (SOURCE: Stivers et al. 2009). On the phone, a pause past about 1 second starts to feel slow, and 2 seconds feels like the line went dead (ASSUMED). The earlier practice agent measured about 1.0 second on normal replies, which is fine. Replies that need a lookup or a booking took about 2.3 seconds, which people will notice. Those lookup turns are the latency that matters on either platform.

**Template for other companies (now C-37).** Either way, all the company-specific parts live in config: company facts, service area, job types, plans, safety lines, prompts with blanks to fill in, and the connector to their scheduling system. A roofing company would mean new config, not new code. LiveKit makes multiple companies easy: one running program picks the company from the number that was dialed. On Retell, each company is its own agent, copied by a script. Both work.

**Proposed plan, if you go with the bake-off**
1. Build the shared parts first: the tool server, the fake ServiceTitan, the prompts and the evals. These are most of the work, and both stacks use them.
2. Add two thin voice layers on top. Retell takes hours, because it's only config. LiveKit takes a day or two, because the earlier practice agent's program already exists.
3. Score both on the same test calls plus 10 real phone calls each, by measuring:
   - the pause before each reply on real phone audio
   - lookup-turn pauses
   - how it handles being interrupted
   - test pass rate
   - whether the safety rules held
4. The winner becomes main on the one number and the other becomes the automatic backup. If the backup isn't worth keeping up to date, we drop it.

Cost per minute is about $0.065 for LiveKit and $0.11 for Retell (SOURCE: their pricing pages). At demo volume the difference is a few dollars.

I also added a repo rule: docs call Bobert "the earlier practice agent" and link it the first time, since reviewers won't know the name.

Sources:
- [LiveKit pricing](https://livekit.com/pricing)
- [Retell: import a phone number](https://docs.retellai.com/api-references/import-phone-number)
- [Retell: keep your number via SIP](https://www.retellai.com/learn/how-to-keep-your-existing-phone-number-using-a-sip-connection)
- [Retell: interaction settings](https://docs.retellai.com/agent/interaction-configuration)
- [Retell: interruptions and turn-taking](https://www.retellai.com/learn/how-ai-voice-agents-handle-interruptions-and-natural-turn-taking)
- [Retell community: interruption settings](https://community.retellai.com/t/agent-interruption/922)
- [Twilio: multiple origination URIs for failover](https://www.twilio.com/blog/sip-trunking-load-balancing-and-failover-made-easy)
- [Twilio voice failover best practices](https://www.twilio.com/docs/voice/twilio-voice-failover-best-practices)

## 5. Jackson

ok some notes and feedback:

* use same number from bobert, retire bobert stuff. we already paid for that phone number so should be an option. but don't reuse the SMS number used for the epic consulting receipt agent as that's solely for epic stuff. so if you need a new twilio # and can't buy it on your own, let me know and ill do it.
* let's properly document speed to speed model not being faster too and let that be a defense for our design decision when relevant.
* yeah i agree, let's build our own eval harness.
* i agree we don't need the $50 thing but let's document that we already have an always-on Vultr server - and provide specs and defensability/rationale around how we know it won't fail/how we know its compute and hardware specs are sufficient
* do some research and analysis around the limitations/necessity for bandwidth for a 40-person urban HVAC company...i.e. how much concurrency do we need, is 5 calls at once acceptable, what happens if >5 call at once/is that likely to happen, how do we defend that on the demo and stuff....i think a max of 5 is good for the demo but curious your thoughts based on that research/analysis and if we can build into the system a way for >5 concurrent calls to not just simply get erased or something...idk maybe a queueeu mechanism or something
* ensure we have postman-based logging built into the plan and an ability for agents to review those....that will help us and our dev agents streamline QA/checking the agent's traces
* i like the bake off idea but let's make sure it doesn't end up with a chaotic repo and that deciding on one and going with that is possible and not too complex
* and make sure to check and document "check whether Retell lets our own action hang up the call"
* document we explored other options like Vapi and Pipecat but didn't choose them for XYZ clear rationale....i like a custom retell-like stack with retell as the baseline over vapi or pipecat.
* make sure we're documenting in some fashion what elements are "template" and which are "single-tenant" or something...this should make taking our final deliverable and adapting it for a roofing or kitchen remodeling company without rebuilding it entirely from scratch
* start building out the fake servicetitan system across both CRM and FSM stuff and anything else that a real 40person HVAC company would have that we'd need to emulate or whatever
* we should also be providing some cost analysis....i.e. let's go forward with the option we think is best but also document a cheaper operating approach...come up with a set of metrics to evaluate on like cost per minute, one-time setup cost, etc. or something....this would serve as materials to answer a client quetion like "what if we halved our budget, what could that get us?"


then give me the eli10 summary of the options we have for the voice platform stack, i'm still a bit confused on all the different options in front of us. your output should make deciding on one easier and clearer for me

**Claude:**

The old practice agent's number, (678) 336-6730, was bought inside Retell, so it lives in Retell's Twilio account rather than one we control. Checking the Vultr box next.

**Claude:**

Cron line removed; the first kill step hit its own shell. Stopping the four processes by PID instead.

**Claude:**

While those run, I'm recording the #5 and #6 decisions.

**Claude:**

I've recorded #5 and #6, retired the old practice agent on Vultr, and written the architecture and template docs. The mock ServiceTitan build and two research jobs (capacity and cost, call control and the phone number) are still running. When they report back I'll fold the results into the #4 readout and give you the simple summary.

## 6. Jackson

also note the following emails i've been getting from retell. i'm confused why im getting these as bobert should not be running right now but idk, figure that out. then make sure you don't use those deprecated function calls in the future Also, just a higher level point of feedback is you need to just make it clear, like the one thing you need my thoughts on or decisions on with the information and context for that decision in front of me. So like maybe that's point me to some markdown document in the decisions thing or a section of one and be like, provide your thoughts and thinking there. And then that way I can give it to you. We're focusing on just one thing at a time. And then we're moving forward from that. I get distracted easily. So it's best when you're kind of surfacing decisions that need to be made. I'm maybe asking some questions, getting some clarification, and then I'm giving you my thoughts and decisions. And then you take that properly, implement it or whatever, and then you surface the next thing to me. And that way we have a more progressive flow. On that note, in my opinion, I've given you, like, in my opinion, we still haven't decided on the voice platform thing. So let's round that out, and then we'll tackle the next thing. Which to me looks like would be 0.5 pipeline and model markdown stuff. But yeah, I don't know. But let's stay focused and keep things progressing. And that way we're not just running around in circles. I'm going to speak to my thoughts on decision 5, pipeline and models now, but don't run with that yet. Let's round out voice platform stuff first. As I mentioned in the interview, I definitely realized that something they care a lot about is cascading model selection for using the right size, class, and pricing of model to the assignment at hand. That way we're not using, you know, Fable for every small ask. So I see there is like a handful of different options we can use. I don't think speech to speech is the one that would be best because there's no cascade and it's too integrated of a solution and doesn't give us enough flexibility and customization for, you know, pulling in data, doing proper checks that we're not saying the wrong thing. Stuff like that. So that's my reasoning by not doing a speech to speech. I think option C is too complicated. But come up with a better reason for not going with that. I'm not going to just document it as, quote, too complicated, quote. I'm confused what you mean with option D, so explain that better. So I can make a better decision or more informed decision. Same thing with E. I don't really know what that's saying. I definitely think we should implement Jev or Lia later. So point out like places where that could play into later, but I just want to scaffold the system without it now and then go back in and add that later. But in the meantime, to support that, maybe have a sub agent design a test suite for Jev and Lia and stuff for like a scoped implementation and then see and measure stuff like, you know, how much latency it adds, if it's truly accurate or not, if it's genuinely better than just having Haiku be the decision and the action model, stuff like that. I'm also confused what you mean by caller text for gates in the context table. So explain that to me better. Multilingual handling. Is not like a paramount consideration. It's just something that's good. Flux multilingual. Is a good option. But that's something we can come back to. So like write down that to come back to later. Then you need to make your recommendation more clear. I really don't know what this is saying. Okay, so use that to revise the 0.5 pipeline and model profiles markdown file. Like just reword it and then I'll speak to it again. Later, and then do the other stuff I asked as well.

**Claude:**

Looking for the Retell emails in your personal inboxes through Mailroom.

**Claude:**

The Retell emails don't come from anything still running. Next I'm pausing the earlier agent's Postman monitors, which are still failing every day.

**Claude:**

Now rewriting the #5 readout based on your feedback.

**Claude:**

Now starting a background agent to design the decision-model test suite.

**Claude:**

Rewriting the #4 readout with everything we've learned, then I'll give you the simple version.

**Claude:**

**The Retell emails come from five one-off test requests sent on Oct 29, not from anything still running.** At 22:31 UTC that evening someone ran curl five times against Retell's old list endpoints: twice for the list of LLMs and three times for the list of agents. They came from 172.93.11.194, which is neither this Mac nor the Vultr box, and nothing in the old agent's code calls those endpoints. Retell batches these warnings and emails them on Mondays, which is why they arrived today.

My own lookup of the 678 number this afternoon also used an old endpoint, so expect one more of these emails next Monday. The repo now has a rule (in CLAUDE.md) to use only Retell's newer versioned endpoints, which is what I used from then on.

Cleaning up the earlier practice agent:
- On Vultr, its servers, tunnel and auto-restart job are stopped. The files are kept, and a note on the server says how to bring it back.
- Its two Postman monitors were still failing every day and emailing you. Both are paused.

**What needs you now: #4, the voice platform.** The full readout is [docs/decisions/04-voice-platform.md](docs/decisions/04-voice-platform.md). Here's the simple version.

A phone agent has four jobs:
- **Ears:** turn the caller's voice into text.
- **Brain:** decide what to say.
- **Mouth:** say it out loud.
- **Switchboard:** connect the phone line and notice when the caller has stopped talking.

The options differ in who does each job.

- **A. Build it ourselves on LiveKit.** We wire Deepgram (ears), Claude (brain) and Cartesia (mouth) together on our own server. LiveKit's free plan is the switchboard. We control every rule, including whether the agent is allowed to hang up on a gas call. It's the most work, though the old agent's code gives us a head start, and it's the cheapest per minute.
- **B. Retell does everything.** Our server only looks things up and books. It gets us to a working number fastest and sounds polished. But we can only turn Retell's dials. Our code can't stop it from hanging up or transferring; the most we can do is take away its ability to hang up at all.
- **C. Retell for the ears, mouth and switchboard, our brain.** Retell has a mode where it handles the phone and the voice, and our server decides what to say. We'd keep Retell's polished turn-taking and get control back, but we're still tied to Retell. This option is new since last time.
- **D. Bake-off.** Build A as the real product, and set up B in a few hours as the baseline A has to beat. Run both on the same tests plus 10 real calls each, keep the winner and delete the other folder. Most of the work (the tool server, the mock ServiceTitan, prompts and evals) is shared, so the extra cost is small.
- **Vapi and Pipecat:** I looked at both and passed. Vapi is another Retell. Pipecat is another LiveKit with less connection to Revin.

**My recommendation is D.** It's your "custom Retell-like stack with Retell as the baseline", and the result is measured rather than guessed.

Three things to know before you pick:
- **Phone number.** The 678 number was bought inside Retell, so it lives in Retell's own Twilio account. It can only ever point at Retell, and it's an Atlanta area code. In D it becomes the Retell test line, and a new NYC Twilio number (about $1.15 a month) goes to whichever stack wins. I don't have Twilio access, so you'd buy that number. Booking texts from it need a one-time business texting registration that costs about $20 and takes up to 5 business days, so that should start early.
- **No $50 LiveKit plan.** Our program runs on the Vultr server you already pay for, so it's always awake. Whether that server can handle 5 calls at once, and what happens to call number 6, is still being researched.
- **Backup isn't automatic.** Twilio only switches to a backup if LiveKit's phone connection is completely dead. If our program crashes while LiveKit stays up, calls won't move over on their own, so we need a health check that switches the number to the backup. That's in the plan.

The #5 pipeline readout is rewritten in plainer words and its early "decided" entry is undone ([docs/decisions/05-pipeline-and-models.md](docs/decisions/05-pipeline-and-models.md)). We'll pick it up after #4.

Three things are still running in the background: the capacity and cost research, the mock ServiceTitan build, and the decision-model test plan.

**Claude:**

The capacity and cost research is back and committed. It doesn't change my recommendation on #4, and I've folded its findings into the readout's hosting note.

- **Five calls at once is fine for the demo, not for production.** In a normal hour about 1 caller in 170 would hit a busy line. In a 3x surge it's about 1 in 4. Around 15 lines keeps busy signals near 1% even at 5x volume. The call volumes behind these figures are assumptions, because no public per-shop numbers exist.
- **Call number 6:**
  - In the demo, it hears the safety line, then goes to voicemail.
  - In production, it gets a short hold, then an offer to press 1 for a callback, with voicemail as the last resort.
- **The Vultr server is short on memory.** It realistically handles 2 to 3 calls at once rather than 5. That's an estimate until we load-test it. Moving it to 4 GB costs $20 a month, billed hourly. That's spend, so I'll ask before changing anything.
- **The server restarts itself for updates every morning at 4:30 or 5:30 a.m. New York time.** That comes from an [another client] project's update setting, so we'll leave it alone. Our program will restart automatically after each reboot, and a health check will alert you if it doesn't come back.
- **Cost, at an assumed 14,000 call minutes a month:**
  - about $648 for the recommended setup
  - about $388 for the half-budget setup
  - about $1,566 for running everything on Retell

  A missed call is worth about $165 to $570. That's [docs/cost.md](docs/cost.md), and it answers the owner's "what if we halved the budget" question.

The question waiting on you is still #4, in [docs/decisions/04-voice-platform.md](docs/decisions/04-voice-platform.md).

**Claude:**

I've committed the plan for testing decision models later: [evals/decision-models/PLAN.md](evals/decision-models/PLAN.md). It's a design only, and nothing has been run.

- **What it tests:** four questions on 480 labelled lines.
  - "Is a vulnerable person in the home" is the main one.
  - The others are "did the caller agree to the read-back", "is this a commercial caller" and "did they ask for a person".
- **What's compared:**
  - the launch setup: the fixed phrase list plus the main model
  - a separate Haiku call made alongside the main reply
  - OpenAI Decisions
  - Jev
  - Laya
- **How a winner is chosen:** a dedicated decision model only goes in if it beats both the launch setup and the plain Haiku call without adding a noticeable delay.
- **Cost:** about $0.08 for a quick run and $1.10 for a full run.
- **Your input later:** the pass bars are placeholders for you to confirm before the first full run.

The question waiting on you is still #4, in [docs/decisions/04-voice-platform.md](docs/decisions/04-voice-platform.md).

**Claude:**

The mock ServiceTitan is built and open as [PR #14](https://github.com/Joconnell04/summit-air-agent/pull/14). It isn't merged yet. All 62 tests pass, and the Postman collection's 101 checks run clean through Newman.

- **What it covers:** customers, memberships, equipment, jobs and appointments, dispatch, the call log, and the company profile.
- **Template vs Summit Air:** everything specific to Summit Air lives in `tenants/summit-air/`. A test proves that a copied tenant folder serves a different company.
- **Double booking:** the database itself blocks it, and a race test confirms only 1 of 8 simultaneous requests for the same slot wins.
- **Caveat on realism:** ServiceTitan's developer site wouldn't load for the build agent, so most field names are guesses. The README labels each one as known or assumed.
- **Stricter than the real thing:** the mock refuses to overbook a slot, while real ServiceTitan allows it.

A review agent is checking the PR against its brief now. I'll merge it once the review findings are fixed.

The question waiting on you is still #4, in [docs/decisions/04-voice-platform.md](docs/decisions/04-voice-platform.md).

**Claude:**

[PR #14](https://github.com/Joconnell04/summit-air-agent/pull/14) no longer conflicts with main. I merged main into its branch and kept both sides of the three conflicting files:

- `.gitignore`: both new entries are kept.
- `pyproject.toml`: both sets of test settings are kept.
- `DECISIONS.md`: the mock's entry is renumbered 003 so it sits after main's 002.

All 62 tests still pass, and GitHub now shows the PR as mergeable. The review agent is still checking it.

The question waiting on you is still #4, in [docs/decisions/04-voice-platform.md](docs/decisions/04-voice-platform.md).

**Claude:**

That comment on [PR #14](https://github.com/Joconnell04/summit-air-agent/pull/14) is only Codex's status banner saying its review is still running. It contains no feedback, so I haven't changed or replied to anything. If Codex posts actual findings, I'll fix them then. Our own review agent is still running too.

The question waiting on you is still #4, in [docs/decisions/04-voice-platform.md](docs/decisions/04-voice-platform.md).

**Claude:**

Fixing the Codex finding on PR #14: booking now refuses windows the company doesn't offer. Tests pass. Pushing and replying next.

**Claude:**

I fixed Codex's finding on [PR #14](https://github.com/Joconnell04/summit-air-agent/pull/14), replied on its comment and resolved it. Booking or rescheduling now refuses arrival windows the company doesn't offer, such as Saturday evening or Sunday morning. I added two tests, and the suite is now 64.

Our own review agent finished and asked for changes before merge. A background agent is fixing them on the branch now:
- Summit Air and HVAC details that were hard-coded in the shared code move into Summit Air's config. That covers tune-up names, the email domain, the main number, and the rule that rejects commercial customers.
- The README gets two honesty fixes. It will say which lock actually stops double bookings, and it will label two ServiceTitan web addresses as unconfirmed.
- The debug log reader gets input checks and stops crashing on a bad log line.
- The new code gets reformatted to the 100-character line limit.

I'll merge once those land and checks pass.

The question waiting on you is still #4, in [docs/decisions/04-voice-platform.md](docs/decisions/04-voice-platform.md).

**Claude:**

The mock ServiceTitan is merged into main ([PR #14](https://github.com/Joconnell04/summit-air-agent/pull/14)). CI passed, all 72 tests pass, and issue #13 is closed.

The review fixes were the last step before merging:
- **Template vs Summit Air:** details specific to Summit Air now sit in its own config folder instead of the shared code. That covers tune-up names, the email domain and main number, equipment names, the urgent-slot holds, and the rule that rejects commercial customers. Tests show a different company's config changes all of these. While moving the equipment names, the agent also fixed a bug that displayed "furnace" as "furnACe".
- **README:** it now states plainly that one database lock decides which of two simultaneous bookings wins, and the unique slot rule is a second safeguard behind it. A new test shows that a booking that fails can be retried with the same key once a slot frees up.
- **Log reader:** it now rejects a bad `limit` value and skips any log line it can't read instead of crashing.

The only thing waiting on you is #4. The readout is [docs/decisions/04-voice-platform.md](docs/decisions/04-voice-platform.md).

## 7. Jackson

Okay, now I'm reading through the revised decision 4 file. I like this plan. I think it looks really good. Let's make sure we get that phone number set up or like apply for it as soon as possible. Let's go with a New York City Twilio number. I like 212 as the area code. So use like my browser or something and handle that. I can load in more money if we need to do that to get that phone number right. And then we can keep that 678 number for retail and use that to evaluate option B. And then the new number would be for the Twilio Live Kit setup. So yeah, I'll load money into that. It should be good. Also, remember like the vault system that I've set up? I added the live kit API key and API secret, and the other stuff there also exists. I'll mint a new API key for Claude. Yeah, and I'll add that to the vault and share it with you here. Just make sure it doesn't end up showing that like I gave you the API key, because that's a bad look. The server restarting at 4:30 or 5:30 a.m., that doesn't concern me. Just make sure it's properly documented why that happens and when, and that we thought about it and that the impact is low, so it's okay. And also just approximate how many minutes that would be down for, and ensure that the plan accounts for a proper like health check so that we know that it's back online automatically.

As for like spending stuff, I just loaded retail with another $10. So there's $13 on retail AI. There's $30 on Twilio. There's basically 200 remaining in the free credits on DeepGram on the pay-as-you-go plan. I'm on the free plan for Cartesia with 20,000 model credits remaining and $1 voice agent dollars remaining. And I've got $0 credit balance on Ngrok. And the same server on Vulture with some money there. And if you think it would be worth the $20 to upgrade the server or to make just a new server for this project, let me know what you think there. I think for now, we don't have to scale the system to be able to handle 5 calls at once. Let's just be cognizant that we're documenting the limits. That way I can speak to it when I present. If he asks, I can just be like, you know, if you expect bandwidth to increase, we can always upgrade and that would encompass XYZ things, ABC time plan, and 1, 2, 3 changes to the numbers or whatever. Yeah, but I really don't want Vulture to be like bottlenecking the capabilities of the system. So let me know if you think I should just get another server or something. Also, I want the system to be able to switch between Anthropic, OpenAI, and Gemini models for the base LLM, like brain part. So make sure there's some like backend configuration for all those API keys. You can use the OpenAI one in the vault, the Anthropic Summit API key in the vault, and double check that the Gemini API key is in the vault and has some money on it too. Yeah, but just tell me if any of these other providers I need to like load more money on and then get started on that Twilio phone number thing. Let me know if you need to do any configuration on GitHub for so that you can properly use the features you need. I'm not sure if you are still planning on using GitHub issues and actions, but if so, let me know if you need anything from me.

summit anthropic api key:
[redacted]

other keys in the vault you can rely on:
livekit api key, livekit api secret, livekit websocket url, postman api key, twilio account SID, twilio auth token, cartesia api key, deepgram api key, ngrok access key, ngrok auth token, fluxion api key, retell api key, openai joco api key. there's $9 on openai. $25-150 in api credits for anthropic (should be the default brain llm provider option).

gemini api key is now loaded into the vault. and $70ish on Gemini API key available. Okay, so I've given you good bits to work on, I think, but let's keep this moving. So yeah, update documentation and then surface to me the next information and decisions you need me to make and stuff to speak on.

**Claude:**

All 13 keys came from the vault into a gitignored `.env`. Checking each one works and reading balances where the APIs allow.

**Claude:**

No 212 numbers are in stock on Twilio; only 917 and 929 have any. I'll ask you which to buy at the end. Recording decision #4 now.

**Claude:**

Now measuring how long the Vultr server is actually down when it reboots.

**Claude:**

I bought the new number: **(646) 624-0418**. 646 is the Manhattan overlay of 212. Twilio has no 212 numbers in stock, and the 917 and 929 numbers it listed all failed to buy with "not available". This one went through, on the same account as the [another client] receipts number, which I left alone. The $1.15 monthly charge will show up separately; the $30 balance hasn't moved yet. The 678 number stays with Retell for the baseline.

**Done**
- **#4 recorded** as option D: our own LiveKit stack is the product, and Retell on the 678 number is the baseline it has to beat. It's DECISIONS.md 004, and the issue is closed.
- **Keys:** all 13 project keys are pulled from your vault into a gitignored `.env`, and none of them is printed in the repo or any commit. Anthropic, OpenAI, Gemini, Cartesia and Retell each answered a test request.
- **Model switching:** `config/models.yaml` can now run the brain on Anthropic (the default), OpenAI or Gemini, one line to switch, with tests that keep the config consistent. The OpenAI and Gemini models are untested candidates until #5 is decided.
- **Server reboots, measured:** the server reboots only on days an update needs it, 5 times between Sep 5 and Oct 14, at 4:30 a.m. (5:30 a.m. in summer). It's down 10 to 12 seconds each time, and our services should be taking calls again within about a minute (an estimate until we see a real reboot).
  - An outside monitor alerts you if the server doesn't come back.
  - The watchdog also moves the number to the backup if our program is down while LiveKit is still up.
  - This is all in [docs/architecture.md](docs/architecture.md).

**Balances**
- **Cartesia: top up.** I recommend the Pro plan at $5 a month. The free plan's 20,000 credits are about 27 minutes of agent speech, which runs out during the bake-off. Pro gives about 133 minutes.
- **Fine as is:** Anthropic, Gemini ($70), OpenAI ($9), Retell ($13, about 118 minutes) and Twilio ($30).
- **Twilio:** texting registration costs about $20, so it may need a small top-up later.
- **Deepgram:** I couldn't read its balance because the key isn't allowed to see billing. About $200 of free credit is plenty.
- **ngrok:** it isn't needed if we get a new server.

**GitHub:** I don't need anything from you. Issues and the board stay as the tracker, and Actions runs the offline checks.

**Texting compliance:** a research agent is preparing a submission meant to pass the first time ([#15](https://github.com/Joconnell04/summit-air-agent/issues/15)). It covers which registration path fits, the most common rejection reasons, every form field drafted for you, and the privacy and texting-terms page that has to be live first. One constraint already: the registration has to be in your name as an individual. Registering the fictional "Summit Air" would be misrepresentation and gets rejected. I'll bring it to you when it's ready.

**What needs you now: [docs/decisions/16-server.md](docs/decisions/16-server.md).** The choice is the current shared server or a new one for Summit Air. I recommend a new 2 CPU, 4 GB server in Vultr's New Jersey data center for about $20 a month, billed by the hour. That gets rid of ngrok and any risk to [another client]'s client services, it sits next to New York instead of in Atlanta, and you can delete it after the interview. That's new spend, so I need your yes.

## 8. Jackson

Okay, now I'm reading your chat output. Good that you bought the number and are researching the compliance stuff for the campaign application. I really don't care about the phone number. I just want to make sure it's a New York City one. And it's totally fine that the retail number is a 678 Atlanta one. That does not matter. Okay, good that you recorded the decision for option D. Yeah, we don't need to think about the server reboots again for now. I'll upgrade the Cartesia plan to the Pro plan. And I'll top up a little bit more on Twilio. Deep Gramma is fine, and Grok I'll leave for now. Then give me a recommendation for the server option. If you think Vulture is best, let's do that. But if there's a more like price efficient option with equal or better capabilities, let me know. Like, give me a few backups. This shouldn't be like a major decision, but it should be documented in the decisions folder. So give me that stuff. I'll top up Twilio and get the Cartesia plan. And then let me know what I should do for the server, and then see my other notes below for decision 5. While you're working, I'm just going to give you more thoughts on the decision 5 markdown document. Something I'm confused and a little bit concerned about is that we're ensuring proper near real-time tool calling and checking for things like a booking availability system and their field service management or CRM backend. I'm sure it's somewhere, but let's just make sure we don't forget about the brain. Like, the underlying LLM needs a harness in and of itself that can pull data super quickly from the CRM, you know, handle scheduling and stuff like that. But yeah, right now I definitely think the 3 separate steps makes the most sense. I don't like option C. I just think that creates so many more potential points of failure. I definitely think we need gates implemented, such as no booking until the address is read back and confirmed by the caller and stuff like that. But I don't think we need a hybrid speech-to-speech model to do that. But somewhere in our like bake-off, let's make sure we also do a bake-off for a speech-to-speech model like OpenAI Real-Time and Gemini Live. Make that bake-off pretty small so that we don't burn a lot of credits, but it should just see how that goes. And eventually we should also have a bake-off of just like various models and versions of those models. Like Haiku versus Opus versus Sonnet and stuff like that. But that's for later, and that's for a bake-off that should be like properly designed at some point later, if it's not already. Yeah, for the side models, let's just do none right now. I do plan to implement it at least in one place, and that way when I present the system, it shows that I'm like up to date on this decisioning model architecture and am always thinking of different ways to like implement that type of stuff. Sorry, all of that I meant related to F, a dedicated decision model. We'll handle that later. But continually think of ways we could implement. And I think the thinking around that should be like, we identify a bottleneck and then we consider if a dedicated decision model would actually help circumvent that or like cut that bottleneck. We don't need to be trying to work in Jev just for the sake of working in Jev. We should be using it as a solution option, not a necessity. And then also in that bake-off, we can test if the second Haiku call beside each turn is truly helpful. And based on my understanding, that option E, a second Haiku call beside each turn, wouldn't necessarily be that different from a decision model, and it would take a lot longer. So in that bake-off, we could do a side model test between none, a second Haiku call, and a dedicated decision model. And for dedicated decision model testing, Jev, a post-trained Laya, and the OpenAI decisions one with their respective like special types. I know they're nuanced, so yeah, that's a consideration. Yeah, but definitely launch with no side model and build a slot for one. Don't focus too much on the multilingual stuff like Spanish accuracy of any decision model or something. That is all the cherry on the top that we can think about later. Okay, but I think once you analyze that and give me what I need to know, I think we'll be in a good place to decide on decision 5.

**Claude:**

Server readout is updated. Now revising #5 with your notes.

**Claude:**

**Server ([docs/decisions/16-server.md](docs/decisions/16-server.md)):** I recommend a new Vultr server in New Jersey: 2 CPUs and 4 GB for $20 a month, billed by the hour. If you delete it after the interview, it comes to about $5 to $10 in total (assuming about 10 days). The alternatives:
- DigitalOcean (New York) and Linode (Newark) are about $24 a month for the same size and need new accounts.
- Hetzner used to be the cheapest, but it raised prices twice this year and the small plans are being retired. It's also in Virginia or Oregon, not near New York.
- Usage-billed platforms like Fly.io or Railway don't save money here, because the voice program has to stay running all day.

To set it up, either add a Vultr API key to the vault and I'll create the server, or create it in the Vultr console:
- Cloud Compute, Regular Performance
- 2 vCPU, 4 GB
- New Jersey
- Ubuntu 24.04
- the `vultr_personal` SSH key

**#5, rewritten from your notes ([docs/decisions/05-pipeline-and-models.md](docs/decisions/05-pipeline-and-models.md)):** read it after the server.
- **Section 4, the brain's harness** (new). The model never reaches into ServiceTitan itself; our server does the work:
  - It looks up the caller's number before the agent says hello.
  - It gives the model about six small tools.
  - It keeps open slots cached so slot checks return in milliseconds.
  - It enforces the gates, such as no booking until the address and phone are read back and confirmed.
  - It puts time limits on every lookup, with a fallback line so the caller never hears dead air.
- **Option C** is now rejected for your reason: every part of the call has two of something, so there are more ways for it to fail, and the safety check would still only catch "gas" after the agent has started answering.
- **Side models:** launch with none and a slot for one. The rule for adding one is to find a real bottleneck first, then test whether a side model fixes it.
- **Bake-offs:** listed in the new [docs/bakeoffs.md](docs/bakeoffs.md).
  - A small speech-to-speech check of OpenAI Realtime and Gemini Live, a few dollars.
  - Side models: none against a second Haiku call against Jev, a post-trained Laya and OpenAI Decisions.
  - The model matrix (Haiku, Sonnet, Opus and so on), designed later.
- **Spanish** is left out of everything for now.

**Waiting after that:** the texting registration readout ([docs/decisions/15-texting-registration.md](docs/decisions/15-texting-registration.md)) is done. The short version: register as a Twilio sole proprietor in your own name, with "Summit Air" described only as a demo, because a fictional company as the brand gets rejected. It costs about $19.50 up front and takes roughly 1 to 3 weeks. Texts from the 646 number are blocked until it's approved. It also needs a public privacy and terms page to be live before you submit. Every form field is drafted. We'll go through it once #5 is settled.

## 9. Jackson

Actually, just so you know, I'm not gonna top Twilio. It has a minimum ad balance of $20, and right now there's $30. So I think we should be good there, but let me know if you think if all the testing would make it use more. If you think I'll use more, just tell me to top up and I will. And also tell me if we need any other subscriptions that would like really benefit this stuff.

I want you to handle the Vultr server stuff. API key is added to vault and is: [redacted]

see screenshot for config details and lmk if you need me to change anything or add an IP(s) to the allowlist for more seamless development. and just added another $20 to vultr account so feel free to use that. also only after you set up the proper vulture stuff, remove Bobbert from the [another client] pipeline and have a subagent handle that, and then double check that the [another client] pipeline is still running totally fine. I don't need an extensive report. I just want to make sure I didn't crash any client stuff on that existing server. Also, once you set up the Vulture server, let me know if you need me to like go grab you like an SSH key or something like that. And then while you do that, I'll start giving you more thoughts for number 5. And make sure that there's like somewhere in this setup for like a come back to later or something, or yeah, just so that when I tell you things like don't focus on multilingual or Spanish, but we can come back to that. Like that way that's documented, and that at the end of building like the V1, I can go back and make sure everything I said to come back to later is properly handled. And make sure you make that public privacy and terms page or something as well. Yeah. But yeah, we're going with Vulture, not the other options.

**Claude:**

Your Vultr account has $35.59 in credit, and the only server on it is labelled "epic-pipeline". Creating a dedicated SSH key and the New Jersey server now.

**Claude:**

The server is being created. While it boots, I'm recording decision #16 and adding the come-back-later list.

**Claude:**

Server is up at 45.77.105.247. Running the base setup in the background.

**Claude:**

While the server sets up, I'm reading the drafted terms page text from #15.

**Claude:**

The server setup is still running; I'm waiting for it to finish before I clean up the old [another client] server.

**Claude:**

While that finishes, I'm updating the hosting section for the new server.

**Claude:**

The new server and the public pages are both still building. I'll check them once each finishes.

## 10. Jackson

Okay, so I'm reading decision 5. I think I gave you everything you need in a previous message. I think we just start with A, we put the decision model testing as like part of the bake-off and also something to come back to later. Yeah, but read through like my previous notes on that, because I think that kind of lays out my decision and stuff pretty well. And then as for the brain's harness, I'm reading that right now. Make sure we have idempotency on the slots. And I think it would make sense to like abstract the, or not like abstract, but like to make sure that an agent can't go rogue and like delete a bunch of customer data on the thing. Like there should be some version of a gate or a check to ensure that the actions the agent is taking related to like the harness and touching like the CRM and the client side stuff isn't out of line. Like there should be checks run very quickly to ensure that somebody on one end of the phone can't just be like, delete all the data, and then it just goes out and does that because it was asked to. Essentially implement like a solid least privileges system with proper validation checks and protections against like prompt injection and whatnot. Doesn't have to be anything like too crazy or cybersecurity focused. It just should be like solid stuff to show that I thought of it. And let's also set up criteria for what we're looking on for the bake-off. And this applies to both the bake-off for this and the other bake-off for voice platforms, if we are doing that still. I just want to like set criteria and expectations or hypotheses like somewhat briefly up front. And that way we have something to interpret the results based on. And like we're keeping the bigger picture in mind rather than just testing everything and like making decisions randomly. Yeah. And then also just make sure that the process you're following for like updating these documentations and stuff like that are good and being tracked. You know, we should, somebody at the end of this project should be able to follow the reasoning to how we arrived at the general system design. I think you're mostly doing that, but I just want to make sure. So yeah, I think decision 5 should be good. So take that, surface anything you need me to speak to, and then keep it going.

**Claude:**

Recording decision #5 and adding the least-privilege checks to the harness design.

**Claude:**

Now the bake-off hypotheses and criteria.

**Claude:**

The new server's SSH stopped answering after the last setup step, while the web port is up. Rebooting it through the Vultr API.

**Claude:**

SSH is most likely refused because fail2ban banned my IP. My wait loop retried the `jackson` login before that user existed. The ban lifts after about 10 minutes, so I'm waiting for it.

**Claude:**

Decision #5 is recorded as DECISIONS.md 006. The least-privilege rules, the bake-off hypotheses and a design trail are written and pushed. I'm waiting for the new server's SSH to come back before cleaning the old agent off the [another client] server.

**Claude:**

Decision #5 is recorded, the new server is running, and the terms and privacy pages are live.

**#5 (DECISIONS.md 006):**
- The pipeline is three separate steps, with a right-sized model for each job and nothing large in the live call.
- There's no side model at launch, but there's a slot for one. A decision model only goes in where a bottleneck has been measured and the bake-off shows it fixes it.
- The side-model test (Jev, a post-trained Laya, OpenAI Decisions) is on the come-back-later list.

**Harness additions, in [docs/architecture.md](docs/architecture.md) under "What the agent is allowed to do":**
- The same slot can't be booked twice.
- The agent can only do what a phone intake person could, and only on the caller's own records. It has no delete or bulk tools, and every input is checked on the server.
- There are per-call limits, and the scheduling-system credentials only allow the calls the tools need.
- Every refused request is logged.
- A caller saying "delete everything" can at worst get the model to ask for a tool the server refuses.
- There are eval callers for prompt injection and for "delete everything".

**Bake-offs ([docs/bakeoffs.md](docs/bakeoffs.md)):** every bake-off is judged on one scorecard:
- safety gates hold, and a single failure rules a candidate out
- books correctly
- feels like a person
- reply delay a caller would notice
- handles interruptions
- cost per call

Each bake-off states its hypothesis and decision rule before it runs. The voice platform comparison is still on.

**Following the reasoning:** [docs/ai-process/design-trail.md](docs/ai-process/design-trail.md) lists every decision in order, with links to the research, readouts and logged rounds. Adding a row each time a decision lands is now a repo rule. [docs/later.md](docs/later.md) holds everything you've set aside, Spanish first. The phase plan has a step to go through it with you before V1 is done.

**New server:** `summit-air`, 45.77.105.247, in New Jersey.
- You have admin rights. Login is by SSH key only, and root login is off.
- The firewall allows only SSH and web traffic, and HTTPS is ready.
- Updates restart the server at 3 a.m. New York time (4 a.m. in summer).
- I don't need an SSH key or IP allowlist entries from you. I made a dedicated key, `~/.ssh/summit_air`, and the Vultr API key worked from here.
- One snag: SSH was locked out for about 10 minutes. The server's auto-ban tool blocked my own login retries during setup. This Mac is now exempt.

**Old agent on the [another client] server:** a subagent is archiving `~/summit` to your Mac, deleting it, and checking that [another client]'s processes, ports and site match what they were before. I'll tell you the result.

**Twilio:** please top up $20 before you submit the texting registration.
- About $21.50 goes in the first month: $4.50 for the brand, a $15 one-time review fee, and $2 a month for the campaign.
- The new number is $1.15 a month, and test calls and texts add only cents.
- The [another client] receipts number bills to the same account.

So the $30 would get down to about $5. No other subscriptions are needed beyond the Cartesia Pro plan you're getting.

**What needs you now: texting registration, [docs/decisions/15-texting-registration.md](docs/decisions/15-texting-registration.md).**
- The terms and privacy pages are live at https://joconnell04.github.io/summit-air-demo-line/, with your name and jt272004@gmail.com.
- I recommend starting option A today. That's a Twilio sole proprietor registration in your own name.
- You have to do the identity steps yourself (photo ID, a selfie, and a code texted to your personal mobile). I can't enter those for you.
- Approval takes about 1 to 3 weeks, so it probably won't land before the Nov 9 interview. The build therefore puts texting behind an on/off switch, and the owner view always shows the confirmation either way.
- Option C skips texting for the demo entirely.

**Claude:**

The old practice agent is gone from the [another client] server, and [another client] looks exactly as it did before.

- **Archive:** everything was saved to `~/Downloads/Claude/personal/revin-interview/vultr-summit-archive.tgz` (30 MB) before deleting. It includes the old agent's secrets file, so treat it as sensitive.
- **[another client] before and after:** the process IDs and open ports are the same before and after; only the uptimes advanced. The login page still returns 200, and the crontab still has only your fantasy job.
- **Leftover references:** none of [another client]'s code or config points at the old agent's folder or ports.

The texting registration (#15) is still the question waiting on you.

## 11. Jackson

Bam, bam, topping up. Twilio right now with another $20. So now there's $48.85 on it. Keep that in mind if necessary. And then point me to the right URL to handle the Twilio registration stuff and identity steps. Hopefully it will get approved more quickly. But if it doesn't, come up with a plan to get around that and still have a phone number that can be used.

**Claude:**

$48.85 covers registration (about $21.50 the first month) plus all the testing, so no more top-ups are needed.

**Where to register:** https://console.twilio.com/us1/develop/sms/regulatory-compliance/a2p-onboarding (Messaging → Regulatory Compliance → Onboarding). Twilio's walkthrough is the [Direct Sole Proprietor registration overview](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc/direct-sole-proprietor-registration-overview).

Order:
1. **Pick "Sole Proprietor."** You have no EIN for this.
2. **Create the individual profile.** It asks for your legal name, jt272004@gmail.com, your personal mobile (not a Twilio number), your address, a photo ID and a selfie. Use your exact legal name, because it has to match the terms page and everything after it.
3. **Register the brand.** Twilio texts a code to your mobile, and you have to reply within 24 hours.
4. **Stop at the campaign step and send me a message.** I'll set up the Messaging Service with only the 646 number and the opt-out replies, then give you the campaign fields to paste. All of them are drafted in [docs/decisions/15-texting-registration.md](docs/decisions/15-texting-registration.md) under "Fields to paste."

The terms and privacy pages are already live, so nothing blocks steps 1 to 3.

**If approval doesn't land before Nov 9:** registration only affects texts, so the 646 number takes calls today either way. The plan (now in the readout):
- **On/off switch for texts.** The agent still asks for consent and the server still records it. While texting is off, the confirmation shows on the owner view as "text queued, waiting on carrier approval" and sends automatically once approved.
- **Email backup.** If the caller gives an email, the same confirmation goes there.
- **In the meeting.** Show the queued text and say business texting needs carrier registration, which is filed and pending. That's a real rollout step a customer would face, so it reads as honest rather than as a gap.
- **Optional hedge.** A second, texts-only toll-free number can usually be verified in 3 to 5 business days for free. It costs $2.15 a month, Twilio discourages it for hobby senders, and texts would come from a different number than the one callers dial.

## 12. Jackson

confused on where to go on twilio

## 13. Jackson

[Image: source: /private/tmp/claude-502/-Users-jacksono-Downloads-Claude-personal-summit-air/b9412510-fcdf-41ee-9b05-4bb92cb06a7b/images/1.webp]
[Image: source: /private/tmp/claude-502/-Users-jacksono-Downloads-Claude-personal-summit-air/b9412510-fcdf-41ee-9b05-4bb92cb06a7b/images/2.png]

**Claude:**

You're one click away. On the second screen (Trust Hub → Registrations), click **A2P 10DLC Brands**, the first item. Ignore the "Regulatory compliance" page from your first screenshot; it's only for numbers outside the US and Canada.

On the A2P 10DLC Brands page:
1. Start a new brand registration and choose the **Sole Proprietor** type. You may need to answer "No" to "Do you have a Tax ID / EIN?" first.
2. It asks you to create or select a customer profile. Create one as an **individual**: your exact legal name, jt272004@gmail.com, your personal mobile, your address, and the photo ID and selfie if it asks.
3. Submit the brand, then reply to the code Twilio texts your mobile within 24 hours.
4. Stop at **A2P 10DLC Campaigns**, the second item on that same list. It only unlocks once the brand is approved. Tell me when it does and I'll set up the 646 number's messaging service and give you the campaign fields to paste.

If anything on the brand form doesn't match what's in [docs/decisions/15-texting-registration.md](docs/decisions/15-texting-registration.md), or Twilio shows an option I didn't mention, send a screenshot and I'll tell you which to pick.

## 14. Jackson

also did we already look into elastic SIP trunking for scaling to proper # of callers/agents or no and/or is that irrelevant?

also i already have a sole proprietor for the receipt agent thing. Double check the assignment brief, but I don't think they asked for any form of SMS texting. So I'm curious if it's possible to just use the number I already have for the [another client] thing and then just make it functionally a receipt agent for my freelance contract for SMS, but then functionally the phone number for voice for this take-home assignment. Is that possible, or should I just make a new account and do it? But I'm confused that won't help because I've already linked my ID, I think, but I'm not really sure. Let me know.

## 15. Jackson

[Image: source: /private/tmp/claude-502/-Users-jacksono-Downloads-Claude-personal-summit-air/b9412510-fcdf-41ee-9b05-4bb92cb06a7b/images/3.webp]

**Claude:**

**Short answer:** stop the registration and leave texting out of V1. The assignment never asks for texts; the closest requirement is "Book the appointment or confirm next steps with the caller", which the agent does out loud on the call.

On your options:
- **The [another client] receipts brand can't be reused.** As the warning in your screenshot says, an individual can have only one sole proprietor brand, and that brand's single campaign is registered for receipts. Sending Summit Air booking texts under it would break what was registered, and the risk would land on the [another client] client's texting.
- **Putting this demo's calls on the [another client] number** would mix client work with an interview demo for no gain. The 646 number already handles calls, and calls don't need any of this registration.
- **A new Twilio account** could in principle hold a second sole proprietor brand with the same ID. It would still take 1 to 3 weeks and might not land before Nov 9. Not worth it.

So: don't click Next on that form. In V1 the agent confirms the booking out loud and the owner view shows it. Texting moves to [docs/later.md](docs/later.md) and becomes part of the rollout story you tell Paul: before going live, Summit Air registers its own business texting brand with its tax ID, the normal path for a real company, and confirmations switch on. The public terms page and all the drafted fields stay ready for that.

**Elastic SIP Trunking:** yes, we covered it in explorations 003 and 004, and it's already the plan. It's how the 646 number sends calls to LiveKit, at about $0.0034 a minute, and how Retell could be a fallback on the same number. It isn't what limits how many callers we can take; none of our research flagged Twilio as a limit at this size. The caps we did find:
- LiveKit's free plan takes 5 calls at once.
- The new server handles about 5, an estimate until we load-test it.
- New one: Cartesia's Pro plan allows only 3 voice streams at the same moment. Agents only use a stream while they're talking, so 5 calls usually fit, but two or three agents speaking at once could briefly hit it. I'll measure that in the load test and note it in the capacity doc.

## 16. Jackson

Yeah, wait, I don't want texting to be a part of that. That wasn't mentioned in the assignment. I'm asking, what do we need for the Twilio phone number? And how can I get that, given that my current account, which already has money on it, is the sole proprietor of the [another client] phone number for receipts? So come on, think through this. And yeah, like I know I can always go back to using retail and the phone number provided from them, but I thought we said we'd try out Twilio or something.

**Claude:**

You already have the number. (646) 624-0418 is bought, active and voice-capable on your current Twilio account, sitting next to the [another client] receipts number. Nothing more is needed from you.

I sent you down the wrong path. The registration form is only for sending texts, and we're not texting, so you can close it. Your sole proprietor brand for [another client] doesn't affect this number at all: buying and taking calls on a Twilio number needs no registration.

What's left for the number is wiring, and I'll do it through Twilio's API during the build:
- **Point it at LiveKit.** Create an Elastic SIP Trunk, attach the 646 number, and send its calls to LiveKit's phone endpoint. On the LiveKit side, add a matching inbound trunk and a rule that hands each call to our agent.
- **Add Retell as the backup.** The trunk lists LiveKit first and Retell second, so the same 646 number can fall back to Retell. Retell's own 678 number stays separate as its baseline test line.
- **Cost:** about $0.0034 per call minute on the trunk, plus the $1.15 a month for the number. The $48.85 balance covers everything through the interview.

To clean up texting:
- I'll close #15 as "not needed, the assignment doesn't ask for texts".
- I'll remove the texting line from the plan and add it to `docs/later.md` as a rollout step.
- The public terms page is harmless to leave up. I'll delete that repo if you'd rather.

## 17. Jackson

Okay, so this is a good example of when you should just be more clear, saying, you sent me down the wrong path, and then surface me the next thing, you know? I shouldn't constantly have to ask for you to keep the progress going and tell me what I should speak to next. Also, just give me the explanation so I know what's going on related to the Elastic SMP trunk. And double check that you're not just doing that because I mentioned it and that that was part of the original plan to begin with. And in the end, just note that we're going to be using one single phone number. The reason we have one on retail and like the 678 number on retail and a different 646 number on Twilio is so that we can do an effective bake-off and then decide on one. You don't need to delete the public terms page. We can keep that in case we ever want to pull in text again. Yeah, just tell me what I should be looking at next and transcribing my feedback for.

**Claude:**

Texting is recorded as out of V1 and #15 is closed. Now refreshing the #10 triage readout so it's ready for you to read.

**Claude:**

You're right. I sent you down the wrong path on texting and then made you ask what came next. From here I'll end every reply by naming the next thing to read. Here's where texting landed:

- Texting is out of V1 and recorded as DECISIONS.md 007. The assignment asks the agent to confirm next steps with the caller, and it does that out loud.
- #15 is closed.
- The terms and privacy pages stay up in case texting comes back. It's on the later list as a rollout step.

**One number.** Decision 004 now says so explicitly. Two numbers exist only so we can call both systems during the bake-off: 678 rings Retell, and (646) 624-0418 rings our stack. After the bake-off there's one number, the winner keeps it, and the other is retired.

**Elastic SIP Trunk, and why it isn't just because you mentioned it.** It was already in the plan: exploration 004 recommended it before you asked, and the one-number note (C-38) was built on it.
- **What it is:** a direct line between your Twilio number and LiveKit. Twilio receives the phone call and hands the audio straight to LiveKit, where our agent picks it up.
- **The other way to connect them:** a small Twilio script on the number that forwards each call to LiveKit.
- **Why the trunk wins:**
  - It's about a third of the cost per minute ($0.0034 against roughly $0.0125, SOURCE: exploration 003).
  - It can list a backup destination if the first doesn't answer.
  - It supports transferring a call to a person later, which the script route doesn't.
- **What it doesn't do:** Twilio can't hang up a trunk call from its API. That's fine, because the hang-up and transfer rules live in our own code.
- **What it isn't:** the reason we can take only a few calls at once. Those limits come from LiveKit's free plan (5), the server (about 5) and Cartesia Pro (3 voices speaking at the same instant).

**Next for you to read and speak to:** [docs/decisions/10-considerations-triage.md](docs/decisions/10-considerations-triage.md), the table under "Context". For every consideration it proposes in, later or out, refreshed with today's decisions. My recommendation is option A: build what the six requirements and Paul's likely tests touch, and put the rest in the rollout story. Go row by row and flag anything you'd move.

After that comes call journeys version 1, the step-by-step path each kind of call takes. I'm drafting it in the background now, and it will have your open choices marked inline.

**Claude:**

Call journeys version 1 is ready: [docs/call-journeys.md](docs/call-journeys.md). It's logged as round 1 and pushed.

It opens with the routine every call follows: lookup before hello, an AI disclosure in the greeting, listening first, a safety check on every turn, read-back before booking, and "anything else?" before ending. Then come 11 journeys, each naming the server check that fires.

It marks six open choices with my recommendation:
1. **Vulnerable household, nothing urgent open today:** book tomorrow's first window and flag a person to try to move it up.
2. **Safety caller:** the agent only gives the safety instructions and flags a person; a person calls back later to offer a visit.
3. **Tenant with no heat in heat season:** one neutral line about the landlord's duty and 311. Book if they or the landlord can approve the visit; otherwise take details and flag a person.
4. **Commercial line:** an owner-occupied one- or two-family house, or a single co-op or condo unit, counts as residential. Three or more units, a property manager, or a business counts as commercial.
5. **Install and replacement quotes:** a comfort advisor calls back. No booked estimate in V1.
6. **After-hours urgent calls:** flag the on-call tech, and never promise a visit tonight.

**Read next:** the triage first ([docs/decisions/10-considerations-triage.md](docs/decisions/10-considerations-triage.md)), since it sets scope. Then the call journeys. For the journeys, answer the six "Open:" items and flag anything that sounds wrong for a real call.

## 18. Jackson

Okay, something I want you to make sure you include and document on properly is what would have to change, and like a general high-level plan on how we would do it to expand from a max concurrent call count of 3, which is bottlenecked by Cartesia Pro, and then document the other bottlenecks of that concurrency. And just document it so, like, you know, if the client were to expect to have a max of 5 like calls at once, we'd have a plan for scaling up to that for full rollout, which wouldn't change the stack and would just change what it runs on or something like that. I don't know. Figure that out. Okay. And then you said next for me to read and speak to is decisions 10. Put privacy as in, like the C30 privacy. That should be in. It shouldn't hinder our ability to like check logs and stuff in development. But there should be a retention policy properly implemented. Success measures should be tracked in this system and stuff like that, so that we can, you know, present in the meeting, which is, you know, a simulation of presenting this to an actual client. Decision models is something we'll come back to. Yeah, and the playbooks idea we'll come back to later. For surge mode, let's just cut that one, but it should pseudo be covered in what I was saying before about a rollout should be able to handle like a high number of calls at any one given moment in a proper scalable implementation, you know? Yeah, and then languages is out of scope for now, but we'll come back to that as well. But let's just try to scaffold this whole system and have it built, and then we'll actually come back to those later things. So yeah, option A with the changes that I just mentioned. And then did we ever go over decision 6 stuff? You're saying we did. But I don't remember doing that. So I'm just going to speak to it right now. Yeah, I think we should use our own harness. Yeah, we should set up like kind of like a cheap to do simulation system. Remove language about stuff like 2 days to build. We're doing this all today, bro. So for the, yeah, let's make sure we're using also the same simulation harness eval suite thing. So even though retail has its own simulator, let's make sure we're comparing apples to apples here. And yeah, let's make sure we have a proper eval framework implemented. Also speak to if you considered using LangSmith, LangGraph, or LangFlow, or whatever for stuff. I know that those were things mentioned in the job description for this position. So I'm curious if we ever considered using that for this demo and if it would help or not, or the rationale behind not doing it. Also update the language so that everything is like aligned. I still see mentions of Bobbert, which is fine, but just make it more clear that that was like a previous personal project, not like someone's name. Also, let's make sure that the eval suite, like the actual tests that are in it, are like realistic, you know, not just perfectly transcribed text, but things where somebody says or stutters or says the wrong thing and then corrects themselves, or says the wrong thing, then lets the AI talk to them and then corrects themselves, you know, be realistic and assign them different levels and build like different sets of those tests, you know, like this is a rigorous test, this is an easy test, you know, that way we can see, and like certain tests are for certain flows and they're properly labeled, so that way we can like customize the eval very quickly on the fly and then have a bake off between multiple setups pretty fast. So yeah, once you do that, then we can keep pushing to the call journey stuff if that's what's next.

**Claude:**

Triage is recorded as DECISIONS.md 008, every consideration now has a status, and #10 is closed. Writing the scaling plan next.

**Claude:**

Scaling plan is written. Next, revising the eval design. First a quick check on LangSmith's current free tier.

**Claude:**

Now the eval suite design, which the eval lane will build from.

**Claude:**

Now making every Bobert mention clear that it was an earlier personal project.

**Claude:**

**#10 scope is decided (DECISIONS.md 008).** It's option A with your changes:
- **Privacy is in.** A real retention policy: transcripts kept 30 days, consent and call records 12 months (assumed). Full logs stay available in development.
- **Success measures are in.** The system tracks them and the owner view shows them for the meeting.
- **Later:** decision models, playbooks and languages.
- **Out:** surge mode, because the scaling plan covers high call volume.

Every consideration now has a status, and the considerations step is ticked in the phase plan.

**Scaling ([docs/scaling.md](docs/scaling.md)):**
- **Today's cap is Cartesia Pro.** It allows 3 voices speaking at the same instant. A call only uses one while the agent talks, so 4 to 5 calls usually fit.
- **The other limits:**
  - The server handles about 5 calls, until we load-test it.
  - LiveKit's free plan allows 1,000 phone minutes a month, roughly 285 calls.
  - Deepgram, Claude and Twilio aren't limits at this size.
- **To get to 5 at once:** move Cartesia to Startup ($49/mo), load-test the server and resize it to $40/mo if needed, and move LiveKit to its $50/mo plan once traffic passes 1,000 minutes. That's about $45 to $135 a month more.
- **To get to 15 at once (a bad heat-wave morning):** Cartesia Scale, two servers, and LiveKit billed by the minute.
- **No code or stack changes** either way. Once every line is busy, the next caller hears the safety line on a recording and can leave a voicemail.

**Correction:** I said earlier that LiveKit's free plan caps us at 5 calls at once. That cap applies only to agents LiveKit hosts. We run our own worker, so the real limit is monthly minutes.

**Decision #6:** you're right that we never really went through it. I had recorded "own harness" from a one-line yes. It's now revised from your notes ([docs/decisions/06-eval-design.md](docs/decisions/06-eval-design.md), [evals/README.md](evals/README.md)):
- One harness scores both stacks. The Retell baseline runs through Retell's text chat, not its own simulator.
- Test callers sound like real people: they stutter, correct themselves straight away or only after the read-back, split phone numbers, and get mangled by speech-to-text.
- Every scenario is labelled by flow and by difficulty (easy, standard, rigorous, adversarial). A run like `make eval TAGS=flow:safety,difficulty:rigorous` or a bake-off across stacks is a single command.
- The "2 days to build" wording is gone.

**LangChain tools:** I hadn't weighed them before today; that's now in the readout.
- **LangSmith fits** as a viewer for eval runs: side-by-side bake-off comparisons and a trace of each test call. The free plan is 5,000 traces a month, and it stays out of the live call.
- **LangGraph is skipped.** LiveKit already runs the conversation and the server holds the rules, so it would add a layer and delay to the live call.
- **Langflow is skipped.** It's a visual prototyping tool.

**Bobert:** each doc now says on first mention that it was an earlier personal practice project, not a person. The copied evidence files are left unchanged, and their folder README explains the name.

**Next to read and speak to: [docs/call-journeys.md](docs/call-journeys.md).** Answer its six "Open:" items:
1. Vulnerable household with nothing urgent open today.
2. Follow-up for a safety caller.
3. Tenant with no heat.
4. Where the commercial line falls.
5. Install quotes.
6. After-hours urgent calls.

Then flag anything that sounds wrong for a real call.

## 19. Jackson

note i had a lot of typos because i was transcribing

okay yes add langsmith for the eval results viewer. langsmith api key is: [redacted] and is also stored in vault.  So go ahead and implement Langsmith appropriately. It should still be kind of like built around our custom eval mechanisms and stuff, but it should just be visualized in LinkSmith and properly documented there or something. Just giving that update documentation. 

Okay, now I'm going to read through the call journeys markdown file and just tell you my thoughts on that. Yeah, so just keep in mind that this system should work both if it's called or if it's calling somebody. But yeah, the lookup before hello, perfect. The greeting should still state that it's Summit Air, even if it's saying the name. So it's like, hi, this is Summit Air. Is this Maria calling? Or something like that. Yeah, listen first. If it's been a few seconds, then the AI can ask, you know, is everything all right? Or whatever, you know, make it realistic without being like too pushy. And I want the journeys to like be able to jump between them. So it's like if somebody triggers the safety journey, but then they continue to talk and it's realized that that's not the case, it needs a way to jump back and get off of that strictly safely journey or whatever. And make sure you ask questions in like a priority order. For example, you know, pets, that's something that's really not super relevant. Maybe, you know, ask that at the end or only ask if it's needed. Like you should not be asking questions like just because it could be helpful. Only ask questions like when it will be helpful, if that makes sense. I just don't want to flood the user with questions. Yeah, always have the readback gate too. And make sure that if the person's like, you know, the best phone number is the one I'm calling from, that the AI can actually read the phone number that's calling from and document that, and that it's not like some proxy phone number or something. And then allow or like enable the agent to use like semantic shorthands. Like if the person's like, you know, and use the phone number ending in 4024, and the AI agent sees like three available phone numbers and one of them ends in 4024, just be like, okay, yeah, cool, that one. Because it should be able to kind of connect those dots. And for the booking, try to make it be good. Make it not just, here's the three next 1 hour availability windows. It should be like, I have a lot of availability on Tuesday and Thursday, as well as Thursday morning. Let me know which of those works and I can give you the list of time slots available or something like that. But yeah, then confirm out loud for sure. Anything else for sure. Yeah, I'm just make sure everything's logged properly. I'm not going to read through every single call journey, but I'll continue giving some feedback. So for like no heat or no cooling, just make sure that when the agent says, I'm sorry, that's no fun, when did it stop? That it sounds like a human and is not super like body, and is that phrase, that's no fun, shouldn't be reused every single time. There should be like some variability, I guess. Don't immediately ask who's home, because that can come off as like creepy. So follow just a more like service-oriented flow. And, you know, don't hard code anything in about the area code of the phone number being mapped to the zip code. Some people might just have a different phone number. Obviously check if the zip is in the service area, but yeah, politely decline, maybe offer flagging to a supervisor or something, only if that's like common practice. Don't do that if it's not. Yeah, make sure proper questions are asked about like membership and stuff in like a respectable manner, given the person might be going through a sensitive situation. Now, and the question about, is everyone at home who's older, very young, or health condition? I don't know. Definitely look up like industry best practices for that type of thing, but that's just not sounding too right. It's, to me, that sounds abruptly like, should I care about your concern or not? And I want it to seem more inquisitive and human and personable, and let the answers decide the priority, but not to be like, give me a reason to make your case low priority or high priority, if that makes sense. And the stuff like, I've marked this as a priority, you know, give them more than that. Be like, I marked this as a priority and just notified the on-call service representative or something like that, as long as it's true, but just make them feel heard and stuff. Yeah, and for number two, I agree with your recommendation to book the first window. So the caller has something certain, and then flag a person, try to move it up, and then have some system in the back end be like proactively managing that. So that way, if somebody gets their call moved up, they get a call back or something like that. Make sure that the decision has no ability to call 911, like for the demo. Like, I don't want, yeah, that to ever happen. Make sure that agent doesn't just end the call without signing off in the safety situation. It should just be like, I recommend calling 911 immediately. You know, happy to stay on the line, but really think you should blah, blah, blah. Yeah, and then after the caller is safe, I agree with your recommendation that yes, someone here should offer a follow-up visit on a later call and say we'll treat it like super high priority or whatever. Make sure for the members that it like thanks them for being a member without being like too cliche. Like, I see you're on the comfort plan, just want to thank you for, you know, being a loyal customer and happy to get you set up some priority scheduling or something like that. Yeah, but I agree with basically all your recommendations here. Also ensure there's like some proper handling of like a gross error. Like, I'm thinking if somebody is in a place with spotty cell service and they keep calling back, the AI agent should at some point be like, hey, I see that we keep getting disconnected. Then be like, you want to call back later, something like that. Just for those gross errors. But yeah, your repeat caller number 7 logic is good as well. Make sure you're nice for the commercial callers too. Just be like, you know, just be like, unfortunately, us at Seminar, we only work on residential, so we're not the right fit for this situation. I can have a member of our team call you back. Actually, don't offer a call back, but yeah, you know what I'm saying. Also, make sure there's like some way we can include like company specific heuristics or information or context. For example, let's say that this hypothetical 40 person HVAC company in New York City had a slogan at the end. We should be able to add that in to some like markdown file or something somewhere, and that way it can use it. Or if the company wants their agent to like actually know specifics about certain HVAC machine models, like it should be able, that should be a possibility to load that in if they want that, to an extent at least. For the after hours, yeah, same spine. It says the office is closed, so yeah, it's good.


also also also, get a subagent to search through and mine blog posts from revin. https://revin.ai/blog Tell it to look for things that are realistic, especially for an HVAC company, and then see if it can like provide any recommendations or insights for things we should keep in mind when building out the system, types of issues, types of flows we're not acknowledging, stuff like that. Doesn't need to be a crazy amount of recommendations or anything. I don't, like, we still need to focus mainly on the assignment at hand, not these blog posts, but I'm curious if they provide any insight to like the real world that we can draw from.

**Claude:**

The LangSmith key works, and it's in `.env` from the vault (not from chat). Starting the LangSmith build and the Revin blog research in the background.

**Claude:**

Both are running. Now looking up how good call-takers ask about vulnerable people in the home.

**Claude:**

Writing call journeys round 2 from your feedback.

**Claude:**

**LangSmith:** your key works. I loaded it into `.env` from the vault, not from your message, so it doesn't appear in any repo file or in the round log. A build agent is adding the viewer on its own branch ([#17](https://github.com/Joconnell04/summit-air-agent/issues/17)). Our eval results stay the source of truth, and each run is copied to LangSmith as an experiment so bake-off setups sit side by side. It also works with LangSmith switched off, and it comes with offline tests and a section in [evals/README.md](evals/README.md).

**Revin blog:** a research agent is reading revin.ai/blog for real-world HVAC call patterns and flows we haven't covered. I'll bring you its findings, eight insights at most.

**Call journeys round 2 is done** ([docs/call-journeys.md](docs/call-journeys.md), logged verbatim):

- **Opening a call**
  - The greeting always names Summit Air: "Hi, this is Summit Air. Is this Maria?"
  - If the caller goes quiet for about 5 seconds, the agent checks in gently once.
- **Moving between journeys**
  - An unclear mention of gas ("my gas bill", "gas furnace") gets one check question: "Just to be safe, are you smelling gas right now?"
  - A clear no puts the call back where it was, and the false alarm is logged for review.
- **What the agent asks**
  - It asks in order of need: what's wrong, where, how bad, what the tech needs.
  - Pets, gate codes and parking are folded into one question at the end.
  - The area code is never used to guess location; the ZIP is.
  - Out-of-area callers are declined kindly with no supervisor flag, which is normal practice.
- **Phone numbers**
  - "The number I'm calling from" uses the real caller ID, read back to the caller.
  - "The one ending in 4024" picks the matching number on file.
- **Offering times:** the agent starts with the shape of availability ("Tuesday and Thursday afternoon, or Thursday morning") and gives exact windows once the caller picks a day.
- **Vulnerable households**
  - HVAC intake guides screen for at-risk people as standard. I found no authoritative script, so the wording is ours.
  - Once the problem is understood, the agent asks "Who's at home with it right now? Anyone who'd have a harder time with the cold?" The answer sets the priority.
  - The agent then says what it actually did, and only after the server confirms it: "I've marked this as urgent and let our on-call team know."
- **When nothing urgent is open today**
  - The agent books tomorrow's first window and flags a person.
  - A background job watches for earlier openings and calls the customer to offer one.
- **Safety calls**
  - The agent has no way to call 911; there's no tool for it.
  - It never hangs up on a safety caller. It signs off: "Please head outside now and call 911 from there. I'll stay on if you need me, but the safest thing is to go now."
  - A person follows up later with a top-priority visit.
- **Members and callers with spotty signal**
  - Members get a short, real thank-you.
  - Membership is asked only after the urgent part of the call.
  - After repeated drops: "Looks like we keep getting cut off, want me to have someone call you back?"
- **Commercial callers:** a warmer decline, with no callback offered.
- **Company knowledge:** `tenants/summit-air/knowledge/` holds Summit Air's slogan, plan wording, equipment notes and common questions as plain markdown (new C-41). Another company swaps the folder.
- **Calls Summit Air makes (new journey 12, C-40):** the same opening steps, only to people who called about this request, 8 a.m. to 9 p.m., one attempt.
- **Settled:** all six open items from round 1, as recommended.

**The one thing I need from you:** in journey 12, which calls V1 places automatically. I recommend:
- **Automatic:** the earlier-slot offer and the requested callback.
- **To a person:** the safety follow-up, because the gas leak may still be in progress.

Once you've answered, read round 2 and say "lock" or give more notes.

**Claude:**

The Revin blog research is in: [docs/explorations/005-revin-blog.md](docs/explorations/005-revin-blog.md). It's based on 22 of their 110 posts, and every figure in it is Revin's own claim. I added the five useful findings as open considerations (C-42 to C-46):

- **Kept bookings and kill criteria (C-42):** Revin measures booked jobs still on the schedule when the tech arrives, not just bookings made, and runs pilots with stop criteria agreed up front. Both fit our success measures and the rollout story.
- **After-hours thresholds (C-43):** each after-hours no-heat call ends as one of three outcomes: someone goes out tonight, the first morning slot, or a callback. Inside temperature and how fast it's falling decide which. I'd make those cutoffs Summit Air settings, marked as assumed.
- **Look-alike no-heat calls (C-44):** a heat pump in defrost, or a furnace that has locked itself out, can sound like no heat. I'd add test calls so these don't raise a false urgent flag. The agent won't walk callers through resets.
- **Callbacks with a time (C-45):** promise a specific time instead of "shortly", and show promised against completed callbacks on the owner view.
- **More call types (C-46):** second-opinion quotes, "isn't R-410A banned?", complaints and warranty requests, and cancellations (ask why, offer two new windows).

They also suggest words worth using with Paul: "a message is not a booked job" and "system of record". One question he may ask: how long call recordings are kept and whether they're used for training. Our retention policy already answers that.

I'll fold these into call journeys round 3 together with your next notes. The question waiting on you is still journey 12: which calls Summit Air places automatically.

**Claude:**

The LangSmith viewer is built as [PR #18](https://github.com/Joconnell04/summit-air-agent/pull/18). It has 37 new offline tests (114 in total), and a test run synced into LangSmith without duplicates. It uses one deprecated LangSmith call, so a review agent is checking it and naming the supported replacement before I merge. That follows your rule about deprecated calls.

The question waiting on you is still journey 12: which outbound calls V1 places automatically.

**Claude:**

That comment on [PR #18](https://github.com/Joconnell04/summit-air-agent/pull/18) is just Codex's banner saying its review is still running. It has no findings, so there's nothing to change or reply to. Our own review is also still running.

The question waiting on you is still journey 12: which outbound calls V1 places automatically.

**Claude:**

The review of [PR #18](https://github.com/Joconnell04/summit-air-agent/pull/18) found two problems, and a fix agent is working on the branch now:
- **Deprecated call.** It replaces the deprecated LangSmith call with the supported one and removes the code that hid its warning. The new call only looks back one day by default, which would quietly let a re-run upload duplicates, so the fix sets the start date explicitly.
- **Half-finished uploads.** It makes re-running an upload that stopped halfway fill in the missing scores, without creating duplicates.

Alongside those, it moves LangSmith out of the runtime dependencies, adds an "error" result separate from pass and fail, and checks scenario labels against the README's list.

I'll merge once checks pass.

The question waiting on you is still journey 12: which outbound calls V1 places automatically.

**Claude:**

Codex left two comments on [PR #18](https://github.com/Joconnell04/summit-air-agent/pull/18). One is the half-finished upload problem the fix agent is already handling. The other is new: every test call shows up in LangSmith as taking 0 ms. I've passed both to the fix agent, which will reply on and resolve both comments after it pushes.

The question waiting on you is still journey 12: which outbound calls V1 places automatically.

## 20. Jackson

Your recommendation for Journey 12 looks good. and good to implement the C42-46 stuff, just make sure you document the rationale and the blog posts corroborating the story. and im confused what you're asking by "Which outbound calls should V1 place automatically?" so rephrase and ill give an answer and then progress forward

**Claude:**

You already answered it. "Your recommendation for journey 12 looks good" was the answer, and that question was asking for exactly that, so nothing else is needed. Restated plainly: when Summit Air calls a customer back, which of those calls the system dials by itself and which become a to-do for a staff member. Settled as recommended:
- **The system dials itself:** "an earlier slot opened up" and "you asked us to call you back".
- **A person makes it:** the follow-up after a gas or carbon monoxide call.

Call journeys round 3 is committed: [docs/call-journeys.md](docs/call-journeys.md). The five Revin findings (C-42 to C-46) are in, each with its reason and the blog post it comes from, marked as vendor practice:
- **Owner view:** booking rate by call type, callbacks promised against callbacks done, and "booked and kept" (bookings still on the schedule when the tech arrives).
- **After-hours no-heat** ends in one of three named outcomes:
  - Tonight, if the on-call tech agrees: below 50 F inside, falling fast, or someone at risk.
  - The first morning slot: above 55 F and holding.
  - A callback at a named time.

  The cutoffs are Summit Air settings, marked as assumptions.
- **Look-alike no-heat calls:** a heat pump in defrost or a locked-out furnace doesn't by itself make a call urgent, and the agent never walks anyone through a reset.
- **Callbacks** always name a time, never "shortly".
- **More call types:**
  - A cancel gets "ask why, offer two new windows."
  - Second-opinion quotes and refrigerant or rebate questions are handled without prices or regulatory claims.
  - New journey 13 covers complaints and warranty requests, flagged to a manager with a summary.

The doc has no open items left. If you lock it, next is the spec (#3): the six requirements plus everything in scope turned into statements the evals can test. I'll draft version 1 for you to react to.

**Claude:**

The LangSmith results viewer is merged into main ([PR #18](https://github.com/Joconnell04/summit-air-agent/pull/18)). CI passed, all 128 tests pass, and #17 is closed.

- **No deprecated calls:** the deprecated LangSmith call is replaced with the supported one. The live re-sync ran with deprecation warnings turned into errors, so any deprecated call would have failed it.
- **Re-runs are safe:** re-uploading the same results twice left exactly 2 runs with 6 scores each, no duplicates. A run that stopped halfway gets its missing scores on the next upload.
- **One limit:** LangSmith only lets an upload look back 400 days, so it can't de-duplicate against runs older than that. That doesn't matter for this project.
- **Results format:** each test result is now pass, fail or error. Error means the simulator itself broke, so it never counts as a failed call. Labels are checked against the README's list. Test calls only show a duration once the harness records one, so nothing shows a false 0 ms.
- **The demo test experiment** (`livekit-fast-demo-demo-1`) is still in LangSmith under the `summit-air-scenarios` dataset. You can delete it there whenever you like.

The question waiting on you is still whether to lock call journeys at round 3 ([docs/call-journeys.md](docs/call-journeys.md)). Once it's locked, I'll draft the spec.

## 21. Jackson

Lock it

**Claude:**

Call journeys is locked and #2 is closed. Drafting the spec now.

**Claude:**

Now the assumptions in the brief.

**Claude:**

Call journeys is locked and #2 is closed.

Spec version 1 is ready for you: [docs/spec.md](docs/spec.md).
- **What it is:** 47 statements a test can check, grouped under the six requirements plus rules that apply to every call. Each one says how it's tested (code test, eval scenario tags, grader, or the live calls) and where it comes from.
- **Must-pass:** 10 are marked with an M. A single miss on any of them fails a run. They cover the safety lines and gates, no 911 tool, the read-back gate, idempotent booking, no commercial or out-of-area bookings, and least privilege.
- **Numbers to sanity-check:**
  - S-36 sets reply delay at under 1.2 s median on normal replies and under 2.5 s when there's a lookup or booking. That's assumed until the latency budget (#7) is set.
  - S-45 sets retention at transcripts 30 days, call records 12 months.

[docs/brief.md](docs/brief.md) now has the assumptions table, each with what changes if it's wrong:
- counties: Brooklyn, Queens and Staten Island
- hours
- arrival windows
- fees: none
- membership plan: "Comfort Club"
- on-call setup
- call volume
- after-hours cutoffs
- the commercial line
- the ServiceTitan mock
- no texting
- English only

**What to read and speak to:** the spec first, row by row. Flag anything wrong, missing or too strict, and say "lock" when it's right. Glance at the brief's assumptions too; any you'd change is a one-line config edit.

After the spec come the last three design steps:
- the latency budget (#7)
- the build briefs for each lane (#8)
- exporting the design transcript

Then the build starts.

## 22. Jackson

Okay, feedback for spec markdown file. It's good so far. I'll include some more feedback. One thing I do want you to change is I don't like how the membership plan is called Comfort Club. Let's just call it premium membership or premium member or a summit VIP or something like that. Actually, I don't like VIP, but maybe just some combination of premium and summit and membership, something like that. Not Comfort Club. That's weird. Yeah, make that decision on your own. If somebody wants to become a member, that should also be a flow. We don't need to re-go over it. I trust that you can handle implementing that and updating the documentation for like the user stories for that appropriately. For the fix safety lines, I think it's good that there's like some level of determinism in that, but make sure that there's some also variability. So that way, if somebody keeps saying the same thing about their carbon monoxide signal or something, that the agent does not repeat the same exact phrase verbatim multiple times in a row to the same person within the same, you know, few minutes or whatever. And then for S18M, there should be some capability of overriding that if the person on the phone is like, stop giving me the safety lines, just book the appointment. Like if they're demanding the appointment be booked, just book it for them. Obviously don't make that something that makes prompt injection easier, but you get what I mean. Ensure proper logging as well for like failed tool calls. Like this will make it easier to debug, but if somebody's like book an appointment and then it doesn't work for some reason, you know, the agent should follow the same thing. Be like, I'm not able to book it right now, but like I'll note for somebody to book it and follow up with a call later. Like it should be able to honestly be like, oh, it's not working right now, but I'll send a note to a human and they'll follow up or something like that. Because if like the booking database is down for a little bit, the agent should still be able to like do its own thing and essentially queue actions, if that makes sense. But think through that. That might be a little bit out of scope, but it might be easy to do. So think through it. Yeah, and the word gas is not an immediate trigger for a safety thing. Like if somebody says gas bill or gas furnace, like, or just gas, you know, it's not enough to be like immediately inquiring on if they're in an active safety scenario. For S28M about out of area zips, you should document those too, because if enough people are calling with out of area zips, you know, maybe that could eventually become a decision for leadership or management to start serving that market of previously out of area zips. Just something that should be like logged and recorded. We don't have to do anything with it now. And if they mention on the call regarding S29, if they mention, like the customer mentions a specific day or time, like morning, afternoon, night, at some point in the call, that should inform the first recommended set of availability options. But a lot of these are good. They're good. Also, just something I'm thinking about that I want you to like research lightly, but let's say that the customer is calling with a lot of background noise. How do we prevent the model from thinking it's interrupted and needs to stop talking every time background noise spikes? Maybe that's not a concern because the speech to text model won't translate that, but I'm not entirely sure. So just something to think about, something to document in considerations. Ensure proper do not call logging. Like if the person on the phone is saying, do not ever call me again. And previously it asked for a callback, just be like, okay, I'm going to respect your decision. But just so you know that the callback can't happen. I don't know, like figure that out nicely. But yeah, you get it. I can't really speak to the number of seconds for the S36 reply delays, but they sound good to me right now. And then if I give feedback later related to like delays and speaking or something, just know to come back to that and change that number. But good for now. Okay, take into account all of this and then consider that decided. And then what's the next thing? Is it brief MD? Figure that out.

**Claude:**

Renaming the plan to "Summit Premium Membership" in the mock ServiceTitan. Running its Postman checks now.

**Claude:**

Plan renamed. All 101 Postman checks and 128 tests pass. Checking how LiveKit handles background noise before writing up the spec changes.

**Claude:**

Spec is locked and #3 is closed. Drafting the latency budget, the next design step.

**Claude:**

The spec is locked and #3 is closed.

What changed from your notes:
- **Plan names.** The plans are now Summit Premium Membership and Summit Standard Membership, plus a Summit Legacy Plan closed to new members. That's applied in the docs and in the mock ServiceTitan; all 128 tests and 101 Postman checks pass.
- **Joining.** New journey 14: a caller who wants to join gets a sign-up lead and a callback at a named time. The agent never takes payment, and only offers the plan at the end of a routine call.
- **Safety lines.**
  - The first time they're said word for word; if the caller raises it again, a shorter approved version is used.
  - Once the lines are said and a person is flagged, a caller who insists can get the booking. That's logged as an override, and nothing a caller says can skip the lines themselves.
  - "Gas" on its own, a gas bill or a gas furnace now triggers nothing; only a described smell, leak, alarm or symptom does.
- **When a tool fails.** The failure is logged, and the agent says honestly that it can't book right now. The request is queued and retried without double-booking, and a person is flagged if it still fails.
- **Out-of-area calls** are counted by ZIP on the owner view, so demand can inform an expansion decision later.
- **Times the caller mentions** ("mornings are better") shape the first options offered.
- **Do-not-call.** It takes effect at once, cancels any pending callback, and the agent says so kindly.
- **Background noise.** LiveKit can require recognized words before it counts something as an interruption, and it resumes after a false one. That's in considerations (C-48), with noisy audio cases planned for the live calls.
- **Reply delay targets** stay as they are, with a later-list entry to revisit them on any feedback about delays.

I also updated the users and call journeys docs for the new plan name, the joining flow, the gas trigger and do-not-call, and logged the changes in the spec round.

On your question: [docs/brief.md](docs/brief.md) was already done as part of the spec (the assumptions table, with the new plan names), so nothing more is needed there.

**What to read next: [docs/latency.md](docs/latency.md) (#7).** It's a short skim and there's nothing you need to decide in seconds. It splits each reply's wait into stages, each with a source or marked assumed:
- phone network
- detecting that the caller has finished
- the model's first words
- the voice

It also covers where time is saved and how each stage is timed on every call. My recommendation: accept it as is. Real measurements from the first calls will replace the assumed numbers. After that comes the last design step, the build briefs (#8), which start the build.

## 23. Jackson

Cut out the Summit legacy plan. That just is creating confusion. And let's just have one membership category, a Summit standard membership. So cut out the Summit premium membership too. So it should be like, you know, net new callers that are in the area scope and then Summit members. And that's it. But tell me if the assignment said something that makes you want to do something differently, because maybe I'm missing something, but I don't want to overcomplicate this. Everything else looks good. Now I'm gonna read latency, markdown, and let you know my feedback. Make sure you come back to these assumed values so that we can actually see it in the right numbers based on what you measure. Also, I just want you to include some analysis of potential time savings from having something on-prem, just for a potential upsell of a hypothetical client. Nothing that should be like talked about a lot anywhere else, just a number for me to know. To be like, it would cost X to do ABC thing to get on-prem, which would reduce latency or time to talk by Y seconds. Just a small thing I want you to do. Does not have to be that deep. Yeah, the latency stuff looks good though. Also, feel free to implement some small level of intentional filler words that are like, okay, I'm going to check that now. Okay, I see dot, dot, dot, you know, but you see how I said, okay, I'm going to check that now. Like that phrase could be used when the model knows it's about to call a tool. And that way, by when the result of the tool call is served and process back into speech, there's very minimal waiting. I don't want that to be overused, because every time you ask something, it'd be like, okay, I'm checking, and then right to the next part. Like, I don't want that to happen. I just want that to be a possibility, especially for the longer tool calls. Also, just in your chat output, tell me what I could expect to see better and worse of if thinking were turned on these live profiles. And make sure you just have proper logging for latency stuff built into stuff. And into the eval suite, you know, if like changing a model or a prompt has a noticeable, meaningful, or just honestly any change to latency or stuff like that, that should be documented and be capable of being captured through the eval suite or something. So review everything I just said, implement those changes, give me for what I asked for, and then I'll look at your chat output and then just probably will tell you to approve it, and then we'll get to the next step.

**Claude:**

Cutting the mock to one membership plan. Postman passes; one test fails, checking it.

**Claude:**

Mock is down to one plan, with tests and Postman green. Now updating the docs to match.

**Claude:**

**Membership: one plan now, Summit Standard Membership.** It gets priority scheduling, two tune-ups a year, 15% off repairs and no after-hours fee. A caller is either a Summit member or not. I cut Premium and Legacy from every doc and from the mock ServiceTitan; all 128 tests and 101 Postman checks pass.

Nothing in the assignment argues for more than one plan. It only asks the agent to "determine if the caller is already a member," which is a yes or no. One plan fits it exactly. The only nuance I kept is a lapsed membership: the agent treats that caller as not a member but says it kindly.

**Thinking on the live profiles, better and worse:**
- **Better:**
  - Fewer slips on calls that juggle several rules at once, such as a renter with no heat whose landlord isn't reachable, or a borderline commercial caller.
  - Fewer wrong or out-of-order tool calls, and slightly smarter phrasing.
- **Worse:**
  - The caller waits before every reply while the model thinks. The published figure for Haiku 5.5 with thinking on is 8.8 s to the first word of the answer. That turns a 1-second pause into dead air, which is far past what people tolerate on a phone call.
  - Each call also costs more.
- **Where it does belong:** after the call (the technician summary) and in the eval grader, where nobody is waiting. The model bake-off can test a small thinking budget later to confirm.

**On-prem estimate:** running speech-to-text and the voice on a GPU server next to the phone line would save about 0.1 to 0.2 s per reply. That costs about $1,000 to $1,500 a month to rent the server, or $10,000 to $15,000 to buy one, plus quote-only enterprise licenses from Deepgram and Cartesia. All of those numbers are assumed. Moving the model on-prem too saves another 0.1 to 0.3 s but means giving up Claude for an open model. It's not worth it for Summit Air; it's an option for a very high-volume client. It's a short section in [docs/latency.md](docs/latency.md) and nowhere else.

**Bridge lines:** the agent says "Okay, let me check that now" only when a lookup takes longer than about 0.7 s, at most once every few turns, and never the same line twice in a call. It's a pre-recorded clip that plays while the lookup runs. That's the difference from the earlier practice agent, where the model spoke "one moment" as its own reply and pushed the real answer back 7 to 10 s. The bridge line stays only if the live calls show it shortens the silence.

**Latency logging and evals:** every turn logs its stage timings. Each eval run is compared with the previous run of the same suite, and delay changes per turn type are printed next to pass rate and cost. Any change over 100 ms or 10% is flagged, and it shows in LangSmith next to the earlier run. So a model or prompt that only slows the agent down still gets caught. This is new spec items S-37a and S-37b.

**Assumed values:** every assumed latency number gets replaced with a measured one after the first real calls. That's now on [docs/later.md](docs/later.md), and the latency doc says so up top.

If you approve the latency budget, the last design step is the build briefs (#8): one per build lane. After that the build starts.

## 24. Jackson

Okay, yeah, I agree with everything and accept and approve everything. One thing just to add to the later or to a future plan is like after the call, have a model with thinking. Produce a summary for the technician and make sure thinking is also used in the eval grader as well. But yeah, approve and then let's progress.

**Claude:**

Latency is approved, #7 is closed, and thinking is now on for the technician summary and the grader (129 tests pass). Writing the shared tool contract and the build briefs next.

**Claude:**

The latency budget is approved and #7 is closed. Your add-on is now in config rather than on the later list: the technician's after-call summary runs on Sonnet with thinking on (spec S-16), and the eval grader runs on Opus with thinking on. A test keeps both on, and every live profile stays off. DECISIONS.md 006 notes it.

**Last design step: the build briefs (#8).** These define the build, so they're worth a skim before anything starts.

- **[docs/tools.md](docs/tools.md), the shared contract.** It lists:
  - every endpoint the voice layer calls: call start, each turn, call end, health
  - every tool the model can call, with the server check on each
  - one reply format, always HTTP 200 with a sentence to say, so a refusal never becomes dead air
  - no tool that deletes, bulk-changes or dials an outside number

  Because every lane codes against this one page, they can run in parallel.
- **[Five briefs](docs/ai-process/briefs/), one issue each:**
  - **02 Tool server (#19).** Every gate, the safety check, priority, the tool-failure queue, do-not-call, logging and the Postman collection. Builds first.
  - **03 LiveKit voice agent (#20).**
    - The worker, prompts, turn handling and bridge lines.
    - The Twilio trunk to the 646 number.
    - The deploy to the new server, with a watchdog and health alerts.
  - **04 Eval suite (#21).** The harness, about 40 tiered scenarios, the grader, suites by tag, the delay comparison between runs, replay and the ratchet.
  - **05 Retell baseline (#22).** Small: Retell on the 678 number, calling the same tool server.
  - **06 Owner view and background jobs (#23).**
    - The dashboard shown in the meeting.
    - Earlier-slot offers, callbacks, retrying queued bookings and the retention job.
    - After-hours email alerts.

**Order:** 02 first, because the others depend on it. Then 03, 04, 05 and 06 run in parallel, each on its own branch with a reviewer check before merge.

Once you approve, the design phase closes. I'll export the design transcript excerpts (the last item on the design checklist) and start lane 02 right away. When it lands, I'll propose the other four as parallel sessions.

## 25. Jackson

Okay, some thoughts and questions for you to address. First, is there not a check membership tool? And if not, why not? Would that like speed that process up? I'm not really sure. Yeah, I feel like find customer should do that. Also, I just think it would be helpful if there could be like notes attached to a customer, like in the CRM. I'm thinking if someone's like had a note of like they prefer warm and friendly, you know, then warm and friendly intro should be used, or I don't know, something like that. But just an idea. Maybe you can throw it on the later markdown file, but I'm sure. Also, stuff like find last service date or find last call. I feel like the model should be able to, if it wants to like know that information too, which is just pretty structured queries to the CRM and other connected databases, right? Maybe it could also document like just free text notes. I don't know, like record note. I don't know. I want to make sure that this system is very versatile and good at handling a wide variety of requests while still holding true to its like underlying contract, if that makes sense. All of that feedback was for the tools markdown file. Now I'm going to read the briefs starting from 2. Yeah, okay. For the mock service Titan CRM and FSM, seed in at least like 200 fake customers with different data centered around like in New York or something like that, with some, just a few like institutions or commercial companies, and as well as a few residents that are like out of area zips. That way it's like a pretty solid database. That does not need to be super high quality or anything, just make it look like there's stuff already in there. That's all for that 01 service Titan thing. For the tool server, just take what I said about the different tools and stuff and make sure those are all properly implemented here, but like don't automatically do it. Answer my questions first and then I'll tell you if I want you to do something to this. Also update the documentation. I think some of these are a little bit deprecated. I'm not really sure. Make sure that the bridge line for the voice agent on LiveKit stuff and 03 brief, make sure the bridge line isn't gonna suck. Like it shouldn't interrupt itself to play a pre-recorded clip or something, I don't know. Make sure the pre-recorded clip is small too, like short. Also in your chat output, explain ratchet, replay, and general thinking I should have around what the tool server is and what customizations or improvements would mean changes to the tool server. Yeah, but these look pretty good. Make sure that the retail baseline is not too short of a brief, though. This will be part of the bake-off, so it needs to not be skimped. Okay, yeah. Do everything I said. I approve of everything, but still give me the chat output before you get started so I can approve one more time.

**Claude:**

The tool contract now has history and notes, and the stale texting wording is fixed. Updating the briefs next.

**Claude:**

I've updated the tool contract and the briefs from your notes. Nothing is built yet; lane 02 starts when you say go.

**Your tool questions ([docs/tools.md](docs/tools.md)):**
- **Membership check:** there's no separate tool on purpose, and you're right that `find_customer` should do it. Both the lookup before hello and `find_customer` return membership status and perks, open jobs, last service date, the last few calls and customer notes, all in one round trip. One richer lookup is faster than two calls.
- **Customer notes:** now in V1, not later. The agent reads the CRM's notes at the start of the call, and they shape how it acts. "Prefers a warm, unhurried pace" changes its manner, a gate code goes into the job, and "tenant, landlord approves" saves a question. A note can never get around a gate: "book without read-back" in a note does nothing.
- **Last service, last call, older history:** the first lookup already has the recent ones. For anything older, the new `get_customer_history` reads past jobs, calls or equipment, only for the customer already matched on this call.
- **Free-text notes:** the new `add_note` tool. It's append-only, so the agent can't edit or delete notes, and the server stores the text as plain text, never as an instruction.
- **Versatility versus the contract:** the read tools only reach the caller's own records, and writes can only add, never change or delete. Any new tool has to pass three tests: evals show the agent needs it, it touches only the caller's own records, and the server can check its arguments.

**Brief changes:**
- **02 (mock ServiceTitan):** at least 200 residential customers around the three counties, about 5 commercial accounts (a property manager, a restaurant, a school, an office, a co-op board), and about 5 residents with out-of-area ZIPs. It also adds customer notes and a few seeded examples.
- **03 bridge line:** it only plays while the agent is silent, so it never cuts the agent off. Each clip is under about 1 second. If the lookup answers mid-clip, the reply waits for the clip to end instead of talking over it. A test uses a deliberately slow tool.
- **05 Retell, rewritten as a full brief:** Retell has to be set up as well as it allows, or the bake-off means nothing. It gets:
  - the same prompts, tools and Claude model where Retell offers it, plus Retell's own-brain mode as a variant
  - the same safety gating, with honest notes where Retell can't match it
  - tuned turn-taking
  - the same traces, timing and cost as our stack, side by side
  - an adapter so the same eval harness drives it
- **Stale wording:** removed leftover texting mentions in four docs. Users now hear the confirmation said back, and the on-call person gets email alerts.

**How to think about the tool server:** it's the company's rulebook and the only door to their systems. The voice side (LiveKit or Retell) just hears, talks and asks. Every fact and every "you're not allowed to" lives in the tool server, so it decides whether a booking goes through, what counts as urgent and what the agent may say it did. That's why a model swap or a voice-platform swap doesn't change behavior. What changes the tool server:
- **Changes something:** a new rule, a new kind of booking, a new gate, a new system to connect, or a new piece of customer data. Each means a server change with tests.
- **Changes nothing there:** tone, wording, voice, model choice. Those live in prompts and config.
- **A new company:** a new tenant folder, plus a new adapter only if they don't use ServiceTitan.

**Ratchet:** a one-way floor that stops quality from quietly slipping. Before every commit, `make check` refuses the change if the test count went down. After a paid eval run, it also refuses if any must-pass case failed, the pass rate dropped below the recorded floor, or the prompts or scenarios changed without a fresh run. You can raise the floor, never lower it by accident.

**Replay:** a way to re-run a real call against the current agent. It takes the caller's side of a recorded call, plays it against today's prompts and tools, and shows where the agent now does something different. It answers two questions: did my fix change what happened on that call, and did a change break something that used to work.

Say go and I'll start lane 02, the tool server, on its own branch. When it merges, I'll propose lanes 03 to 06 as parallel sessions.

## 26. Jackson

Go

**Claude:**

**Tool server build (lane 02)**: starting a separate session that builds the tool server on its own branch from brief 02 and opens a pull request.

**Claude:**

Lane 02 is running in its own session: [Build the tool server (lane 02)](#local_957e5bd2-7115-404f-aad0-29b8c9d57265). Meanwhile I'm closing out the design phase.
