# Side model 3: a tighter question and a 0.65 threshold

Jackson saw the at-risk side model overfire, and asked for the question to stop counting health conditions that heat or cold doesn't make dangerous, and people near the home rather than in it. Every number here is MEASURED with `evals/decision-models/run.py`, OpenAI Decisions only, at a 0.65 threshold, about $0.004 a run.

**The lines.** The 80 original lines, written blind to the phrase lists (37 at risk, 38 not, 5 borderline left out of scores), plus 18 new ones written after reading the question, so they are a targeted test rather than a blind one: 12 hard negatives (a bad back, a broken wrist, allergies, ADHD, a migraine, a neighbor upstairs, the old lady next door, a mother who lives in Florida, a grandmother who passed, a nurse's job, a doctor's opinion, a 45-year-old father) and 6 hard positives (COPD, 72 and freezing, heart surgery, diabetic and numb, asthma, a newborn).

| Wording | Blind 80, alone | Blind 80, with the phrase rules | New 18, alone | New 18, with the phrase rules |
|---|---|---|---|---|
| Old (`20261104-234327`) | caught 37 of 37, 1 false alarm of 38 | caught 37 of 37, 7 false alarms | caught 6 of 6, 6 false alarms of 12 | caught 6 of 6, 8 false alarms |
| First rewrite (`20261104-234409`) | missed 8 of 43 at-risk lines across both sets | | | |
| Final (`20261104-234455`) | caught 36 of 37, 0 false alarms | caught 37 of 37, 6 false alarms | caught 6 of 6, 0 false alarms | caught 6 of 6, 2 false alarms |

**What changed in the wording** (`prompts/side/questions.yaml`). The question now lists the groups that count (older, a baby or young child, pregnant, medical equipment, bedbound or unable to get around, and named conditions: heart or lung disease, asthma, COPD, diabetes, sickle cell, recent surgery, or an illness the caller says the temperature makes worse) and says being in a group is enough. It names what doesn't count: an injury or condition heat or cold doesn't make dangerous (a bad back, a broken bone, allergies, a headache, ADHD), even if it stops someone doing a task; a neighbor or anyone in a separate apartment or house; someone who lives elsewhere, used to, drops by now and then, or is away; anyone's job. Someone on another floor of the same house counts.

**The first rewrite overcorrected.** It asked whether someone was "at higher risk from the heat or cold there", and the model then wanted the caller to tie each person to the temperature: "my grandmother is 82 and she lives on the second floor" scored 0.0, and "my pop turned ninety and he's here for the winter" 0.06. The final wording says being in a group is enough, and that another floor of the same house counts.

**Still open.** The one blind miss is "my mother just got home from the hospital after her hip surgery" (0.12), likely pulled toward "a broken bone". The phrase rules catch it ("recovering"), so the live system still raises it. The remaining false alarms with rules come from the phrase rules, not the side model: for example "my mom calls me every day, she's 90, she lives down in Florida" matches the parent rule. That is a phrase-rule question, not a side-model one.
