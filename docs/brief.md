# Brief

## The assignment (as received)

You're a Forward Deployed Engineer at Revin. A new customer, Summit Air, a residential HVAC company in NYC, has asked you to build an AI phone agent that handles their inbound calls.

From the kickoff call:

> "We're a 40-tech, residential-only shop across three counties, and our phones ring off the hook, especially when the first heat wave or cold snap hits. Most calls are people whose AC went out, furnace won't kick on, or they need their annual maintenance scheduled. We want an AI agent that answers the phone, figures out what they need, and books a service call. If someone's got no heat in January with an elderly person in the house, that needs to get flagged as a priority immediately."

The agent should be able to:

1. Answer a call and figure out what HVAC issue the caller has
2. Determine if the caller is residential or commercial
3. Determine if the caller is already a member
4. Collect all the information a technician needs to run the appointment
5. Distinguish routine maintenance from urgent situations (no heat in winter, no AC with a medical condition or elderly resident, gas smell)
6. Book the appointment or confirm next steps with the caller

No constraints on tools, frameworks or models: use what you'd use if a real customer needed this tomorrow.

Delivered 24 hours before the session: a phone number to call the agent live, and access to this repo.

The session (45 minutes) is run as a customer demo: walk Summit Air through the build, call it live, handle the owner's questions and pushback, debrief honestly, and propose a rollout. Then an out-of-character discussion of prompting, architecture and tradeoffs, and live debugging.

## Assumptions

Filled in during the design session. Each line says what was assumed and what would change if it's wrong.
