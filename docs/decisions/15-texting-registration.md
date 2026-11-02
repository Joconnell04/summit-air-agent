# 15. How do we send the booking confirmation text without a rejected registration?

Issue: #15. Status: ready

## Question

(646) 624-0418 cannot text anyone until carriers approve it, and Jackson has no company number (EIN) to register with. Which route sends the "booking confirmed" text, if any, with one submission that passes? Texting is not on the assignment's required list (docs/assignment.md asks the agent to "book the appointment or confirm next steps"); it is our own addition (C-08 in docs/considerations.md).

## Options

### A. Register the 646 number as an individual (Twilio "Sole Proprietor" brand)
Carriers require business texts from a normal local number to be registered (called A2P 10DLC). Jackson registers as himself, not as Summit Air, and describes the number truthfully: a demo phone agent for a made-up HVAC company, texting only people who call and say yes. Twilio texts a code to his own mobile, then a person reviews the campaign. Limits: one campaign, one number, 1 text a second, 3,000 a day, far above tens of texts.

### B. Buy a toll-free number and verify it for texts only
Free verification (name, address, website, use case, how people consent), 3 to 5 business days. A Sole Proprietor is allowed, but Twilio says toll-free is meant for medium and large businesses, not hobbyists. The text would come from a different number than the one callers dialed. The same honesty rules apply.

### C. No text in the demo; show the confirmation another way
The agent says "your confirmation is on the owner screen and in your email", and we show it on the owner view or send an email. No registration, no waiting, nothing to reject. We lose the "text arrives on the phone" moment.

Not chosen: **Low-Volume Standard** brand. It needs an EIN, and getting one for a business Jackson does not run, or registering "Summit Air" itself, is the misrepresentation to avoid.

## Context

| | A. Sole Proprietor 10DLC | B. Toll-free verification | C. No text |
|---|---|---|---|
| Cost to start | $4.50 brand + $15 review + $2 a month (SOURCE); number already bought | $0 verification; number $2.15 a month (SOURCE, exploration 004) | $0 |
| Wait | Identity check about 2 to 3 days, brand minutes (up to 7+ business days if flagged), campaign 5 to 10 days (SOURCE) | 3 to 5 business days (SOURCE) | None |
| Chance of a rejection | Medium: a person reads every field, and a fictional-company demo is unusual (ASSUMED) | Medium (ASSUMED) | None |
| Cost of a miss | $15 review fee is non-refundable (SOURCE); re-filing charges unconfirmed | No fee; some rejections cannot be re-filed (SOURCE) | None |
| What Revin sees | Real text from the called number | Real text from another number | Confirmation on screen or email |

Until approval, Twilio blocks every text from an unregistered 646 number to US phones with error 30034 (SOURCE). Calls are not part of this registration (ASSUMED).

## Recommendation

Start A now, registered honestly as Jackson, using the fields below, because approval takes days to weeks and the build does not have to wait. Build the text behind an on/off switch so the owner view and email always show the confirmation (C is the free fallback); drop to C, not a blind re-file, if the first campaign is rejected or the demo comes before approval, and consider B only if Jackson does not want his name on a public page for A.

## Research notes

Tools: built-in search and fetch, plus curl on Twilio's public help-center JSON (support.twilio.com returns 403 to plain fetch). No Exa or Firecrawl calls. Carrier codes of conduct (AT&T, T-Mobile): current versions not found; the CTIA text and Twilio's reviewer rules carry the same consent and privacy requirements. This is not legal advice.

### What Jackson supplies (placeholders, never filled here)

[BRAND NAME] = exact legal first and last name on his ID (not "Summit Air", not an invented trade name). Also [FIRST NAME], [LAST NAME], [EMAIL] (one address, used everywhere, shown on the page), [PERSONAL MOBILE] (a real US mobile, not a Twilio number; can be used for at most 3 brand registrations), [STREET], [CITY], [STATE], [ZIP], government photo ID and a selfie for the identity profile, [PAGES URL], [DATE], retention periods [N].

### Why honesty is also the way to pass

