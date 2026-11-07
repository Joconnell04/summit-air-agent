# 011. Saying and hearing local NYC names right

Issue #58, consideration C-55. Researched 2026-11-03. Free tools first; 5 paid Exa calls (4 searches, 1 contents fetch) to find local pronunciation sources the free tools missed. No calls to Cartesia, Deepgram or any voice provider. Numbers are SOURCE (linked in section 6), MEASURED or ASSUMED.

## 1. Question

Can the agent say Houston Street and a short list of other hard NYC street and neighborhood names the way locals do, and transcribe a caller who says them? Which parts belong in a per-tenant list, and how would we check it without paid voice calls?

Houston Street is in Manhattan, outside Summit Air's Brooklyn, Queens and Staten Island zones (tenants/summit-air/config.yaml), so the agent never books or reads it back. A caller may still say it, and the agent may repeat it when declining. The rest of the list is in or beside the zones.

## 2. Why it matters to Summit Air

The read-back is where a wrong street name shows most: S-26 (docs/spec.md) makes the agent read the address aloud and the caller confirm it before any booking, and `read_back` in tenants/summit-air/agent.yaml writes that sentence on the server. A New Yorker who hears "kah-SKY-oo-ZEE-ko Street" doubts the agent got the address, and the demo audience hears the same slip. The brief makes the tenant list the place for local facts (docs/brief.md; C-36 local context, C-37 template), so names belong in config, not in code or the prompt. Hearing matters because speech-to-text writes whatever it hears, and the read-back then speaks that spelling.

## 3. Options

### The names

Respellings and IPA below come from the linked sources; the "likely wrong" column is ASSUMED from the spelling and unheard (no test run). Service area is judged by neighborhood against the ZIPs in config (ASSUMED match, not checked street by street).

| Name | Where; in area? | Locals say; IPA | Likely wrong (ASSUMED) | Sources |
|---|---|---|---|---|
| Houston Street | Manhattan; no | HOW-stun; /ˈhaʊstən/ | HYOO-stun (the Texas city) | WNYC, Thrillist, Wikipedia |
| Kosciuszko Street | Bed-Stuy and Bushwick; yes (zone 1) | Locals disagree: koss-ee-OSS-koh /ˌkɑsiˈɑskoʊ/ (Brownstoner, "most common"); kuh-SHOOS-koh (Thrillist, near the Polish); kah-skee-OOS-koh (WNYC) | kos-KY-oos-ko, letter by letter | Brownstoner 2015, Thrillist 2014, WNYC, Wikipedia |
| DeKalb Avenue | Fort Greene to Bushwick; yes | dee-KALB /diˈkælb/; some say DEE-kalb, and "al" varies | deh-KAWLB | Wikipedia, Thrillist, Brownstoner ("everyone differs") |
| Schermerhorn Street | Downtown Brooklyn; yes (11201) | SKER-mer-horn /ˈskɜrmərhɔrn/ | SHAIR-mer-horn | WNYC, Thrillist, Brownstoner (also "SKIM-er-horn") |
| Gowanus | Brooklyn, 11215; borders zone 1 | guh-WAH-nus /ɡəˈwɑnəs/ | GOW-uh-nus | WNYC, Wikipedia |
| Cortelyou Road | Flatbush, Ditmas Park; partly (11218) | cor-TEL-you /kɔrˈtɛlju/ | COR-tuh-loo | Forgotten NY ("to a person"), Thrillist |
| Van Wyck Expressway | Queens, Jamaica to Flushing; a landmark | van WICK /væn ˈwɪk/; some say van WYKE | van WYKE | WNYC, Thrillist, Wikipedia (lists both) |
| Wyckoff Avenue | Bushwick (11237); yes | WHY-kawf /ˈwaɪkɔf/ | WICK-off | Thrillist |
| Nostrand Avenue | Brooklyn, many ZIPs; yes | NO-strand /ˈnoʊstrænd/ | NOSS-trand | Thrillist |
| New Utrecht Avenue | Bensonhurst; yes (11204) | YOO-trek /ˈjuːtrɛk/, no final t | yoo-TRECHT | Brownstoner, Thrillist |
| Conselyea Street | Williamsburg; yes (11211) | con-SEHL-yuh /kɑnˈsɛljə/ | con-suh-LEE-uh | Thrillist, Brownstoner |
| Joralemon Street | Brooklyn Heights; yes (11201) | juh-RAL-uh-mun /dʒəˈræləmən/ | JOR-uh-LEE-mon | WNYC, Thrillist, Brownstoner (vowels vary a little) |
| Boerum Hill | Brooklyn, 11201 and 11217; partly | BORE-um /ˈbɔrəm/ | BOO-rum | WNYC |
| Todt Hill | Staten Island; yes (zone 5, ASSUMED) | TOHT /toʊt/ (Wikipedia) or TOAD (WNYC, Secret NYC) | TOT | Wikipedia, WNYC, Secret NYC 2025 |
| Arrochar | Staten Island, 10305; no (not in config) | uh-ROH-sher /əˈroʊʃər/; one source only | AIR-uh-kar | Wikipedia only, locals unchecked |

