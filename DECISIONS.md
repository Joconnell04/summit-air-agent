# Decisions

One entry per real choice, newest first. Each entry: what was decided, why, what else was considered, what would change it, and a link to the discussion or exploration behind it.

<!-- Template
## NNN. Title
Decided: ...
Why: ...
Considered: ...
Would change if: ...
Discussion: docs/ai-process/transcripts/... or docs/explorations/...
Issue: #N
-->

## 001. The mock sells technician-windows, not counters
Decided: In the mock ServiceTitan, a slot is one technician in one arrival window on one day. Open capacity is technicians on shift with the right skills, minus those booked, minus a few held for urgent jobs. A database constraint allows one live appointment per technician per window.
Why: A counter can go wrong under parallel requests; a unique row cannot. It also gives skill matching, an on-call technician and a real technician to put in the job.
Considered: A capacity table with a count per window and business unit (simpler, but needs extra locking to be safe).
Would change if: Real ServiceTitan capacity turns out to be count-based and the adapter needs the same shape from the mock.
Discussion: docs/ai-process/briefs/01-mock-servicetitan.md
Issue: #13
