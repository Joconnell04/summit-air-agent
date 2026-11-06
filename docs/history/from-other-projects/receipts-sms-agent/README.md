# Receipt-reconciliation agent (email and SMS)

A client project, written up without client detail. A small business coded its monthly card statement by hand in a chat tool: upload a statement and a pile of receipts, re-explain the rules, fix the workbook it returns. The agent replaces that with a pipeline. No files, data or names are in this repo.

## How it works

1. People email or text a photo of a receipt with a few words on what it was for.
2. The server reads each receipt into a validated schema (store, date, total, currency, line items) and checks the arithmetic in code. The model reads; code computes.
3. When a statement arrives, rows are coded from the business's own approved months and rules. The model suggests a code only for a merchant with no history, in a separate column.
4. Receipts are matched to rows by amount first, then a date window, with merchant name as a weak tiebreaker.
5. The reviewer gets one email with a workbook in the format they already use. Their reply with the corrected workbook is the approval.

## Patterns

- **One intake, two channels.** Email and SMS (Twilio) both call the same ingest function. Message ids are prefixed by channel and every step is idempotent on the message id, so polling twice changes nothing.
- **Grouping.** A photo and a follow-up text within two minutes are one receipt. The text becomes the note.
- **The reply is the approval.** No web page and no new login. The server diffs the returned workbook against what it sent.
- **Learning from corrections.** The reviewer's edits update a merchant map keyed by card, newest approved month first, so next month codes those merchants the reviewer's way. Fill blanks only; never overwrite a typed value.
- **Rules before the model.** Deterministic coding, flag on conflicting history, one model call per unknown merchant. A cheap model call judges whether a sender's note says what the purchase was for; if it fails, a plain rule applies and the run is never blocked.
- **Numbers labelled by source.** The plan marks measured figures SOURCE and unmeasured ones ASSUMED, and the status board reports rows, match rates and dollars reconciled rather than adjectives.
- **Tests are hermetic.** No network, no keys, no live model calls in the suite; live runs are small, detached and cost cents.

## What carries into Summit Air

- **SMS confirmation after booking,** and a text-in path that calls the same booking logic as the voice agent (channel-agnostic intake).
- **Owner approval loops:** a reply to approve a non-routine job instead of a new dashboard.
- **Idempotent ingestion** so a repeated webhook or retry never double-books.
- **Ask only when needed:** the agent asks a follow-up question only when the sender left out something the system cannot infer.
- **Small paid tests:** live model runs on a cheap subset, full suites only at a release gate.