- Twilio checks that brand, website, description, samples and consent evidence "describe the same sender" (SOURCE: [rejection guide](https://support.twilio.com/hc/en-us/articles/15778026827291), 2026-09-10). A Sole Proprietor campaign title must match the registered name (30914). Registering a made-up company fails identity checks and risks a "fraudulent or misleading" rejection (30959) that cannot be re-filed.
- So the brand is Jackson; "Summit Air" appears only as the fictional business in the demo, always next to the word "demo". In production each real customer would register its own brand (with its EIN); this is the demo-tier path.
- A Sole Proprietor must not have an EIN (SOURCE: [required information](https://support.twilio.com/hc/en-us/articles/13718729624091), 2025-09-12).

### Common rejection reasons and the fix

| Reason (code) | Fix in this submission |
|---|---|
| Opt-in unclear or incomplete (30896, 30909, 30917) | Message flow below lists the one opt-in path from first prompt to confirmation, with the full script |
| Verbal consent not documented (Twilio: "most difficult to verify" but acceptable) | Full script pasted in; consent stored with time, call ID, number, wording version (CTIA 5.1.2 list); never marketing |
| Consent language missing type, frequency, rates, STOP (30924, 30544, 30545, 30547, 30569) | Script and every text carry all four |
| Privacy policy missing or shares mobile data (30908, 30932, 30536) | Policy below has the exact "not shared or sold for marketing" sentence |
| Terms missing frequency, rates, HELP/STOP, carriers-not-liable, support contact (30557 to 30563) | Terms below cover each item |
| Samples lack brand or opt-out text, or do not match use case (30893, 30889, 30539) | Brand in every sample, STOP in all, brackets for variables |
| Website not live, gated, or does not match brand (30891, 30907, 30919, 30922) | Public HTTPS pages, same name and email as the brand, loads logged out |
| Description does not say who sends, who receives, why, or the software that sends (30886, 30912) | Description below answers all three and names the application |
| URL shortener or http link (30963, 30964) | Direct https link to own page |
| Treated as test or personal traffic (30532, 30533, 30534) | Say plainly it is an application sending to callers who asked; keep recipients to Jackson and invited reviewers |
| Sole Proprietor with EIN (30903, 30915); brand email or phone reused too often | No EIN; email used for at most 10 brands, mobile for at most 3 (SOURCE: [collect business info](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc/collect-business-info)) |

Residual risk: a fictional-company demo has no precedent in Twilio's guides, and review is case by case. Honest, consistent fields are the best available defense, not a guarantee.

### Fields to paste

**Brand (Trust Hub Individual Profile, then A2P Brand):** profile name "[BRAND NAME]"; authorized representative [FIRST NAME] [LAST NAME], [EMAIL], [PERSONAL MOBILE]; address [STREET], [CITY], [STATE], [ZIP], US; brand name [BRAND NAME]; vertical "Technology" (optional). Reply to the code texted to [PERSONAL MOBILE] within 24 hours.

**Campaign use case:** "Sole proprietor" (the only choice for this brand). Campaign title: [BRAND NAME]. Checkboxes: embedded links = yes (sample 1 links the terms page); phone numbers = no; direct lending = no; age-gated = no.

**Campaign description:**
> [BRAND NAME], an individual in [STATE], sends these messages from a demo application: an AI phone agent built as a personal software project to demonstrate a fictional heating and cooling company called Summit Air. The only recipients are people who phone the demo line (the developer and reviewers the developer invited) and, during that call, ask the agent to text them a confirmation of the demo booking they just made. The application sends one text per request with the date, arrival window and reference code. There is no marketing, no contact list and no real service business; Summit Air is fictional.

**Message flow (how people consent):**
> Consent is given verbally during an inbound call to the demo line, (646) 624-0418. Nobody is texted unless they call first. Texting is optional; a caller can finish a booking without it. (1) The caller phones the line and an automated AI agent answers and says it is a demo. (2) Only if the caller asks for a confirmation text, or accepts the agent's offer, the agent reads this script: "I can text a confirmation of this demo booking to the number you are calling from. It is a demo of a fictional company called Summit Air, run by [BRAND NAME]. You will get one text per request, with your booking details and no marketing. Message and data rates may apply. Reply HELP for help or STOP to opt out. The terms and privacy policy are linked in the text and at [PAGES URL]. Do you want me to send it? Please say yes or no." (3) Only a clear "yes" counts; "no" or silence sends nothing. (4) The server stores the answer with the time, call ID, caller number and script version, and refuses to send without it. (5) The text is sent at once with the brand name, rates notice, HELP and STOP instructions and the terms link. Terms: [PAGES URL]/terms. Privacy policy: [PAGES URL]/privacy.

**Sample messages (variables in brackets):**
1. `[BRAND NAME] (Summit Air demo): Your demo visit is booked for [Day] [Date], arrival window [Start] to [End]. Ref [Code]. Terms: [PAGES URL]/terms Msg&data rates may apply. Reply HELP for help, STOP to opt out.`
2. `[BRAND NAME] (Summit Air demo): Your demo annual tune-up is booked for [Date], arrival window [Start] to [End]. Ref [Code]. Reply HELP for help, STOP to opt out.`
3. `[BRAND NAME] (Summit Air demo): Your demo visit [Code] is now [Date], [Start] to [End]. Reply HELP for help, STOP to opt out.`

**Keywords and replies (set the same text in the Messaging Service's Opt-Out tab):**
- Opt-in keywords: START, UNSTOP (only to resume after STOP). Opt-in message: `[BRAND NAME] (Summit Air demo): You will get booking confirmation texts when you ask for them, one per request. Msg&data rates may apply. Reply HELP for help, STOP to opt out.`
- Opt-out keywords: STOP, UNSUBSCRIBE, END, QUIT, CANCEL. Message: `[BRAND NAME] (Summit Air demo): You are unsubscribed and will get no more messages from this number. Reply START to resubscribe.`
- Help keywords: HELP. Message: `[BRAND NAME] (Summit Air demo): booking confirmations only, one per request. Help: [EMAIL]. Msg&data rates may apply. Reply STOP to opt out.`

### Public page text (three pages: home, /terms, /privacy)

**Home.** "[BRAND NAME] Summit Air demo line. This is a personal software project by [BRAND NAME], an individual in [CITY], [STATE]: a demonstration AI phone agent for a fictional heating and cooling company called Summit Air. Summit Air is not a real business and books no real visits. Call (646) 624-0418. The agent is automated. If you ask it to text you a booking confirmation, it asks for your yes first, then sends one text to the number you called from. No marketing messages are ever sent. Contact: [EMAIL]. Links: SMS terms, Privacy policy."

**SMS terms.** "Program: booking confirmation texts for the Summit Air demo (a fictional company), sent by [BRAND NAME]. Messages: the date, arrival window and reference code of the demo booking you made on a call. Frequency: one message per request; a reschedule or cancel adds one. No marketing. Consent is given verbally on the call by saying yes to the script below and is not a condition of using the demo. Message and data rates may apply. **Opt out: reply STOP. Help: reply HELP or email [EMAIL].** Carriers are not liable for delayed or undelivered messages. Privacy policy: [PAGES URL]/privacy. Script read to callers: (paste the script from the message flow)."

**Privacy policy.** "Last updated [DATE]. Sender: [BRAND NAME], an individual; contact [EMAIL]. We collect the number you call from, a transcript of the call and the booking details you give, your yes or no to the text with its time, and whether the text was delivered. We use it to run the call, send the text you asked for, keep proof of your consent, and test the demo. Service providers that run the phone line, speech, language model, hosting and text delivery process it to provide their service to us (currently Twilio, LiveKit, Deepgram, Anthropic, Cartesia, Vultr). **We do not sell or share your SMS opt-in data or personal information with third parties for marketing purposes.** Mobile information and text opt-in consent are not shared with third parties or affiliates for marketing or promotional purposes. We keep transcripts [N] days and consent records [N] months. Reply STOP to stop texts; email [EMAIL] to ask for deletion."

Every sentence on the pages must be true of the build (vendor list, retention, stored consent) before submitting.

**Hosting.** A small public GitHub repo with GitHub Pages: free, HTTPS, no server. The main repo is private, and free plans can only publish Pages from public repos (SOURCE: [GitHub docs](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site)). The pages must stay live after approval.

### Step order (each step needs the one before it)

1. Confirm the Twilio account is paid (not trial); registration needs it (SOURCE: [toll-free guide](https://support.twilio.com/hc/en-us/articles/20212966914075)).
2. Console, Trust Hub: create the Individual Profile (ID and selfie). Wait for approval (2 to 3 days); keep going meanwhile.
3. Publish the three pages; open each in a private window and confirm HTTPS, no login, no redirect, brand name and email visible.
4. Build and test the consent gate and text tool against a mock (real sends fail with 30034 until approval): script wording identical to the filing, consent stored, send refused without a yes, 21610 (number opted out) handled.
5. Console, Trust & Compliance: register the Sole Proprietor Brand; answer the code texted to [PERSONAL MOBILE] within 24 hours. Wait for APPROVED.
6. Create a Messaging Service with only the 646 number in its sender pool (a second number triggers 30034); set the Opt-Out tab text from above.
7. Create the campaign with the fields above, the privacy and terms URLs, and that Messaging Service; pay the $15 review. Wait 5 to 10 days; contact Twilio Support if still open after 10.
8. After approval, send one real test text to Jackson's phone and check the delivery log.

**Check before pressing submit:** brand name identical in profile, brand, title, every sample, the script and both pages; same email everywhere; pages live now; script in the filing equals the script the agent speaks; no PII or real numbers in samples; no shortened links; every field different text (30911); all in English.

### Sources (numbers are SOURCE unless marked)

- Twilio, [Direct Sole Proprietor Registration Overview](https://support.twilio.com/hc/en-us/articles/52779180539803) (2026-08-03): OTP within 24 hours, brand review 7+ business days if flagged, campaign 5 to 10 days, one number.
- Twilio, [Pricing and Fees for A2P 10DLC](https://support.twilio.com/hc/en-us/articles/1260803965530) (2026-09-26): $4.50 brand, $2 a month Sole Prop campaign. [Campaign Vetting FAQ](https://support.twilio.com/hc/en-us/articles/11587910480155) (2026-10-27): $15 non-refundable, up to 5 business days.
- Twilio, [Comparison of brand types](https://support.twilio.com/hc/en-us/articles/4407882914971) (2025-09-12): 1 campaign, 1 number, 1 text a second, 3,000 a day (1,000 to T-Mobile).
- Twilio, [Campaign Onboarding Guide](https://support.twilio.com/hc/en-us/articles/11847054539547) (2026-10-29), [Registration Recommendations](https://support.twilio.com/hc/en-us/articles/26149060902555) (2026-10-27), [Campaign Registration Rejected](https://support.twilio.com/hc/en-us/articles/15778026827291) (2026-09-10), [blog](https://www.twilio.com/en-us/blog/insights/best-practices/improving-your-chances-of-a2p10dlc-registration-approval) (2025-03-28).
- Twilio, [error 30034](https://www.twilio.com/docs/api/errors/30034) (page modified 2026-04-21); [privacy and terms URLs required for API campaigns from 2026-06-30](https://www.twilio.com/en-us/changelog/a2p-10dlc-campaign-registration-will-require-privacy-policy-and-); [Trust Hub profiles](https://www.twilio.com/docs/trust-hub/profiles); [Advanced Opt-Out](https://www.twilio.com/docs/messaging/tutorials/advanced-opt-out).
- Twilio toll-free: [verification overview](https://support.twilio.com/hc/en-us/articles/5377174717595) (2026-06-01; unverified numbers blocked, free, 3 to 5 business days), [developers and hobbyists](https://support.twilio.com/hc/en-us/articles/20212966914075) (2026-06-05), [error 30527](https://static0.twilio.com/docs/api/errors/30527) (sole proprietor allowed), [error 30482](https://www.twilio.com/docs/api/errors/30482.md) (free email must appear on the site).
- CTIA, [Messaging Principles and Best Practices](https://api.ctia.org/wp-content/uploads/2023/05/230523-CTIA-Messaging-Principles-and-Best-Practices-FINAL.pdf) (May 2023), 5.1 and 5.1.2: informational texts need express consent, which may be given verbally; consent records to keep.
- Toll-free number price $2.15 a month and SMS about $0.0083 plus about $0.004 carrier fee per segment: docs/explorations/004-call-control-and-numbers.md (Twilio pricing, Aug 2026). Tens of texts therefore cost well under $2 (my arithmetic, ASSUMED 2 segments each).
