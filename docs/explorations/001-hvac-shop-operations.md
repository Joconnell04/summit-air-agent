# 001. How a 40-tech residential HVAC shop runs

Issue: #11. Labels: SOURCE (link in section 6), ASSUMED. No MEASURED numbers; nothing here was run.

## 1. Question

Who works in a residential HVAC shop of about 40 technicians, who answers the phone and who is reachable after hours, what do callers ask for, how do bookings, memberships and commercial inquiries work, and what does each person judge success by? Answer so `docs/users.md` names the real people and what each wants.

## 2. Why it matters to Summit Air

`docs/users.md` has four staff entries (owner, dispatcher or office staff, technician, on-call person) and marks the on-call rotation as ASSUMED. The brief and spec will be tested by Paul playing the owner (`docs/assignment.md`), and an owner is persuaded by the roles and numbers they recognise. The research also sharpens three build rules: who approves non-routine jobs (spec: approval by reply), what a technician needs in job notes, and how membership is recognised.

## 3. Options (implications for docs/users.md)

These are not mutually exclusive; each is an edit to the doc, not a build.

| # | Implication | Effort | Risk |
|---|---|---|---|
| A | Name roles by function, not title: call-taker (CSR), dispatcher, service manager, on-call tech, comfort advisor, owner. At this size the CSR and dispatcher are separate people, and the dispatcher reports to the service manager (SOURCE: ServiceTitan HVAC team lists them as separate roles; job listings put the dispatcher under the service manager). Headcount: ASSUMED 5 to 6 CSRs and 2 to 3 dispatchers, 1 service manager, 3 to 5 comfort advisors. Only anecdotes support it: SOURCE ACHR owner has five to six CSRs at $10M revenue (technician count not given); forum shops report 5 to 7 office staff for 20 to 25 techs. | Low | Staffing is a guess; label it ASSUMED and let Paul correct it |
| B | State the after-hours model: ASSUMED an on-call technician holds the phone for a week at a time and a manager or dispatcher decides "tonight or morning." SOURCE: the four common set-ups are a rotating on-call phone, software AI, a live answering service, or AI answering (Carly, Oct 2026). Tonight: no heat in cold weather, no cooling in a heat wave, water leaking from the air handler, burning smell. Gas smell or CO alarm goes to 911 or the gas company, never a booking. The agent's output is a short text, not a dispatch. | Low | If Summit Air already uses an answering service, the agent replaces or sits ahead of it; ask |
| C | Add the comfort advisor as a recipient. Replacement and estimate calls are sales leads, not repair slots; advisors work from pre-qualified leads and are judged on close rate and average ticket (SOURCE: job listings). Add a note that membership attaches to the home, not the person, so an adult child calling for a parent must be matched by address (ASSUMED, from how field-service systems store customers and locations). | Low | Adds a booking type (estimate) to the spec; scope decision |
| D | Give each role a success measure from industry practice, so the owner view and evals map to something real: owner (booking rate, after-hours capture, member attach rate), dispatcher (no overbooked board, fewer rescheduling calls), technician (job notes that need no call-back). Figures in the table below. | Medium | Vendor-sourced benchmarks; present as context, never as a promise |

Key figures for A to D:

| Figure | Value | Label |
|---|---|---|
| Typical call booking rate, all trades | about 42%, can reach 90% (ServiceTitan claim) | SOURCE |
| HVAC call booking rate, June 2022 | 38%; 59% for 25+ techs; 24% for under 5 | SOURCE |
| Booking rate, shops over 50 techs / 10 or fewer | 58% / 30% | SOURCE |
| Booking rate after 6 p.m., large / small shops (2022) | 21% / 9% | SOURCE |
| Off-hours share of residential HVAC calls | 14.1% (June peak), 9.8% (October low) | SOURCE |
| First heat wave vs a normal summer day | calls +20%, jobs +25%, revenue +90% (first), +20% (fifth); heat-wave average, 2019 to 2021 | SOURCE |
| Cold spell vs a normal winter day | same-day calls +20%, jobs booked +30%, average ticket +30% (2018 to 2021 data) | SOURCE |
| Jobs per tech per day / first-time fix | 3 to 5 / about 80% | SOURCE |
| Service agreement attach rate, high performers | 30 to 50% | SOURCE |
| HVAC customer retention / plan-holder retention | 66% / 80%+ (plan figure is a vendor claim cited second-hand) | SOURCE |
| After-hours call fee, Long Island company | $249 flat (diagnosis plus first hour) vs $189 daytime | SOURCE |
| Summit Air's own fee, windows, membership terms | unknown | ASSUMED |

