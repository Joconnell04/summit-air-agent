# 16. Shared server or a new one for this project

Issue: #16. Status: ready

## Question

Run the tool server and the voice program on the existing Vultr server, or on a new server just for Summit Air. Blocks hosting setup, and with it the first real phone call.

## Options

### A. Keep the existing server as it is
No extra cost. But it is small (2 CPUs, 2 GB memory, about 2 to 3 calls at once), it also runs Epic's client services, we have no admin rights on it (so public web access needs a paid or capped tunnel like ngrok, which has a $0 balance), and it sits in Atlanta.

### B. Make the existing server bigger
Resize to 4 GB for about $20 a month (SOURCE: Vultr pricing, in exploration 003). Fixes memory, but the resize restarts Epic's client services, we still have no admin rights, and a busy Summit Air demo would still share a machine with client work.

### C. A new server just for Summit Air
2 CPUs, 4 GB, in Vultr's New Jersey data center, about $20 a month billed by the hour (about $0.027 an hour, SOURCE: Vultr), and deleted after the interview if you like. We get admin rights (our own web address with HTTPS, no ngrok), our own update and reboot schedule, enough memory for 5 calls (ASSUMED until load-tested), and nothing we do can affect Epic. New Jersey is next to New York, so the trip to the phone network and to callers is shorter than from Atlanta.

## Context

| | A. As is | B. Bigger | C. New |
|---|---|---|---|
| Extra cost | $0 | about $20/mo | about $20/mo, hourly |
| Calls at once | 2 to 3 | about 5 | about 5 |
| Admin rights, own HTTPS | No (needs ngrok) | No | Yes |
| Risk to Epic's client services | Shared | Restart, then shared | None |
| Location | Atlanta | Atlanta | New Jersey |

What you'd say if Paul asks about scale: the demo runs on one small server that handles about 5 calls at once; for production we'd size it to the call volume (exploration 003 has the numbers) and add a second server in another location as a hot spare.

## Recommendation

C, a new server for Summit Air. For the same $20 a month as B it removes the ngrok workaround and any risk to Epic's client work, and it can be deleted when the interview is over.

## Research notes

- Existing server, MEASURED over ssh: Ubuntu 24.04, 2 vCPU, 1.9 GiB memory, Atlanta (ipinfo for 96.30.198.9), runs Epic services, user account without sudo, automatic reboots at 09:30 UTC set by another project.
- Memory per call and the 2 to 3 call estimate: docs/explorations/003-capacity-and-cost.md (ASSUMED; LiveKit publishes no memory figure for its turn-detector model).
- ngrok free plan: 20,000 requests a month (from the earlier practice agent's notes); account balance $0 (FROM JACKSON).
