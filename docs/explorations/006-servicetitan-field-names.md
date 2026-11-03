# 006. ServiceTitan's real field names against the mock

Researched 2026-11-02. Free tools only (WebSearch, WebFetch, curl, `gh`); no Exa or Firecrawl credit was spent. Provenance: SOURCE means seen in a ServiceTitan OpenAPI document, SECONDARY means seen only in third-party material, ASSUMED is my estimate. The mock README's "I could not read developer.servicetitan.io" is out of date: the portal's page is a JavaScript shell, but the specs behind it download with plain `curl`.

## 1. Question

Which of the names, paths, enums and shapes in `servicetitan_mock/` match ServiceTitan's real public API, and which do not? In particular: the capacity request and response, the telecom call record (is there an outcome or disposition field?), and how job priority and arrival windows are named.

## 2. Why it matters to Summit Air

`docs/brief.md` says the FSM is "ServiceTitan, emulated by our mock with the same shapes (ASSUMED field names)" and that pointing the adapter at the real API leaves agent code unchanged. That claim holds only if the shapes are right. `docs/spec.md` S-44 puts "booking rate by call type" on the owner view, so the call-type words must be the real ones. DECISIONS 003 says the technician-window slot model should be revisited "if real capacity turns out to be count-based". A reviewer who knows ServiceTitan will check exactly these places.

## 3. Options

### What the real API says

ServiceTitan publishes 24 OpenAPI 3.1 documents at `https://developer.servicetitan.io/api/docs/apis/tenant-<module>-v2` (telecom has no `-v2`). I downloaded 13 of them (the 9 linked below, plus customer interactions, scheduling pro, service agreements and marketing ads) and read the schemas for the modules the mock models (SOURCE). A copy pinned on 2026-09-04 in a third-party repo matches the live ones for capacity and telecom, and differs only slightly elsewhere. Verdicts: Match, Partial (name right, values or shape incomplete), Differs, Not real (absent from all 24 documents).

