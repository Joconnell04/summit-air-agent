# Notes for the Summit Air conversation

Points to raise with the owner, or to have ready when they ask. Each one is small, true of what is built, and says what it would take to change. Not a roadmap; rollout steps belong in the meeting's next steps.

## Why we built our own voice stack instead of using Retell as is

Retell is a strong managed platform, and we built a working Retell version to compare against (docs/bakeoffs.md). The deciding limit was control over exact wording. On Retell the platform's model speaks every line itself, including the gas and carbon monoxide safety lines, and on a test call it paraphrased them. Our server still blocked any booking until the exact lines were said, but the caller heard something slightly different from the script Summit Air signed off.

On our stack the server decides the words for anything that has to be exact, and the agent says them as written. That is what lets the agent be fitted to Summit Air rather than to a platform's defaults: the safety script, the greeting, how the company is named, any disclosure the owner wants read, slogans and the house tone, and later the brand's own design system on anything the caller sees. For the owner: "anything you need said a certain way gets said that way, every time."

## A booked visit can be cancelled by phone only before a technician is on the way

Today the agent can cancel or move a visit while it is still Scheduled. Once a technician is dispatched, the agent takes the request and flags a person instead, because the technician may already be driving there (tenants/summit-air/agent.yaml; ASSUMED rule). A small change could route that request straight to the dispatched technician or the dispatcher, by a flag that alerts them or a warm transfer once transfers are in, so the customer isn't waiting on the office. It is one rule and one alert, not a new feature.

## Connecting to their real ServiceTitan

The mock of ServiceTitan is matched to ServiceTitan's published API specs (docs/explorations/006-servicetitan-field-names.md). Going live means a ServiceTitan app key and a tenant id from Summit Air, and ideally a sandbox tenant to check the few shapes the specs leave open, such as whether capacity is counted in hours or jobs.

## The address lookup shows how any of their tools can plug in

When a caller says "40 Fulton Street", the agent doesn't ask for a ZIP: the tool server looks the street up in NYC Planning's GeoSearch, a free public service, and gets the exact address, borough and ZIP back in well under a second (app/geocode.py, `find_address` in docs/tools.md). If two boroughs have that street, it asks which one. The caller never notices a lookup happened.

That's the pattern for any system Summit Air already runs on. Each one becomes a tool on our server: the server calls it, decides what it means, and hands the agent one sentence to say. The agent never talks to the outside system directly, so adding one doesn't change the prompt or the safety rules, and every call to it is logged on the owner view like everything else. GeoSearch is the proof of concept; ServiceTitan is the same shape at a larger size.

Talk track: "The address lookup is a small example of something bigger. Anything your business already uses, whether that's your CRM, your payment system or your parts supplier, can be plugged in the same way, and the agent gets more useful with each one without getting less predictable."

## Answering questions from their own material

Today the agent answers equipment questions ("why is my furnace short-cycling?") from one page of HVAC basics the company owns (`knowledge/hvac-basics.md`), matched by plain word overlap, with no prices and no repair steps, so every answer can be traced to the line it came from. That is the right size for a page. For a company with a real library, such as manuals for the brands it services, warranty and maintenance-plan terms, or past job notes, or one that wants the agent to answer deeper technical questions, we'd recommend full retrieval (RAG): the documents split into passages and indexed, the few best passages fetched for each question, and the answer kept to what they say, with the source named in the call log. It plugs in behind the same tool, so the rules about prices and repairs stay where they are (docs/later.md C-86).

## Priority, visit types and the on-call roster

Two fields on every visit. Visit type is Emergency dispatch (a technician sent right away, always Urgent) or Scheduled (every other visit); a call that left no visit is No visit. Priority is its own field:

| Priority | When | Example |
|---|---|---|
| Low | Planned work, nothing wrong | tune-up, maintenance, estimate |
| Normal | Something's wrong, no strong signal | "it's not keeping up" |
| High | A fault the caller describes, or no heat in the heating season (Oct 1 to May 31), or no cooling in the cooling season (Jun 15 to Sep 15) | "it's completely out", "it's banging" |
| Urgent | A special call-out, and every emergency dispatch | an older person, a baby or a medical condition at home; an active hazard |

On a High no-heat or no-cooling call, Brian asks once: "Can this wait for a scheduled visit, or do you need someone out right away?" Someone right away makes it Urgent and an emergency dispatch. In the day an emergency dispatch takes the soonest technician on the schedule; after hours it goes to the on-call technicians. The company file says there are two (ASSUMED), each busy about two hours a dispatch, and when both are out the caller hears "the first one free will call you and head to you after their current job, in about an hour and a half."

Talk track: "Every visit is either scheduled or an emergency dispatch, and priority is separate. The caller decides whether it can wait; the server decides what counts as urgent from what they said."

## The safety menu

Gas, carbon monoxide or any other danger the side model hears gets the safety lines once, then one menu: someone on the team now, an appointment, or someone sent right away. A person now is put straight through; an appointment books at High; someone right away is an emergency dispatch once Brian has the address and knows where the problem is. If the caller takes it back ("it was the stove"), the call carries on as usual. A leak is never treated as a 9-1-1 call.

## Equipment questions

The agent answers equipment questions ("why do my radiators bang?") from five short pages the company owns, searched by meaning with an embedding index, so a caller's own wording finds the right passage. No prices and no repair steps are in the pages, and the agent never mentions a document. A bigger library is the same feature with more pages. Sending these answers to a larger model (Sonnet) is built and switched off: on 10 test questions it was no more accurate and about 1.4 seconds slower to its first word (evals/rounds/knowledge-rag-01.md, MEASURED).
