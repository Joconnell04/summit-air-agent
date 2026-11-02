# 005. What Revin's blog tells us about real HVAC calls and rollouts

Researched 2026-11-02 with WebFetch only; no paid calls. Revin is a vendor, so every number below is SOURCE: Revin blog (vendor claim, not checked at origin). Side input; the assignment stays the focus.

## 1. Question

Does Revin's blog describe call types, caller behavior, failure modes, rollout practice or vocabulary that our docs (users.md, call-journeys.md, considerations.md) miss?

## 2. Posts read

The index lists 110 posts (mostly roofing, remodeling, plumbing, vendor comparisons). I read 22. Dates are post headers; page metadata shows 2026-11-02 on all, probably a re-crawl date. Links are under https://revin.ai/blog/ (slug shown).

- `hvac-after-hours-no-heat-calls` (Oct 2): four-fact triage, three outcomes.
- `hvac-ai-call-transfer-to-human` (Sep 1): what escalates, what a handoff carries.
- `hvac-ai-answering-service-pilot` (Sep 18): 30-day pilot scorecard.
- `hvac-callback-queue` (Sep 15): why promised callbacks die.
- `hvac-same-day-cancellations` (Sep 23): rebook, confirm, backfill.
- `hvac-booking-rate-vs-answer-rate` (Jun 8): "effective conversion".
- `hvac-second-opinion-calls` (Oct 14), `hvac-refrigerant-recharge-calls` (Nov 2), `hvac-heat-pump-rebate-calls` (Sep 30), `hvac-home-inspection-calls` (Oct 28): niche HVAC call types.
- `hvac-membership-capture-on-inbound-calls` (Aug 6), `hvac-maintenance-plan-renewal-calls` (Aug 18), `hvac-spanish-speaking-callers-revenue` (Jul 8).
- `ai-intake-that-books-into-servicetitan-hvac` (Jun 22), `hvac-summer-dispatcher-bottleneck` (May 28), `hvac-shoulder-season-call-center` (Aug 27), `hvac-missed-calls-summer-revenue-leak` (May 19).
- `ai-answering-service-soc-2-compliance` (Aug 12), `csr-team-after-ai-home-improvement` (Jul 27), the Vertex rollout announcement (Jul 1), plumbing triage (Sep 11) and roofing call audit (Sep 24), read for method.

## 3. Insights