| Mock | Real | Verdict | Spec | Conf. |
|---|---|---|---|---|
| Paths `/{module}/v2/tenant/{id}/...` for crm customers, locations, contacts, notes, bookings, leads; memberships; membership-types; recurring-service-events; installed-equipment; jpm jobs, appointments, job-types, job-cancel-reasons, history, notes; dispatch capacity, zones, technician-shifts; settings business-units, technicians; marketing campaigns | Same paths. Also real: `crm .../booking-provider/{id}/bookings`, `dispatch .../arrival-windows`, `dispatch .../appointment-assignments`, `memberships .../recurring-services`, `jbce .../call-reasons` | Match | [crm], [jpm], [dispatch], [memberships], [equipment], [settings], [marketing] | SOURCE |
| List envelope `page, pageSize, hasMore, totalCount, data` | Same (`totalCount` only when `includeTotal=true`) | Match | [crm] | SOURCE |
| Auth: client credentials, `ST-App-Key`, hosts `api.servicetitan.io`, `api-integration.servicetitan.io` | Same; token URL `https://auth.servicetitan.io/connect/token` | Match | [dispatch] | SOURCE |
| Creates return 201 | Documented success is 200 | Differs | [jpm] | SOURCE |
| Error body `status, code, title, detail, errors, requestId` | `type, title, status, traceId, errors, data` | Differs | [dispatch] | SOURCE |
| `Idempotency-Key` header | No idempotency header in any of the 24 documents | Not real | all | SOURCE |
| `GET settings/company-profile` | No such endpoint (settings has 22 operations) | Not real | [settings] | SOURCE |
| Capacity request `startsOnOrAfter, endsOnOrBefore, businessUnitIds, jobTypeId, skillBasedAvailability` | Same; `skillBasedAvailability` is required | Match | [dispatch] | SOURCE; SECONDARY [php] |
| Capacity request `priority`, `zip` | Not in the request | Not real | [dispatch] | SOURCE |
| Capacity response `availabilities[]` with `date, businessUnitId, timeslots[]` | `timeStamp` and a flat `availabilities[]`; each item is one time frame: `start, end, startUtc, endUtc, businessUnitIds[]` | Differs | [dispatch] | SOURCE |
| `windowId, afterHours, bookedCount, heldForUrgent` | None of these exist | Not real | [dispatch] | SOURCE |
| `totalAvailability, openAvailability, isAvailable` | Same names. Unit is hours in the 2026 spec; a generated client from 2024-12-24 says "jobs" | Partial | [dispatch], [php] | SOURCE; unit unconfirmed |
| `technicianIds` (open technicians) | `technicians[]` of `{id, name, status: Available or Unavailable, hasRequiredSkills}`, plus `isExceedingIdealBookingPercentage` | Differs | [dispatch] | SOURCE |
| Job fields `id, jobNumber, customerId, locationId, jobStatus, completedOn, businessUnitId, jobTypeId, priority, campaignId, summary, firstAppointmentId, lastAppointmentId, appointmentCount, createdOn, modifiedOn` | All present with the same names | Match | [jpm] | SOURCE |
| Job status `Scheduled, Dispatched, Working, Done, Canceled` | `Scheduled, Dispatched, InProgress, Hold, Completed, Canceled`. (`Working` and `Done` are appointment words) | Differs | [jpm] | SOURCE |
| Priority `Low, Normal, High, Urgent` | Same values (documented on jobs, bookings, leads); job `priority` is a plain string | Match | [jpm], [crm] | SOURCE |
| Appointment `id, jobId, appointmentNumber, start, end, arrivalWindowStart, arrivalWindowEnd, status, createdOn, modifiedOn` | Same | Match | [jpm] | SOURCE |
| Appointment status without `Hold` | `Scheduled, Dispatched, Working, Hold, Done, Canceled` | Partial | [jpm] | SOURCE |
| `technicianIds` on the appointment response | Not in the response; accepted on create. Read assignments from `GET dispatch/appointment-assignments` (`technicianId, appointmentId, jobId, status`) | Differs | [jpm], [dispatch] | SOURCE |
| Job create: appointment `start`, `end` optional | `start` and `end` required (adapter `app/fsm/servicetitan.py` sends only the window) | Differs | [jpm] | SOURCE |
| Reschedule body, cancel body `reasonId, memo` | Same (`reasonId` and `memo` both required) | Match | [jpm] | SOURCE |
| `jobStatus=A,B,C` filter | Documented as one value | Unconfirmed | [jpm] | SOURCE (absent) |
| Arrival window naming (config only, `windowId`) | `arrivalWindowStart/End` on appointments; windows are `GET dispatch/arrival-windows` as `{id, start (time of day), duration, businessUnitIds, active}` | Differs | [dispatch] | SOURCE |
| Notes with `jobId`, `createdBy` | Customer notes and job notes are separate endpoints; input is `text`, `pinToTop`; response has `createdById` | Differs | [crm], [jpm] | SOURCE |
| Customer `id, name, type, active, address, balance, doNotMail, doNotService, createdOn, modifiedOn` | Same; `type` is `Residential` or `Commercial` | Match | [crm] | SOURCE |
| `contacts[]` inline on the customer | Not inline: `GET customers/{id}/contacts` (one extra call per lookup) | Differs | [crm] | SOURCE |
| Contact `type` `Phone, MobilePhone, Email`; `memo` | Same, plus `Fax` | Match | [crm] | SOURCE |
| Contact `role`, `name` | Only `id, type, value, memo` | Not real | [crm] | SOURCE |
| Address `street, unit, city, state, zip, country` | Same, plus `latitude, longitude` | Match | [crm] | SOURCE |
| Address `county` | No county anywhere; zones carry `zips` and `cities` | Not real | [crm], [dispatch] | SOURCE |
| Location `id, customerId, name, address, zoneId, active, createdOn, modifiedOn` | Same (README had `zoneId` as ASSUMED; it is real) | Match | [crm] | SOURCE |
| `customers?phone=`, `?street=`, `?unit=`, `?zip=`; `locations?street=&unit=&zip=&customerId=` | All real | Match | [crm] | SOURCE |
| `locations?phone=` | No phone filter on locations | Not real | [crm] | SOURCE |
| Booking `id, source, status, name, address, summary, priority, start, businessUnitId, jobTypeId, campaignId, externalId, createdOn`; status `New` | Same; status `New, Converted, Dismissed, Accepted`; `source, name, summary, externalId, isFirstTimeClient` required on create | Match | [crm] | SOURCE |
| Booking `providerId`; `customerId`; list filters `status`, `customerId` | `bookingProviderId`; no `customerId`; list filters are only `externalId`, `ids` and dates | Differs | [crm] | SOURCE |
| Lead `kind`, `customerType`, `name`, `phone`, `street`, `zip` | `leadCustomerName, leadPhone, leadStreet, leadZip` (and unit, city, state, email); `campaignId` required; `callReasonId`; no `kind` or `customerType`. Status `Open, Dismissed, Converted` | Differs | [crm] | SOURCE |
| Membership `id, customerId, locationId, membershipTypeId, status, from, to, memo, active` | Same; status adds `Suspended, Deleted` | Partial | [memberships] | SOURCE |
| `memberships?customerId=&locationId=` | `customerIds` (list); no `locationId` filter | Differs | [memberships] | SOURCE |
| Membership-type `perks` | None. Real: `discount, discounts[], recurringServices[], durationBilling[]` | Not real | [memberships] | SOURCE |
| Recurring event status `NotAttempted, Scheduled, Completed`; `name`, `membershipId` | `NotAttempted, Unreachable, Contacted, Won, Dismissed`; `locationRecurringServiceId`, `locationRecurringServiceName`, `membershipId`, `jobId`, `date` | Differs | [memberships] | SOURCE |
| Installed equipment `id, locationId, customerId, name, manufacturer, model, installedOn, memo, active` | Same; filter is `locationIds`, no `customerId` filter | Partial | [equipment] | SOURCE |
| `systemType`, `installYear` | Not real; real has a `type` object and `status` `Installed` or `Replaced` | Not real | [equipment] | SOURCE |
| Zones `id, name, zips, active` | Same | Match | [dispatch] | SOURCE |
| Technician shifts `id, technicianId, shiftType, start, end, note, active` and filters | Same; `shiftType` adds `TimeOff`; `title` is required | Partial | [dispatch] | SOURCE |
| Technician `id, name, businessUnitId, active, phoneNumber, email, skills` | Same, but `skills` are `{id, name}` objects | Partial | [settings] | SOURCE |
| Technician `homeZoneId`; `technicians?businessUnitId=` | `mainZoneId` and `zoneIds`; filters are `ids, userIds, name, active` only | Differs | [settings] | SOURCE |
| Business units, campaigns, job cancel reasons (`id, name, active`) | Same | Match | [settings], [marketing], [jpm] | SOURCE |
| Telecom `GET calls`, `calls/{id}` | Real, v2 and v3 (`/telecom/v3/tenant/{id}/calls` is the feed) | Match | [telecom] | SOURCE |
| `POST telecom/calls` | No create. Only `PUT /telecom/v2/tenant/{id}/calls/{id}` to classify an existing call | Not real | [telecom] | SOURCE |
| Call `direction` `Inbound, Outbound` | Same | Match | [telecom] | SOURCE |
| Call `callType` `Booked, Unbooked, Excused` | `Abandoned, Unbooked, Excused, Booked, NotLead`. `null` means not yet classified; outbound is always `null` | Partial | [telecom] | SOURCE |
| Call `receivedOn, from, to` at top level | Same names, nested under `leadCall`; top level is a bundle: `id, jobNumber, projectId, businessUnit, type, leadCall` | Differs | [telecom] | SOURCE |
| Call `duration` integer seconds | String of format `duration` (a time span) | Differs | [telecom] | SOURCE |
| Call `reason` free text | Object `{id, name, lead, active}`; list at `GET jbce/v2/tenant/{id}/call-reasons` (`id, name, isLead, active`) | Differs | [jbce] | SOURCE |
| Call `customerId`, `jobId`, `externalId` | `customer` object, `campaign`, `agent`, `tags`; telephony id is `sid`; the job link is `jobNumber` and `projectId` on the bundle | Differs | [telecom] | SOURCE |
| Calls filters `phone`, `receivedOnOrAfter` | v3: `callerPhoneNumber, phoneNumberCalled, createdOnOrAfter, modifiedOnOrAfter, sid, tag, campaignId, agentId, minDuration` | Differs | [telecom] | SOURCE |

