# Phases

The route through the build. Claude drives it: after each step closes, Claude checks the next step and says "Unless there's anything else, next is ...". Jackson says go, adds something, or redirects. Check a box when its exit condition is met.

## 1. Design (one voice-driven session)

- [x] Users: `/round users` until locked (#1)
- [x] Call journeys: `/round call-journeys` until locked (#2)
- [x] Considerations: walk docs/considerations.md, mark each item in, later, out or decided (#10)
- [x] Spec: the six requirements plus in-scope considerations as testable statements; assumptions into docs/brief.md (#3)
- [x] Voice platform: explore, bake-off plan, decide (#4)
- [x] Pipeline shape and model profiles (#5)
- [x] Eval design: scenarios, scoring, tooling (#6)
- [x] Latency budget (#7)
- [x] Build briefs, one per lane (#8)

Exit: spec locked, the three decisions in DECISIONS.md, briefs merged. Export the design transcript excerpts into docs/ai-process/transcripts/. Done: docs/ai-process/transcripts/design-session.md.

## 2. Build (parallel sessions, one per brief)

- [x] Mock FSM, with tests (brief 01, #13)
- [x] Tool server, the integration layer (brief 02, #19); first, the others code against docs/tools.md
- [x] Voice agent on LiveKit, prompts, phone line (brief 03, #20)
- [x] Eval suite and grader (brief 04, #21)
- [x] Retell baseline (brief 05, #22)
- [x] Owner view and background jobs (brief 06, #23)
- [x] First end-to-end call on a test number
- [ ] Live go/no-go, 10 calls (#9)
- [x] Ideas round, decided after the build and before testing (DECISIONS.md 019 to 025)
- [x] Ideas-round build: rules by stage (#56), trade layer and local names (#57, #58), carbon monoxide check (PRs #63 to #65)

Exit: every lane merged through review, scoreboard running, go/no-go passed or the fallback chosen.

## 3. Test and tune

- [ ] Eval loop until the scoreboard holds. Done bar (DECISIONS.md 119): routine booking, no heat with an elderly person, and gas each pass about 10 scripted runs with no safety errors; the rest becomes known issues in the debrief
- [ ] Model and prompt matrix
- [ ] Human test pass, notes logged
- [ ] Bake-off result recorded in DECISIONS.md
- [ ] Walk docs/later.md with Jackson: each item done, scheduled after V1, or dropped with a reason

## 4. Deploy and demo prep

- [ ] Hosting: always-on server and worker, backup number, failure alerts
- [ ] Owner view, runbook, feasibility questions ("how would you do X"), kept outside the repo
- [ ] Rehearsal with a skeptical owner
- [ ] README: number, scoreboard, diagram, what's cut
- [ ] Repo front door (DECISIONS.md 117): shaped as the deployment repo an FDE leaves for Summit Air and the Revin team, not a template product
  - Done: root down to a few areas: the agent (Retell config and prompt), the server (tool server and mock), evals, deploy, docs; process and history one level down, moved and linked, nothing deleted. Retired stacks and arms in `voice/archive/` and `retell/archive/`, the live agent in `retell/socket/`, lessons from earlier builds in `docs/history/`
  - Done: README for the reviewer: the six requirements, each mapped to where it is enforced and how it is tested; one architecture diagram; the number to call; a "debug a call" section for the live debugging part of the session
  - A light run-it-yourself path: `.env.example`, `make setup`, `make demo-local` (server and mock with seeded data plus a scripted text call), so a reviewer can try it without Retell
  - Scale story as a talking point, not a build: the customer's config already lives in `tenants/summit-air/`, so the next HVAC customer is a new folder

## 5. Handover

- [ ] Repo shared with the Revin team, number sent, 24 hours before the session
