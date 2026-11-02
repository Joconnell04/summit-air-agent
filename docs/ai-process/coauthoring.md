# Co-authoring log

Rounds on the documents that define the product. Each round: the feedback as spoken (verbatim from transcription), what changed and why, and the commit.

<!-- Template
## <doc> r<N>
Feedback (verbatim):
> ...
Changed:
- ...
Commit: <sha>
Status: open | locked
-->

## users r1
Feedback (verbatim):
> (none; version 1 drafted from docs/assignment.md, docs/considerations.md and from-other-projects/README.md)
Changed:
- First draft: nine caller types, cross-cutting caller traits, four Summit Air staff roles, and a ranking for when needs conflict.
Commit: 5660e98
Status: open

## users r2
Feedback (verbatim):
> Okay, so I'm just speaking and here's some feedback and considerations and stuff for the user's markdown file. So firstly, just do a little bit of background research on how these HVAC companies operate. We need to know their business, to know their users. You know, what does an on-call person do? Are they just like the person answering the phone? Do they know the HVAC people? You know, it's a 40-person company, so you can make some assumptions around that, but make sure we really know the different people involved on this. Also think through if we should implement some form of like playbook layer, you know, or like tool call thing or something, like a playbook. So that way it's like, if there's a surge in this zip code and a phone number from that zip code is calling, you know, maybe we know not to try to upsell them on that call because we know it's probably going to be related to a surge. I'm not sure if that's the best idea, but just something to consider and ideate on. Yeah, like vulnerable household. I like your wants and worries for all of these. That's a good way to do that. But also through this, you know, maybe take some notes or whatever on ways we can actually start to capture these things, like vulnerable household. Like, are there keywords that we can expect a vulnerable household will say that deterministically would mean we change our playbook or approach or something or label as priority? You know, I say there's probably not too many like 100% deterministic things we can build in, but maybe there's like a sentiment score or something like that that we can build in without breaking the latency budget. Not really sure. Another thing to ideate on. I'm not saying it just to tell you what to do. I'm saying it to just surface an idea I'm having. And make sure to ask around, are there vulnerable people? Like, don't make that weird. There needs to be some good like language use, like normal language. And make sure we're not implementing some like potentially damning system for stuff related to three, the safety caller. I.e., there should be like proper checks to ensure that we're not like hanging up on a safety caller or something like that. I don't know. Make sure you simplify language too. For example, for maintenance scheduler, you say, in a surge, these jobs yield to urgent ones. That is confusing wording and makes no sense to the reader. Definitely members expect to be recognized to get the plan's perks. And there should be a proper tracking, acknowledgement, and like playbookization around like repeat callers. Like if somebody's calling like 5 times within the same minute, like that's something that should be properly tracked and applied within the conversation or something. Yeah, so for commercial stuff, yeah, just make sure you filter them out properly. Don't add them in. Actually, for commercial callers, we should be tracking that a company called in case it ever becomes useful. We just shouldn't be like making it a big deal or spending a lot via tool usage on that, like, or whatever. But if anybody calls, we should be tracking something around that. Yeah, for number 9, not a customer call. That's good considerations to have. Make sure your handling for other languages is solid too, without requiring the need for like a million more agents or anything. Yeah, so anyway, take into account all the feedback. It's not too much. Make sure you do proper research on the industry and what these actual different stakeholders and people look for, you know, things like common requests for HVAC companies, especially like those in New York and stuff like that. Yeah, and then next. So do that and then slash next and lock
Changed:
- Staff section rebuilt from research (explorations 001, 002): call-taker, dispatcher, service manager, on-call technician and comfort advisor named, each with what they judge success by. The on-call person is a technician on a weekly rotation, not the person answering the phone.
- New section on how the agent tells callers apart: server facts, fixed phrases, and a score that runs beside the turn so it adds no delay. The server picks a playbook from these (C-32), e.g. no upsell for a caller in a surge ZIP.
- Each caller gets "How the agent knows". Vulnerable household gets the plain-words question and the phrase examples.
- Safety caller: separate gas and CO scripts from official wording, and checks so the end-call and spam paths can't drop a safety call.
- Repeat callers: calls linked by number, a dropped call resumes where it stopped, repeated calls with no progress go to a person.
- Commercial: one-line lead saved with the normal end-of-call record, no extra lookups.
- Languages: one agent, Spanish live, other languages get a callback.
- New caller type for replacement and install quotes (NYC Clean Heat questions); renter section grounded in the NYC heat law.
- Plain wording throughout, e.g. the surge line for tune-ups.
Commit: d9d04b7
Status: locked

## users r3
Feedback (verbatim, given after the r2 lock):
> I'm going to give you more feedback and stuff, but just another consideration to throw on to the ideas or consideration stuff is if we could use Jev or Laya or a decisioning model at any point without breaking the latency budget to make the system better. That's just something I want to throw out there. Yeah, and regarding what I said about like a zip code based surge, I'm not really confident that's like how that really works, but that was just an idea to throw down. So every time I have an idea to throw down, it doesn't mean just go and implement verbatim what I said or based on your interpretation. It's just like look into stuff and whatnot. But I do think Jev or decisioning model would be a good way to show the interviewer that I'm up to date on those types of things and am constantly looking for the best ways to do stuff. Yeah, make sure that the fix list of phrases to raise priority isn't like overly ambitious and stuff. And make sure that the safety caller wording is not too weird or like overly formal. Should still sound like a human, properly explain things without going overly too technical, stuff like that. Yeah, and regarding the repeat callers, I think that's like just a good point to approach this work with in general is like item potency. So keep that in mind. And this system shouldn't be overly specific to New York City, but like obviously it should be somewhat specific. Like it should have proper context on each of the boroughs and neighborhoods and stuff like that, if it makes sense. But it shouldn't be like mentioning state programs unless it's like super relevant.
Changed:
- ZIP-based surge playbook taken out as a design; C-32 now says it is an unverified idea.
- The fixed list is cut to gas, CO, smoke or burning, and medical equipment like oxygen. Age, babies and illness go to the score or a decision model (C-35).
- Safety scripts rewritten as two short lines a calm person would say, with the agent ending the call so the caller can get outside.
- Idempotency stated for repeat callers and made a general rule (C-34).
- Service-area context comes from config; state programs mentioned only when they change what the caller does (Clean Heat line removed, C-36).
Commit: 2c8e355
Status: locked

## call-journeys r1
Feedback (verbatim):
> (none; version 1 drafted from docs/users.md, docs/considerations.md, the #10 triage proposal and DECISIONS.md 001 to 007)
Changed:
- First draft: a shared routine every call follows, eleven journeys with the server check that fires in each, and six open choices marked inline with a recommendation.
Commit: f0ccc61
Status: open