Dropped because no usable local source turned up: Throop, Kissena, Huguenot. Sutphin was dropped because locals do not agree: a 2006 Usenet post says people say it with and without the f, and nothing settles it. Houston Street also needs the key "Houston Street", not "Houston", so a caller from Houston, Texas is not rewritten.

### A. Cartesia pronunciation dictionary, one per tenant (the model the repo uses is `sonic-3`, config/models.yaml)

What it is: Cartesia stores a list of `text` and `pronunciation` pairs, and swaps the matched text before synthesis. `pronunciation` takes sounds-like text ("chop-uh-TOO-liss") or IPA in `<<ˈ|h|aʊ|s|t|ə|n>>` form, one phoneme per bar (SOURCE: Cartesia custom pronunciations and phonemes pages). It works on `sonic-3` and later only. Spelling hacks exist (a `<spell>` tag, spaced letters), but they are for codes, not names.

Fit: the repo's installed LiveKit Cartesia plugin (1.8.10) takes `pronunciation_dict_id` and rejects it for non-sonic-3 models (SOURCE: plugin source in `.venv`; LiveKit Inference does not expose it, community post 2026-04-09, but we use the plugin). Plug-in points: a `voice.names` list in tenants/summit-air/agent.yaml beside `voice.clips`; a small script that posts it to `POST /pronunciation-dicts/` and saves the returned id in the same file; `build_session` in voice/livekit/worker.py passes the id and the tenant's keyterms; `clips.py` adds the id to its `/tts/bytes` body. It covers read-back, safety lines, model text and clips at once, and stored text never changes (the `read_back` gate compares street keys, so substitution must stay at the voice, not in the sentence).

Effort: about a day with tests (ASSUMED). Cost: no extra charge is stated on Cartesia's pricing page; TTS is 1 credit a character either way (SOURCE). Risk: matching is case-sensitive on `sonic-3`; case-insensitive needs `sonic-3.6` (SOURCE), so keys must match how the server writes names ("Houston Street", "DeKalb Avenue"). The create call requires `Cartesia-Version: 2026-08-14` while the plugin sends 2025-04-16 on speech requests; whether the older version honors the id is untested (ASSUMED to work).

### B. Our own text filter before the voice

What it is: replace names with respellings in the text sent to the voice. LiveKit already ships `replace_words` (single words, safe across streamed chunks, SOURCE: plugin source in `.venv`) and accepts custom `tts_text_transforms` on `AgentSession`; `SummitAgent.tts_node` in voice/livekit/call.py is the other hook. Reads the same tenant list. Effort: half a day (ASSUMED); cost: none. Risk: one-word keys only in the built-in helper, so "Van Wyck" and "Todt Hill" need our own phrase matcher; respellings like "HOW-stun" can be read as letters; no IPA; it holds only for LiveKit, so Retell would not share it.

