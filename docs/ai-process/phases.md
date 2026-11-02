# Phases

The route through the build. Claude drives it: after each step closes, Claude checks the next step and says "Unless there's anything else, next is ...". Jackson says go, adds something, or redirects. Check a box when its exit condition is met.

## 1. Design (one voice-driven session)

- [x] Users: `/round users` until locked (#1)
- [ ] Call journeys: `/round call-journeys` until locked (#2)
- [ ] Considerations: walk docs/considerations.md, mark each item in, later, out or decided (#10)
- [ ] Spec: the six requirements plus in-scope considerations as testable statements; assumptions into docs/brief.md (#3)
- [ ] Voice platform: explore, bake-off plan, decide (#4)
- [ ] Pipeline shape and model profiles (#5)
- [ ] Eval design: scenarios, scoring, tooling (#6)
- [ ] Latency budget (#7)
- [ ] Build briefs, one per lane (#8)

Exit: spec locked, the three decisions in DECISIONS.md, briefs merged. Export the design transcript excerpts into docs/ai-process/transcripts/.

## 2. Build (parallel sessions, one per brief)

- [ ] Integration layer and mock FSM, with tests
- [ ] Voice agent and prompts
- [ ] Eval suite and grader
- [ ] First end-to-end call on a test number
- [ ] Live go/no-go, 10 calls (#9)

Exit: every lane merged through review, scoreboard running, go/no-go passed or the fallback chosen.

## 3. Test and tune

- [ ] Eval loop until the scoreboard holds
- [ ] Model and prompt matrix
- [ ] Human test pass, notes logged
- [ ] Bake-off result recorded in DECISIONS.md

## 4. Deploy and demo prep

- [ ] Hosting: always-on server and worker, backup number, failure alerts
- [ ] Owner view, runbook, planted-bug drills
- [ ] Rehearsal with a skeptical owner
- [ ] README: number, scoreboard, diagram, what's cut

## 5. Handover

- [ ] Repo shared with the Revin team, number sent, 24 hours before the session
