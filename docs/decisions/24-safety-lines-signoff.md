# 24. Sign off the safety lines the server speaks

Issue: #24. Status: ready

## Question

The tool server now speaks the safety lines word for word from `tenants/summit-air/agent.yaml` (`safety`, with `signed_off: false`). Spec S-17 and journey 3 say a person signs that wording off before launch. Does it ship as written, and does the sign-off keep "I'll stay on if you need me"?

What the caller hears today:

- Gas: "Please get everyone out of the house now. Don't flip any switches or use the stove, and call 911 or the gas company once you're outside."
- Carbon monoxide: "If a window is right there, open it. Then get everyone out of the house now and call 911 once you're outside."
- Said again instead of repeating the lines, in turn: "Please get outside and call 911 from there." and "The safest thing is to head outside now and call 911 or the gas company." (gas), "The safest thing is to get everyone outside now and call 911." (carbon monoxide)
- Check questions for a vague mention: "Just to be safe, are you smelling gas in the house right now?" and "Just to be safe, is a carbon monoxide alarm going off, or is anyone feeling sick or dizzy?"
- Sign-off: "Please head outside now and call 911 from there. I'll stay on if you need me, but the safest thing is to go now. We'll follow up once you're safe."

## Options

### A. Sign off as written
The wording is journey 3's, already through the co-authoring rounds. Keeps the "stay on the line" warmth Jackson asked for.

### B. Sign off with one change to the sign-off
Replace the middle sentence with "Please hang up and call 911 once you're outside." Matches the gas utility's guidance not to use a phone inside; loses the offer to stay on.

### C. Rewrite through /round first
Room to change tone or add lines (for example, not going back in until cleared). Slower, and the current lines already match the official guidance on everything else.

## Context

| | A | B | C |
|---|---|---|---|
| Matches National Grid's "don't use anything electrical, even a phone" | No | Yes | Depends |
| Matches "call 911 from outside" | Yes | Yes | Yes |
| Change to code | None, flip `signed_off` | One line in agent.yaml | Lines in agent.yaml |

Saying the lines is enforced by the server either way: booking and end-call wait until an agent turn contains them, so a wording change is config only.

## Recommendation

B. The sign-off is the only line that disagrees with the gas utility that serves Brooklyn, Staten Island and most of Queens; the rest already matches. Would change if Jackson prefers the warmth of staying on the line and accepts the small spark risk, in which case A.

## Research notes

- National Grid (the gas utility for Brooklyn, Staten Island and much of Queens): leave first, call 911 or 1-718-643-4050 from a safe place, and do not use matches, lighters or anything electrical, "even a phone" (SOURCE: [Gas Safety Newsletter June 2025](https://www.nationalgridus.com/NY-Home/About-Your-Bill/Gas-Safety-Newsletter-June-2025); [NGrid gas safety flyer, Brooklyn CB8](https://www.brooklyncb8.org/wp-content/uploads/2020/04/NGrid-Gas-Safety.pdf)). It also says not to rely on smell alone (hissing is a sign); `hissing gas` is in the clear phrases.
- Carbon monoxide: no plain FDNY step list was found; NYC Emergency Management points to FDNY's education program, and FDNY's alarm brochure covers detectors (SOURCE: [NYC Emergency Management, carbon monoxide](https://www.nyc.gov/site/em/ready/carbon-monoxide.page); [FDNY smoke and CO alarm brochure](https://www.nyc.gov/assets/fdny/downloads/pdf/fire-safety-education/08%20smoke%20and%20cm%204%20page%20%20brochure_for%20web.pdf)). Getting everyone out and calling 911 from outside is the common advice in secondary sources; ASSUMED until checked with FDNY.
- Whether a phone call in progress is a real ignition risk is debated; the utility's guidance is the standard a reviewer would hold the agent to.
