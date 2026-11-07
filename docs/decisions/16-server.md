# 16. Where the Summit Air server runs

Issue: #16. Status: decided, option B (DECISIONS.md 005)

## Question

Run the tool server and the voice program on the existing shared Vultr server, or on a new server just for Summit Air, and if new, with which provider. A small decision, but it blocks the first real phone call.

## Options

### A. Keep the existing shared server
No extra cost, but it's small (2 CPUs, 2 GB, about 2 to 3 calls at once), runs another project's services, gives us no admin rights (so public web access needs ngrok), and sits in Atlanta.

### B. New Vultr server in New Jersey (recommended)
2 CPUs, 4 GB, $20 a month billed by the hour (SOURCE: Vultr, exploration 003). Your account already exists, so no sign-up. Admin rights, our own HTTPS, our own update schedule, no risk to the other project, and it sits next to New York.

### C. DigitalOcean (New York) or Akamai Linode (Newark)
Same size for about $24 a month, also hourly and also in the New York area (SOURCE, comparison sites, 2026). Equal capability for $4 more and a new account.

### D. Hetzner (Virginia or Oregon)
Historically the cheapest, but Hetzner raised cloud prices in April and June 2026, some lines more than doubling, and the small plans we'd use are being retired, so the price is unclear today. New account with ID checks, and not in the New York area.

### Not chosen: usage-billed platforms (Fly.io, Railway, Render)
They bill by use, but the voice program has to stay running and connected to LiveKit all day, so an always-on machine costs about the same while adding a platform to learn.

## Context

| | A. Shared | B. Vultr NJ | C. DO / Linode | D. Hetzner |
|---|---|---|---|---|
| Monthly | $0 extra | $20, hourly | about $24, hourly | unclear after 2026 rises |
| Calls at once | 2 to 3 | about 5 | about 5 | about 5 |
| Admin rights | No | Yes | Yes | Yes |
| Near New York | No (Atlanta) | Yes | Yes | No |
| New account | No | No | Yes | Yes, with ID check |

Hourly billing means the server only costs what it's up: about $0.027 an hour, so running it from now until the interview and deleting it after is roughly $5 to $10 (ASSUMED, about 10 days).

## Recommendation

B, a new Vultr server in New Jersey: the cheapest option in the New York area, no new account, and nothing shared with the other project. To create it, either add a Vultr API key to the vault and I'll set it up, or create it in the Vultr console (Cloud Compute, Regular Performance, 2 vCPU and 4 GB, New Jersey, Ubuntu 24.04, with the vultr_personal SSH key).

## Research notes

- Existing server, MEASURED over ssh: Ubuntu 24.04, 2 vCPU, 1.9 GiB memory, Atlanta (ipinfo for its address), runs another project's services, user account without sudo, automatic reboots at 09:30 UTC set by another project.
- Memory per call and the 2 to 3 call estimate: docs/explorations/003-capacity-and-cost.md (ASSUMED; LiveKit publishes no memory figure for its turn-detector model).
- ngrok free plan: 20,000 requests a month (from the earlier practice agent's notes); account balance $0 (FROM JACKSON).
- Hetzner 2026 price changes (SECONDARY): https://findstack.com/resources/hetzner-price-increase-2026, https://wz-it.com/en/blog/hetzner-price-increase-june-2026-cpx-ccx-alternatives/
- DigitalOcean, Vultr and Linode 4 GB prices (SECONDARY): https://bestusavps.com/vs/do-vs-vultr-vs-linode/, https://costbench.com/compare/linode-vs-vultr/
