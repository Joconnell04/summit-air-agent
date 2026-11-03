# Notes for the Summit Air conversation

Points to raise with the owner, or to have ready when they ask. Each one is small, true of what is built, and says what it would take to change. Not a roadmap; rollout steps belong in the meeting's next steps.

## Why we built our own voice stack instead of using Retell as is

Retell is a strong managed platform, and we built a working Retell version to compare against (docs/bakeoffs.md). The deciding limit was control over exact wording. On Retell the platform's model speaks every line itself, including the gas and carbon monoxide safety lines, and on a test call it paraphrased them. Our server still blocked any booking until the exact lines were said, but the caller heard something slightly different from the script Summit Air signed off.

On our stack the server decides the words for anything that has to be exact, and the agent says them as written. That is what lets the agent be fitted to Summit Air rather than to a platform's defaults: the safety script, the greeting, how the company is named, any disclosure the owner wants read, slogans and the house tone, and later the brand's own design system on anything the caller sees. For the owner: "anything you need said a certain way gets said that way, every time."

## A booked visit can be cancelled by phone only before a technician is on the way

Today the agent can cancel or move a visit while it is still Scheduled. Once a technician is dispatched, the agent takes the request and flags a person instead, because the technician may already be driving there (tenants/summit-air/agent.yaml; ASSUMED rule). A small change could route that request straight to the dispatched technician or the dispatcher, by a flag that alerts them or a warm transfer once transfers are in, so the customer isn't waiting on the office. It is one rule and one alert, not a new feature.

## Connecting to their real ServiceTitan

The mock of ServiceTitan is matched to ServiceTitan's published API specs (docs/explorations/006-servicetitan-field-names.md). Going live means a ServiceTitan app key and a tenant id from Summit Air, and ideally a sandbox tenant to check the few shapes the specs leave open, such as whether capacity is counted in hours or jobs.