### C. Prompt only

Tell the model to write "HOW-stun". Cheapest, but respellings then land in transcripts and logs, the server-written read-back is untouched, and a prompt is not a gate. Not recommended.

### Hearing them: Deepgram keyterm prompting

`nova-3` (the repo's model) accepts `keyterm`, up to 100 terms and 500 tokens, repeated as a query parameter or updated mid-stream; Deepgram recommends 20 to 50 terms and says keyterms bias but do not guarantee spelling (SOURCE: Deepgram keyterm docs). The `keywords` parameter is not for Nova-3 (the plugin raises an error). The plugin takes `keyterm=[...]` in `deepgram.STT(...)` (SOURCE: plugin source in `.venv`). Cost: $0.0013 a minute on top of $0.0077 streaming (SOURCE: deepgram.com/pricing). At 150 calls a day (ASSUMED, docs/brief.md) and 2 minutes each (ASSUMED), about $0.39 a day, roughly $12 a month. Latency impact is not stated; measure with voice/livekit/measure.py. Keyterms fix spelling, not a wrong word; a caller who is misheard still has to correct it.

### Retell and Vapi

Retell: agent field `pronunciation_dictionary` (word, alphabet `ipa` or `cmu`, phoneme), supported only for some voice providers (dashboard shows which), and `boosted_keywords` for names and streets (SOURCE: Retell create-agent API). Vapi: `pronunciationDictId` on a Cartesia `sonic-3` voice, with sounds-like or IPA entries, case-sensitive and first match wins; for Deepgram Nova-3 it exposes `keyterm` (SOURCE: Vapi docs; a support thread reports `keyterm` errors, unconfirmed). Keeping the tenant list provider-neutral (text, respelling, IPA) means any entrant can be fed from it.

### How to test

Cheap offline check: render the 15 names in one carrier sentence each ("Let me read that back: 412 Houston Street, Brooklyn.") through `clips.py`-style `/tts/bytes` calls, once without and once with the dictionary, then listen to 30 clips. About 60 characters each, so about 1,800 credits, about 2 minutes of audio at 750 to 800 credits a minute (SOURCE: Cartesia pricing). On the Pro plan that is about $0.09, and the free tier's 20K credits covers it (ASSUMED plan); I did not run it. The same pass renders `sonic-3.6` for the case question. Hearing check: speak the 15 names into a phone recording and run it through Nova-3 with and without keyterms; under 1 minute of audio, a few cents.

Evals: the harness is text only and roughens caller lines (evals/transcribe.py), so it cannot judge voice. It can check what the server controls: (1) a scenario where the caller gives "Kosciuszko Street" with transcription errors, with `read_back: before_booking` and a booking in the right ZIP; (2) a Houston Street caller gets `out_of_area` and no booking; (3) unit tests that the list validates (no duplicates, every entry has a respelling and a source), that the filter rewrites chunked text, and that the stored read-back sentence is unchanged. Add the names to the real-call script among the 10 real calls per stack (evals/live-calls.md).

## 4. Recommendation, and what would change it

Do A for speaking and add Deepgram keyterms for hearing, both fed from one tenant list of the names Jackson accepts. Run the clip test first; if the dictionary does not take effect on the plugin's API version, or a name needs phrase logic, fall back to B. Skip C. Effort about a day plus the clip test; the main risk is case-sensitive matching, handled by writing keys as the server writes them. Nothing here edits a high-touch doc.

What would change it: the clips show `sonic-3` already says most names right (then keep only the failures); Cartesia's dictionary needs a newer API version than the plugin sends (use B); Retell or Vapi wins the bake-off (port the list into their fields); keyterms add measurable delay (drop them and test hearing only).

## 5. Open questions for Jackson

1. Which Kosciuszko should the voice say? A: koss-ee-OSS-koh. B: kuh-SHOOS-koh. C: ask a local (Paul) first. Recommend A, since the guide that calls it most common is a Brooklyn one, and accept all three when hearing.
2. Case matching: A: stay on `sonic-3` and write keys as the server writes them. B: test `sonic-3.6` in the clip run and move if it is no slower. Recommend A, and B only as a clip-test side check, since the voice stack is settled (DECISIONS.md 006).
3. List size: A: the 15 above, only names with a source. B: a 50-name starter list. Recommend A; add names as real calls find misses.
4. Keyterms: A: on for all names, about $12 a month. B: on only after the 10 real calls show misheard streets. Recommend A, with the cost noted in docs.
5. Clip test: may I run the 30-clip Cartesia check (about $0.09) when you say go? Recommend yes.

## 6. Sources

Accessed 2026-11-03. Read in full unless marked.
- Cartesia custom pronunciations: https://docs.cartesia.ai/build-with-cartesia/sonic-3/custom-pronunciations
- Cartesia phonemes guide: https://docs.cartesia.ai/build-with-cartesia/capability-guides/phonemes
- Cartesia create dictionary API: https://docs.cartesia.ai/api-reference/pronunciation-dicts/create.md
- Cartesia pricing: https://cartesia.ai/pricing
- LiveKit Cartesia plugin: https://docs.livekit.io/agents/models/tts/plugins/cartesia/ ; community post 2026-04-08: https://community.livekit.io/t/pronunciation-dictionary-limitations-in-inference-cartesia-sonic-3/812
- Deepgram keyterm prompting: https://developers.deepgram.com/docs/keyterm ; pricing: https://deepgram.com/pricing
- Retell create agent API: https://docs.retellai.com/api-references/create-agent
- Vapi pronunciation dictionaries: https://docs.vapi.ai/assistants/pronunciation-dictionaries ; Deepgram on Vapi (snippet only): https://docs.vapi.ai/providers/transcriber/deepgram
- WNYC How to Speak New York (undated, audio): https://project.wnyc.org/pronunciation/
- Thrillist pronunciation guide, 2014-12-01: https://www.thrillist.com/entertainment/new-york/new-york-city-pronunciation-guide
- Brownstoner, 2015-08-15: https://www.brownstoner.com/brooklyn-life/how-pronounce-utrecht-canarsie-conselyea-middagh-dekalb-schermerhorn-joralemon-kosciuszko
- Secret NYC, 2025-08-26: https://secretnyc.co/hardest-nyc-places-to-pronounce/
- Village Voice, 2011-07-01: https://www.villagevoice.com/?p=261886
- Forgotten NY, Cortelyou Road (snippet only): https://forgotten-ny.com/2009/12/cortelyou-road-brooklyn/amp
- Wikipedia (IPA or respelling): https://en.wikipedia.org/wiki/Houston_Street , https://en.wikipedia.org/wiki/DeKalb_Avenue , https://en.wikipedia.org/wiki/Gowanus,_Brooklyn , https://en.wikipedia.org/wiki/Van_Wyck_Expressway , https://en.wikipedia.org/wiki/Kosciuszko_Bridge , https://en.wikipedia.org/wiki/Todt_Hill , https://en.wikipedia.org/wiki/Arrochar,_Staten_Island
- Usenet "Pronunciation of Sutphin", 2006-12-25 (via Exa): https://nyc.general.narkive.com/nWIyCpXN/pronunciation-of-sutphin
- NYC Transit Forums pronunciation thread, 2012-05-09 (via Exa; Kissena, weak): https://www.nyctransitforums.com/topic/34936-the-streetcity-pronunciation-thread/
- Repo files read: config/models.yaml, voice/livekit/worker.py, call.py, clips.py, tenants/summit-air/agent.yaml, app/tools.py, evals/transcribe.py, plugin source in `.venv` (livekit-plugins-cartesia and -deepgram 1.8.10).