1. **After-hours no-heat ends in one of three named outcomes, with thresholds.** Ask indoor temperature, who is home, gas or CO signs, system behavior. Outcomes: send someone tonight, first morning slot, or self-fix with a 20-minute callback. Tonight if under 50 F or falling fast, or an infant, elderly person or medical need; morning if above 55 F and holding. Any after-hours fee is disclosed only after triage. Covered? Partly (call-journeys.md journeys 1, 2, 11): no thresholds, no fee. Recommend: make the cutoffs config values marked ASSUMED, and name the outcomes in journey 11.
2. **Two look-alike no-heat calls.** A heat pump in defrost restarts within about 10 minutes; a locked-out furnace may restart after a 5-minute power-off. Covered? No. Recommend: never walk callers through resets (gas appliance, liability); add one eval case and one knowledge-file line so a description of steam at the outdoor unit does not raise a false urgent flag.
3. **Promised callbacks are where trust leaks.** With no owner or timer, a callback rolls to tomorrow. Revin tracks promise rate, completion rate and a 30-minute target, and says to commit to a specific time. Covered? Callbacks exist (journeys 7, 11, 12) but journey 11 says "shortly" and the owner view has no completion count. Recommend: a concrete time window after hours and "callbacks promised vs done" on the owner view (wording change via `/round`).
4. **Their pilot is the rollout story.** Baseline 30 days of after-hours data, run 30 days after hours, weekly review of about 20 random calls plus every handoff and hang-up, scripted test calls (gas smell, member tune-up, Spanish speaker, no-heat emergency), kill criteria and a penalty-free exit written first, then 90 days. Primary metric: booked jobs still on the schedule when the technician arrives. Covered? C-31 has shadow mode, then after hours, then all inbound, with answer, booking, priority recall and handoff rates; no kept-booking metric, kill criteria or exit. Recommend: add both to the rollout slide. Expect a Spanish test call; later.md defers Spanish, so have the callback answer ready.
5. **Escalation categories we lack.** Revin sends complaints about finished work, pricing, credit or warranty exceptions, and "get me a person" to staff, with caller, history and reason. Failure modes: never transferring, transferring too readily ("an expensive phone tree"), callers repeating themselves. Covered? "Wants a person" yes; complaints and warranty no. Recommend: one complaint case that flags a person with a summary, plus a handoff log in the debrief.
6. **Answer rate hides booking rate.** Effective conversion is answer rate times booking rate; the loss is answered calls where nobody asks for the booking. Vendor figures: emergency 70%+, maintenance 80%+, replacement price-shopping 40% to 55%; the 38% HVAC average is credited to ServiceTitan's 2022 report (not read at origin). Covered? users.md names booking rate. Recommend: owner view shows booking rate by call type, not one blended number; do not present Revin's benchmarks as ours.
7. **Call types we do not list.** (a) Second opinion: caller holds another firm's replacement quote; capture the recommendation, failed part, system age, whether heat works; never criticize the other firm; running systems go to a comfort advisor. (b) "Isn't R-410A banned?": quote no price, book a diagnostic with a leak check, ask system age. (c) Heat pump rebates: capture fuel, utility, owner or renter, ZIP; only the advisor states a dollar amount. (d) Home-inspection repairs: ask who is calling and who pays first. Covered? Journey 9 bans price and rebate promises; (a), (b) and the capture fields are absent. Recommend: add (a), (b), (c) to journey 9 and the knowledge file with two eval cases; skip (d), rare for NYC co-ops. The agent should not state regulatory facts; they change monthly.
8. **Cancel calls are a rebooking chance.** Ask why, offer two specific new windows before ending, log the reason; confirm the day before with a question; offer freed slots to a standby list. Covered? Journey 7 allows one cancel per call and no more. Recommend: add "ask why, offer two windows, log the reason" to journey 7; the day-before confirmation and standby list need texts and outbound calls, so they go to later.md.

## 4. Words and framing worth borrowing

- "A message is not a booked job." Their demo test: after the call, is there a real job on the board, or a note someone still has to type? Our mock ServiceTitan can answer that live.
- "Disposition" for how a call ends; "booked and kept" as the success measure, not "calls handled".
- "System of record" for where bookings land; "the 80%" for routine calls the agent takes; "the transfer nobody designs" for handoffs.
- Rollout words: baseline, 30-day after-hours pilot, kill criteria, penalty-free exit.
- Owner question to prepare for: Revin says it trains on each customer's own recordings and says nothing on retention periods. Our retention policy (DECISIONS.md 008) is a clear answer. Do not quote Revin's own results ("500+ appointments in two weeks"); the posts do not explain how they were measured.

## 5. Sources

All read in full on 2026-11-02; header dates in section 2 (SOC 2 and Vertex: Aug 12 and Jul 1, 2026).
- https://revin.ai/blog (index)
- https://revin.ai/blog/hvac-after-hours-no-heat-calls
- https://revin.ai/blog/hvac-ai-call-transfer-to-human
- https://revin.ai/blog/hvac-ai-answering-service-pilot
- https://revin.ai/blog/hvac-callback-queue
- https://revin.ai/blog/hvac-same-day-cancellations
- https://revin.ai/blog/hvac-booking-rate-vs-answer-rate
- https://revin.ai/blog/hvac-second-opinion-calls
- https://revin.ai/blog/hvac-refrigerant-recharge-calls
- https://revin.ai/blog/hvac-heat-pump-rebate-calls
- https://revin.ai/blog/ai-answering-service-soc-2-compliance
- https://revin.ai/blog/vertex-service-partners-rolls-out-revins-ai-voice-and-sms-agents-across-its-national-roofing-portfolio
- Remaining posts: same base URL, slugs in section 2.
