# Where Brian's rules live

Each color is one layer: what the model is told (the prompt), what the server decides in code, what sits in company and trade config, the side model, and what the voice socket and the reply filter do on the way in and out. Built from the code at the commit that added this page; the files named in each box are the source.

## One caller line, every layer

```mermaid
flowchart TB
  classDef outside fill:#e4e4e7,stroke:#71717a,color:#27272a
  classDef socket fill:#c9eef2,stroke:#1d8b99,color:#073a41
  classDef code fill:#cdeccf,stroke:#2e8b3d,color:#0d3315
  classDef tenant fill:#ffe9a3,stroke:#b38600,color:#4a3300
  classDef trade fill:#f5c6b8,stroke:#b8442a,color:#4d1a0d
  classDef side fill:#e2d0f7,stroke:#7b4bc4,color:#2e1458
  classDef prompt fill:#cfe3ff,stroke:#2f6fd0,color:#0b2a55
  classDef filter fill:#f9cfe0,stroke:#c2417a,color:#561432

  subgraph RIN["Retell, in"]
    A1["Caller speaks"]:::outside
    A2["Speech to text and turn-taking"]:::outside
  end
  subgraph SOCKIN["Voice socket, caller side (voice/retell_llm/call.py)"]
    K1["Join the caller's words since the agent last spoke"]:::socket
    K2["Agent line began before the caller's: the caller's earlier words are kept"]:::socket
  end
  subgraph SRV["Server turn check (app/safety.py, app/dead_air.py)"]
    T1{"Gas or CO phrase in the line?"}:::code
    T2["Server speaks the safety lines and menu. Model not asked."]:::code
    T3{"Hold on, or an aside?"}:::code
    T4["Hold: agent says a short yes. Aside: model says nothing."]:::code
  end
  subgraph CFG["Config the server reads"]
    C1["Tenant: 'If you're smelling gas, the safest thing is to get everyone outside and call 9-1-1'"]:::tenant
    C2["Trade: gas phrases such as 'smelling gas'"]:::trade
  end
  SM1["Side model: danger word in the line, active_hazard scores it, 0.8 or over gets hazard lines"]:::side
  subgraph MOD["Model and prompt (retell/socket/prompt.md)"]
    M1["The model reads the prompt and writes a reply or calls a tool"]:::prompt
    P1["How you talk: 'Never confirm details yourself.'"]:::prompt
    P2["Tool results: 'next: what to do next. Do it.'"]:::prompt
    P3["Safety: 'Never give safety advice of your own and never repeat it.'"]:::prompt
    P4["Before the first tool: 'one question a turn'"]:::prompt
    P5["Closing: 'the closing is said for you.'"]:::prompt
  end
  subgraph TOOLS["Tool server gates (app/flow.py, app/tools.py)"]
    G1{"Model calls a tool"}:::code
    G2["Refused: find_slots before the caller confirms the read-back"]:::code
    G3["Refused: book_appointment before the visit questions. Server asks them itself."]:::code
    G4["Result has say, spoken as written, and next, the model's next step"]:::code
    G5["Priority: no heat Oct 1 to May 31 is at least High"]:::tenant
  end
  SM2["Side model: vulnerable_person scores the call, 0.65 or over raises priority"]:::side
  subgraph SOCKOUT["Voice socket, reply side"]
    O1["Server's say is spoken word for word and ends the turn"]:::socket
    O2["Model's step narration such as 'Let me check' is never said"]:::socket
    O3["Empty model reply: asked once more"]:::socket
    O4["Slow tool: a hold phrase after 1.4 s"]:::socket
  end
  subgraph FIL["Reply filter (prompts/assemble.py SpokenReply)"]
    F1["Drops a sentence that restates the caller's address, ZIP or phone"]:::filter
    F2["Drops 'the rules say' and 'knowledge base'"]:::filter
    F3["ZIP and 'ending in' digits said one by one"]:::filter
  end
  subgraph ROUT["Retell, out"]
    B1["Cartesia voice speaks it"]:::outside
  end

  A1 --> A2 --> K1
  K1 -.- K2
  K1 --> T1
  C2 -.-> T1
  T1 -->|"yes"| T2
  C1 -.-> T2
  T2 -->|"sent as written"| B1
  T1 -->|"no"| T3
  T1 -.->|"no phrase"| SM1
  SM1 -.->|"yes"| T2
  T3 -->|"hold or aside"| T4
  T4 --> B1
  T3 -->|"neither"| M1
  P1 -.- M1
  P2 -.- M1
  P3 -.- M1
  P4 -.- M1
  P5 -.- M1
  M1 -->|"tool call"| G1
  G1 --> G2
  G1 --> G3
  G5 -.-> G1
  SM2 -.-> G5
  G2 --> G4
  G3 --> G4
  G1 -->|"allowed"| G4
  G4 -->|"next goes back to the model"| M1
  G4 -->|"say"| O1
  O1 --> B1
  M1 -->|"its own words"| O2
  O2 --> F1
  F1 --> F2
  F2 --> F3
  F3 --> B1
  M1 -.-> O3
  G1 -.-> O4
```