Tally of the 56 rows (MEASURED: counted from the Verdict column): 18 Match, 7 Partial, 20 Differs, 10 Not real, 1 Unconfirmed. Several rows group related names, so the count of individual names checked is higher (ASSUMED: about 150). Nothing here is from a live tenant: a spec can lag the running service, so one sandbox call per area is still needed (see section 5).

Telecom answer. There is no `disposition`, `outcome` or `booked` boolean. The only classification is `callType` (five values above) plus the `reason` object (whose `lead` flag marks a lead) and free `tags`. The "booked" signal is `callType = Booked` together with the job that links back. The write path for a partner is `PUT .../calls/{id}` (`callType, reason, jobId, agentId, campaignId, customer, location, tag, excuseMemo`), which scope `tn.tlc.calls:w` allows. Nothing creates the call itself; ServiceTitan's phone integration does. Jobs and leads can also carry `partnerLeadCallId` or `callId`.

Capacity answer. Real capacity answers "how many hours are open in this time frame, and which named technicians are free", with no priority, ZIP, window id or urgent-hold concept. The technician-window model in DECISIONS 003 still fits, because a free slot is a technician with `status: Available` (and `hasRequiredSkills`) in a frame. The urgent holds, after-hours urgent-only rule and ZIP-to-zone sorting are Summit Air rules and must live in our server, not be asked of ServiceTitan.

