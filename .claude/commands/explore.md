---
description: Start background research on a side idea without derailing the current discussion
argument-hint: <idea>
---

Idea: $ARGUMENTS

1. Open an issue with the exploration template: `gh issue create --label exploration --title "Explore: <short title>" --body "<idea and what prompted it>"`. Add it to the project board.
2. Pick the next number in docs/explorations/ (NNN).
3. Launch the `researcher` agent in the background with the idea, the issue number, the target path docs/explorations/NNN-<slug>.md, and one line of context from the current conversation.
4. Reply in one line with the issue number, then go straight back to what we were discussing.