A caller line goes through the voice socket and the server first; the model only sees it if no safety line or hold takes it, and the server's own sentences (say) override the model's words.

## If-then decisions on a no-heat or gas call

```mermaid
flowchart TB
  classDef outside fill:#e4e4e7,stroke:#71717a,color:#27272a
  classDef code fill:#cdeccf,stroke:#2e8b3d,color:#0d3315
  classDef tenant fill:#ffe9a3,stroke:#b38600,color:#4a3300
  classDef trade fill:#f5c6b8,stroke:#b8442a,color:#4d1a0d
  classDef side fill:#e2d0f7,stroke:#7b4bc4,color:#2e1458
  classDef prompt fill:#cfe3ff,stroke:#2f6fd0,color:#0b2a55

  S0["Caller describes the problem"]:::outside
  S1["Model picks the job type from the caller's words"]:::prompt
  S0 --> S1

  subgraph SAFE["1. Hazard check, before anything else"]
    H1{"Gas or CO phrase?"}:::code
    H1a["Trade phrases: 'smelling gas', CO alarm"]:::trade
    H2{"Danger word? Side model scores active_hazard"}:::side
    H3["Tenant lines: 'If you're smelling gas, the safest thing is to get everyone outside and call 9-1-1 or the gas company from there.'"]:::tenant
    H4["Menu: 'I can connect you with someone on our team right now, or book an appointment with one of our technicians to check it out. And if it's really urgent, we can send someone out right away to help.'"]:::tenant
    H1a -.-> H1
  end
  S1 --> H1
  H1 -->|"no"| H2
  H1 -->|"yes"| H3
  H2 -->|"0.8 or over"| H3
  H3 --> H4
  H4 -->|"a person now"| HP["Server hands off to a person, no details taken"]:::code
  H4 -->|"an appointment"| HA["Job type safety_check, base priority High"]:::trade
  H4 -->|"right away"| HR["Rule right_away raises to Urgent"]:::trade
  HA --> HN["Booking adds: 'the technician can only work on it once it's been checked and found safe.'"]:::tenant

  H2 -->|"under 0.8"| P0["Next steps come from the server: how bad, since when, system, phone"]:::code

  subgraph PRI["2. Priority, raised never lowered"]
    P1["Start from inside and outside temperature: below 55 F inside is Urgent, below 65 F is High"]:::tenant
    P2["No heat Oct 1 to May 31, no cooling Jun 15 to Sep 15: at least High"]:::tenant
    P3["'completely out', 'banging', 'iced up': High"]:::trade
    P4["Someone 60 or older: raises priority"]:::tenant
    P5["Side model vulnerable_person, 0.65 or over: raises priority"]:::side
    P1 --> P2 --> P3 --> P4 --> P5
  end
  P0 --> P1
  P5 --> L{"Priority level"}:::code

  L -->|"Urgent"| D0
  L -->|"High"| R1
  L -->|"Normal or Low"| W1

  subgraph HIGH["3. High: ask once"]
    R1["Read-back first: 'So that's the address, and the phone. That's all correct, right?'"]:::tenant
    R2["Gate: no openings until the caller confirms the read-back"]:::code
    R3["'Can this wait for a scheduled visit, or do you need someone out right away?'"]:::tenant
    R1 --> R2 --> R3
  end
  R3 -->|"can wait, schedule, no rush"| W1
  R3 -->|"right now, asap, send someone"| U1["Rule right_away raises to Urgent"]:::trade
  U1 --> D0
  HR --> D0

  subgraph URG["4. Urgent: dispatch, no windows offered"]
    D0{"Dispatch only at Urgent, sentence is also the read-back"}:::code
    D1{"After hours, 8 PM to 8 AM?"}:::code
    D0 --> D1
    D2["In hours: 'We'll get a technician out to you right away at the address. Expect them within the next hour.'"]:::tenant
    D3{"On call roster: 2 technicians, 120 minutes a job"}:::code
    D4["One free: 'Our on-call technician will call you within twenty minutes and head out to you.'"]:::tenant
    D5["Both out: 'the first one free will call you and head to the address after their current job, in about X.'"]:::tenant
    D1 -->|"no"| D2
    D1 -->|"yes"| D3
    D3 -->|"one free"| D4
    D3 -->|"both busy"| D5
  end

  subgraph APPT["5. Appointment"]
    W1["Soonest windows offered, one at a time"]:::code
    W2{"Any window open?"}:::code
    W3["'Unfortunately we're fully booked for the next two weeks. I can have someone call you back as soon as an opening comes up.'"]:::tenant
    W4["Caller picks a window"]:::code
    W5["Server asks the visit questions itself: approval, someone home, gate code or buzzer"]:::code
    W6["'That window just filled up. The next one I have is ...'"]:::tenant
    W1 --> W2
    W2 -->|"none"| W3
    W2 -->|"yes"| W4
    W4 --> W5
    W5 -->|"window gone"| W6
    W5 -->|"booked"| W7["Booked"]:::code
  end

  D2 --> E1
  D4 --> E1
  D5 --> E1
  W7 --> E1
  W3 --> E2
  HP --> E2
  E1["Call ends with a visit"]:::code
  E2["Call ends with no visit"]:::code
  subgraph LAB["6. Label, set by the server when the call ends"]
    X1["Emergency dispatch: a technician sent right away"]:::code
    X2["Scheduled: a visit booked, with its priority as the reason"]:::code
    X3["No visit: callback promised, passed to a person, nothing booked"]:::code
    X4["Check: booking differs from the confirmed read-back, so an Urgent flag for a person"]:::code
  end
  E1 -->|"dispatch stands"| X1
  E1 -->|"any other visit"| X2
  E1 -.-> X4
  E2 --> X3
```

