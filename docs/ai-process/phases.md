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
- [ ] Ideas-round build: rules by stage (#56), trade layer and local names (#57, #58), carbon monoxide check

Exit: every lane merged through review, scoreboard running, go/no-go passed or the fallback chosen.

## 3. Test and tune

- [ ] Eval loop until the scoreboard holds
- [ ] Model and prompt matrix
- [ ] Human test pass, notes logged
- [ ] Bake-off result recorded in DECISIONS.md
- [ ] Walk docs/later.md with Jackson: each item done, scheduled after V1, or dropped with a reason

## 4. Deploy and demo prep

- [ ] Hosting: always-on server and worker, backup number, failure alerts
- [ ] Owner view, runbook, planted-bug drills
- [ ] Rehearsal with a skeptical owner
- [ ] README: number, scoreboard, diagram, what's cut

## 5. Handover

- [ ] Repo shared with the Revin team, number sent, 24 hours before the session
