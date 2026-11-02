# 10. Which considerations are in scope for the demo build

Issue: #10. Status: ready (refreshed after decisions 004 to 007)

## Question

Each item in docs/considerations.md gets one status: in (built before the demo), later (goes in the rollout plan Jackson presents), or out. This sets what the spec covers.

## Options

### A. Proposed triage below
Everything that touches the six requirements, safety or call quality is in; things that only matter once real staff and real volume exist go to later. Eight items are later, none are out; four are already settled by a decision.

### B. Leaner
Also move C-04 (owner view) and C-18 (repeat callers) to later. Faster to build, but the owner view is a strong demo moment and repeat callers are a likely Paul test.

## Context

The demo is judged on whether the agent meets the requirements, handles the unexpected, and whether the cuts are explained well. "Later" items aren't lost: they become the rollout story ("here's what we'd add before going live"), which the assignment asks for. The risk on the other side is building too much and having a shaky live call.

| Item | Proposed | Why |
|---|---|---|
| C-01 Four parts | in | The system shape |
| C-02 Mock FSM that looks real | in | Every lookup and booking depends on it |
| C-03 One adapter to the FSM | in | Cheap now, and it's the answer to "does this work with ServiceTitan?" |
| C-04 Owner and dispatcher view | in, simple | Shows flags and bookings live in the demo; read-only list, no editing |
| C-05 Residential vs commercial | in | Requirement 2 |
| C-06 Membership | in | Requirement 3 |
| C-07 What the technician needs | in | Requirement 4 |
| C-08 Booking | in | Requirement 6; confirmed out loud and on the owner view, no texts (DECISIONS.md 007) |
| C-09 How priority is judged | in | Requirement 5 |
| C-10 Confidence | in | Low-confidence urgent calls round up |
| C-11 Flagging a human | in: owner board + email alert to on-call. Later: warm transfer, texts | No real staff to transfer to in a demo; no texts in V1 |
| C-12 Safety script | in | Highest stakes |
| C-13 NYC heat rules | in, one line | Tenants are about half of two-family homes |
| C-14 Caller state | in | Paul will push |
| C-15 Special assistance | in | Prompt and pacing, little code |
| C-16 Language | later | Not a deciding factor for V1 (docs/later.md) |
| C-17 Who decides for the household | in | Needed to book a renter correctly |
| C-18 Repeat callers | in | Likely test; mostly server lookups |
| C-19 Relevance guard | in | Likely test |
| C-20 Spam and wrong numbers | in | Cheap |
| C-21 Prices | in | Never quote; fee only from config |
| C-22 Sounds like a person | in | Judged directly |
| C-23 Reading back | in | Earlier practice agent lesson |
| C-24 Latency and interruptions | in | Judged directly |
| C-25 Disclosure | in | Cheap, and a likely question |
| C-26 Surge mode | later | Needs real volume data; for the demo, calls stay short and urgent slots are held |
| C-27 Business and after hours | in | Changes who gets the flag |
| C-28 Swappable models and prompts | in | Already set up |
| C-29 Hosting | in (phase 4) | The demo number must stay up |
| C-30 Privacy | later | State a retention policy in the README; build it after go-live |
| C-31 Rollout and success measures | later | Presented in the meeting, not built |
| C-32 Playbooks | later | Unverified idea; revisit after evals show where calls go wrong |
| C-33 Asking who's home, naturally | in | Prompt wording plus eval lines |
| C-34 Idempotent by default | in | Earlier practice agent lesson |
| C-35 Decision models | decided | None at launch, slot built, bake-off when a bottleneck shows (DECISIONS.md 006) |
| C-37 A template, not a one-off | in | docs/template.md; tenant config already used by the mock ServiceTitan |
| C-38 One phone number | decided | One number after the bake-off (DECISIONS.md 004) |
| C-39 Least privilege for the agent | in | docs/architecture.md (DECISIONS.md 006) |
| C-36 Local context, lightly | in | Service-area config |

## Recommendation

Option A. It builds everything the six requirements and Paul's likely tests touch, and turns the rest into a credible rollout plan; if the build lanes slip, C-04 (owner view) is the first to move to later.

## Research notes

Tenant share from docs/explorations/002-nyc-callers.md (SOURCE: NYCHVS 2023). Everything else is a judgment call against docs/assignment.md.
