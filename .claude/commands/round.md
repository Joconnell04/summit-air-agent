---
description: Revise a high-touch doc from spoken feedback and log the round. Use "/round <doc>" or "/round <doc> lock".
argument-hint: <users|call-journeys|spec|...> [lock]
---

Target doc: $ARGUMENTS

1. If the doc has no content yet, write version 1 from docs/brief.md, earlier locked docs and docs/history/from-other-projects/README.md, then stop and ask for feedback.
2. Otherwise take Jackson's latest message as feedback. Apply the CLAUDE.md glossary when interpreting it.
3. Revise the doc. Change what the feedback asks for; don't rewrite parts it didn't touch.
4. Append a round to docs/ai-process/coauthoring.md: the feedback verbatim (exactly as transcribed), a short list of what changed and why, status open.
5. Commit: `<doc> r<N>: <what changed>`, then put the sha in the log entry and amend.
6. Show the changed sections in chat and ask what's next.

If the argument includes "lock": mark the latest round locked, commit `<doc>: locked`, close its issue if one exists, and say which doc is next.