The server walks this tree on every call; the quoted lines are word for word from the tenant config, and the model says them as written.

## Legend

| Layer | Where it lives | Example |
|---|---|---|
| Prompt (blue) | `retell/socket/prompt.md` | "Never give safety advice of your own and never repeat it." |
| Server code (green) | `app/flow.py`, `tools.py`, `priority.py`, `safety.py`, `dead_air.py`, `consistency.py`, `labels.py` | find_slots is refused until the caller confirms the read-back. |
| Tenant config (yellow) | `tenants/summit-air/agent.yaml`, `config.yaml` | Heating season Oct 1 to May 31; on call: 2 technicians, 120 minutes a job. |
| Trade config (terracotta) | `trades/hvac/agent.yaml` | "completely out" raises a no-heat call to High. |
| Side model (purple) | `prompts/side/questions.yaml`, `app/side_model.py` | vulnerable_person at 0.65 or over raises priority. |
| Voice socket (teal) | `voice/retell_llm/call.py`, `llm_socket.py` | Caller words spoken over the end of the agent's line are kept. |
| Reply filter (pink) | `prompts/assemble.py` (SpokenReply) | ZIP digits and "ending in" digits are said one by one. |
| Retell and caller (gray) | Outside this repo | Speech to text, turn-taking, Cartesia voice. |