What the research could not support. There is no reliable published split of calls into repair, maintenance, estimate and status; Housecall Pro's published split is revenue share and covers about 11% of jobs. Do not put percentages in users.md; order callers by season instead. No source says how residential-only shops handle commercial calls; ASSUMED practice is to screen early, decline politely and refer or take a message (the doc already says this). Commercial work differs in contracts, packaged units and technician skills (SOURCE: ServiceTitan commercial vs residential). Membership perks in the sources: priority scheduling, two tune-ups a year, 10 to 15% off repairs, waived diagnostic or overtime fee. How a shop flags a member in its system was not found; ASSUMED lookup is by phone number to customer to location to active membership.

## 4. Recommendation, and what would change it

Do A, B and C now, and D as a short "what each person judges success by" line per role. Keep the doc to roles and what they look for; keep numbers in this exploration. Three rules the research supports for the spec: the agent never states a fee unless Summit Air has configured it; a job summary follows the ACHR pattern (what the customer is experiencing, recent related work, access, expectations already set); and the agent never promises a membership perk it cannot see.

This changes if Paul says Summit Air already has an answering service, no comfort advisors (replacements handled by technicians), or a combined CSR and dispatcher desk.

## 5. Open questions for Jackson

1. Does the demo owner persona get named staff (a dispatcher and an on-call tech), or stay generic?
2. Is "40 technicians" service-only, or does it include install crews?
3. After hours, should the agent only text the on-call person, or may it page them directly?
4. Should the agent state the after-hours fee if configured, or always leave pricing to a person?
5. Are replacement and estimate calls booked by the agent (needs an estimate slot type) or routed to the office?

## 6. Sources

- ServiceTitan, field service metrics, 28 Nov 2025: https://www.servicetitan.com/blog/field-service-metrics
- ServiceTitan, call booking rate data report, 25 Oct 2022 (June 2022 data): https://www.servicetitan.com/blog/data-call-booking-rates
- ServiceTitan, call trends in the trades (no publication date; data May 2025 to Jul 2026): https://www.servicetitan.com/guides/call-trends-in-the-trades
- ServiceTitan, summer after-hours call spike (2025 data): https://www.servicetitan.com/toolbox/state-of-the-trades/trends/hvac-summer-after-hours-call-spike
- ServiceTitan, heat wave data, 7 Apr 2022: https://www.servicetitan.com/blog/hvac-revenue-heat-waves
- ServiceTitan, cold weather data, 4 Nov 2021: https://www.servicetitan.com/blog/hvac-cold-weather-impact
- ServiceTitan, HVAC team roles, 18 May 2025: https://www.servicetitan.com/blog/hvac-team
- ServiceTitan, HVAC service calls guide (call types, intake questions): https://www.servicetitan.com/blog/hvac-service-calls
- ServiceTitan, commercial vs residential HVAC, 22 Dec 2024: https://www.servicetitan.com/blog/commercial-vs-residential-hvac
- ServiceTitan, dispatching playbook (dispatcher pains, undated): https://www.servicetitan.com/guides/ai-automation-playbook/dispatching-most-stressful-job
- ServiceTitan help, capacity planning (job types, business units, arrival windows): https://help.servicetitan.com/docs/capacity-planning
- ACHR News, Heck, office and field alignment, 20 Jul 2025: https://www.achrnews.com/articles/164923-bridging-the-gap-between-office-and-field-how-to-align-csrs-dispatchers-and-technicians-for-better-job-outcomes
- ACHR News, Merritt, CSR headcount anecdote, 28 Aug 2024: https://www.achrnews.com/articles/155123-can-ai-replace-my-customer-service-representative
- Carly, HVAC answering service options and triage rules, 7 Oct 2026 (vendor blog): https://www.usecarly.com/blog/hvac-answering-service/
- All Island Comfort, emergency fee page, updated 7 Sep 2026 (contractor marketing, one company): https://allislandcomfort.com/guides/costs/emergency-hvac-service-cost/
- ApplauseHQ, membership programs, 2026 (vendor blog, cites Smart AC and Contracting Business): https://www.applausehq.com/blog/hvac-membership-programs
- Housecall Pro, HVAC repair economics, Mar 2026: https://www.housecallpro.com/wp-content/uploads/2026/03/032026-HCP-Trades-Quarterly-Pulse_HVAC-Repair-Economics.pdf
- Comfort advisor job listings (One Hour, AireServ): https://one-hour-careers.careerplug.com/jobs/3108845
- ServiceTitan community, CSR and dispatcher staffing threads (anecdotal): https://community.servicetitan.com/t5/The-Hotline/CSR-DISPATCHER-ROLES/m-p/31858

Tools: built-in WebSearch and WebFetch only; no Exa or Firecrawl calls, no paid calls.