### The options

**A. Relabel only.** Move every confirmed name from ASSUMED to SOURCE in `servicetitan_mock/README.md` and `docs/brief.md`, and list the 38 rows that are not a clean match as known gaps. Effort: about 1 hour (ASSUMED). Cost: $0. Risk: the mock stays wrong; the adapter must translate later, which contradicts "agent code doesn't change".

**B. Make the mock's wire shape match the spec, keep the world model.** Change only what the agent touches: capacity response, job status words, call record (nested `leadCall`, five call types, `reason` object, `sid`, string duration), customers without inline contacts, lead and booking field names, membership filters, error body and 200 on create. The SQLite schema, urgent holds and technician-window rules stay; the shaping functions and `app/fsm/servicetitan.py` change. Plugs into `routes/dispatch.py`, `routes/jpm.py`, `routes/settings.py`, `routes/crm.py`, `servicetitan_mock/capacity.py`, Postman, and `tests/servicetitan_mock/`. Effort: 2 to 3 days (ASSUMED). Cost: $0. Risk: test churn and the ratchet; re-baselining evals that read the old shapes.

**C. Pin the specs and add a contract test.** Vendor the 9 JSON files for the modules we use under `servicetitan_mock/contracts/` with a hash file, and add a pytest that validates every mock response and request body against its schema, with a short allowlist for deliberate extras (`/debug/*`, `X-Call-Id`, idempotency). Effort: 1 day (ASSUMED). Cost: $0; about 2.0 MB of JSON (MEASURED: 1,976,620 bytes for the 9 linked files). Risk: the specs are public but their terms of use were not checked (see section 5); a green test proves the spec, not a live tenant.

