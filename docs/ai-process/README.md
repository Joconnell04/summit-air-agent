# How this was built with AI

Built with Claude Code, mostly by voice through transcription. The structure is meant to make sure nothing said is lost and every AI change can be traced.

- **Sessions by phase.** Setup, design, build (parallel lanes), test and tune, demo prep. Each new session starts from a prompt kept in `briefs/`.
- **Co-authoring for the documents that define the product.** Claude drafts, Jackson reacts by voice, Claude revises; each round is logged in `coauthoring.md` with the feedback verbatim and the commit.
- **Decisions.** Jackson's calls are prepared as short readouts in `/docs/decisions/` and recorded as one entry each in `/DECISIONS.md`, linked to the discussion that produced it. `design-trail.md` lists them in order so the reasoning can be followed end to end.
- **Set aside on purpose.** `/docs/later.md` holds everything deferred, walked before V1 is done.
- **Side research.** `/explore` runs a background research agent with a fixed template; results in `/docs/explorations/`.
- **Agent loops with verification.** Build agents work on branches from a brief. A pre-commit hook runs the fast tests, CI runs everything on each pull request, a ratchet blocks a lower test count, and a read-only reviewer agent checks the change against its brief. Two failures escalate to a needs-decision issue.
- **Transcripts.** Exported from the design sessions into `transcripts/`.
- **Considerations.** A running list in `/docs/considerations.md`, added to by voice with `/consider` and triaged into scope during design.
- **Phases.** `phases.md` is the route; Claude proposes each next step and Jackson says go.

## Finished branches

A build or fix branch is kept as a tag once its pull request merges: `archive/<branch>` (for example `archive/build/tool-server`), then the branch is deleted, so the branch list shows only work in progress and every lane's history stays on GitHub under Tags.