**D. Real sandbox call.** Ask for a sandbox tenant and run one capacity call, one call-classification PUT and one job create. Effort: 0.5 day once credentials exist. Cost: $0 if Revin supplies a tenant (ASSUMED). Risk: depends on access Jackson may not have.

## 4. Recommendation

Do A now, then C, then B in the order C's failures list. C turns this one-off research into a standing check, so B proceeds as a list of failing tests rather than a rewrite, and A keeps the docs honest in the meantime. Take the capacity response, call record and job status words first; those change behavior, while the rest is naming. D is worth one request if Paul or Revin can lend a tenant, because it settles the hours-versus-jobs question.

What would change it: if the review is a read-through only, A alone is enough and the differences list becomes the "what I would verify with a sandbox" slide. If a real tenant is on offer soon, skip B and run D first. If the spec terms forbid copying, C fetches the specs in CI instead of vendoring them.

## 5. Open questions for Jackson

- Do you want the mock's wire shape corrected (option B), or kept as is with the gaps documented (option A)? B moves a lot of tests; A leaves the "same shapes" line in `docs/brief.md` untrue and needs rewording.
- Is a ServiceTitan sandbox tenant or app key available through Revin? One capacity call would settle whether `openAvailability` counts hours or jobs.
- Is vendoring the public OpenAPI files into this repo acceptable, given I did not read ServiceTitan's developer terms?
- Should the owner view (S-44) use the five real call types, including `Abandoned` (caller hung up) and `NotLead` (wrong number or spam), and should the agent's own call store record them under those names?
- Which scope does the agent need, if it only classifies calls and books? From the specs: `tn.crm.bookings:w`, `tn.dis.capacity:w`, `tn.tlc.calls:w`; the full list is in each document's `securitySchemes`.

Not found: any ServiceTitan statement of rate limits, token lifetime (the README's 15 minutes is not in the specs), or how booking and capacity behave under parallel requests; a Meltano or Singer tap and an Airbyte source for ServiceTitan (two searches, nothing); the portal's prose pages (`/docs/...`), which return only a title.

## 6. Sources

Read on 2026-11-02 unless noted.

- ServiceTitan OpenAPI documents, downloaded with curl: [crm], [jpm], [dispatch], [memberships], [equipment], [settings], [marketing], [telecom], [jbce]. Listing: https://developer.servicetitan.io/api/docs/apis
- Third-party pinned copy of all 24 documents retrieved 2026-09-04, with SHA-256 digests (all verified): https://github.com/montrellcruse/servicetitan-mcp/tree/main/docs/contracts (repo last commit 2026-10-26)
- SECONDARY, a PHP client generated from the same spec (last commit 2024-12-24), used to cross-check capacity: [php]
- Searches that found nothing useful: Airbyte `source-servicetitan`, Singer `tap-servicetitan`; the Celigo connector page returned 403.

[crm]: https://developer.servicetitan.io/api/docs/apis/tenant-crm-v2
[jpm]: https://developer.servicetitan.io/api/docs/apis/tenant-jpm-v2
[dispatch]: https://developer.servicetitan.io/api/docs/apis/tenant-dispatch-v2
[memberships]: https://developer.servicetitan.io/api/docs/apis/tenant-memberships-v2
[equipment]: https://developer.servicetitan.io/api/docs/apis/tenant-equipment-systems-v2
[settings]: https://developer.servicetitan.io/api/docs/apis/tenant-settings-v2
[marketing]: https://developer.servicetitan.io/api/docs/apis/tenant-marketing-v2
[telecom]: https://developer.servicetitan.io/api/docs/apis/tenant-telecom
[jbce]: https://developer.servicetitan.io/api/docs/apis/tenant-jbce-v2
[php]: https://github.com/compwright/servicetitan/blob/main/docs/Model/DispatchV2CapacityResponseAvailability.md
