# Side-model quick run 20261104-234327-vulnerable_person

Question `vulnerable_person`, 98 lines (93 scored, the rest borderline), threshold 0.65, cost $0.0027. All numbers MEASURED by this run (evals/decision-models/run.py).

| System | Recall alone | Precision alone | Recall with rules | Precision with rules | Fixes | New alarms | McNemar p | p50 ms | p95 ms | Max ms | Failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| rules | 0.698 [0.549, 0.814] | 0.789 | 0.698 | 0.789 | 0 | 0 | 1.0 | 0 | 0 | 0 | 0 |
| openai-decisions | 1.0 [0.918, 1.0] | 0.86 | 1.0 | 0.741 | 13 | 7 | 0.2632 | 233 | 444 | 724 | 0 |

## rules

- Missed vp-007 (soft, p=0.0): i have a lung condition so i can't be in the heat / my ac is broken
- Missed vp-013 (soft, p=0.0): we need the old ac looked at before summer / my wife is due in september
- Missed vp-021 (soft, p=0.0): my pop turned ninety last month and he's here for the winter / the heat is dodgy
- Missed vp-023 (soft, p=0.0): my wife's seven months along and the heat is out
- Missed vp-029 (soft,borderline, p=0.0): i've lived in this house since 1974 / and the boiler finally gave out
- Missed vp-030 (soft, p=0.0): my son is two and the ac in his room won't turn on
- Missed vp-032 (soft, p=0.0): mi abuela lives with us and she gets cold real fast / the heat is out
- Missed vp-043 (soft, p=0.0): nana lives with us and she's not well / the boiler is down
- Missed vp-049 (soft, p=0.0): no heat on the third floor / my brother has sickle cell and the cold sets it off
- Missed vp-053 (soft,stt_noise, p=0.0): second floor is boiling my sister who lives with me has lupus cant take the heat
- Missed vp-064 (soft,stt_noise, p=0.0): no heat at all my wife is on die a lisis three days a week
- Missed vp-070 (soft, p=0.0): ac is not working / my husband had bypass surgery in april and the doctor said keep him out of the heat
- Missed vp-072 (explicit,stt_noise, p=0.0): i got a four year old daughter and theres no heat
- Missed vp-095 (hard_positive, p=0.0): my wife just had heart surgery and she's home recovering
- False alarm vp-003 (hard_negative, p=1.0): i think my grandfather clock is rattling or maybe it's the furnace / there's a rattle in the living room
- False alarm vp-014 (hard_negative,borderline, p=1.0): do you have a senior discount / i need a quote for a new ac
- False alarm vp-031 (hard_negative, p=1.0): my mom is 80 but she lives in florida / i'm just calling about my own heat
- False alarm vp-048 (hard_negative, p=1.0): my sister has a newborn but she lives in jersey / i'm calling about my own ac
- False alarm vp-050 (hard_negative,stt_noise, p=1.0): my father in law comes by on sundays for dinner the heat out
- False alarm vp-055 (hard_negative, p=1.0): my mother used to live here / i inherited the house and the boiler is dead
- False alarm vp-080 (hard_negative, p=1.0): the baby grand in the living room is going out of tune because the ac isn't cooling
- False alarm vp-083 (hard_negative,broad_condition_or_third_party, p=1.0): my mom calls me every day, she's 90, she lives down in Florida
- False alarm vp-090 (hard_negative,broad_condition_or_third_party, p=1.0): my grandmother used to live here, she passed last year, the furnace was hers

## openai-decisions

- Missed vp-008 (soft,borderline, p=0.0): my mother in law is coming for the whole month of december / and the heat in the guest room is dead
- Missed vp-029 (soft,borderline, p=0.08): i've lived in this house since 1974 / and the boiler finally gave out
- False alarm vp-045 (hard_negative, p=0.78): my neighbor is 90 and she told me to call you / my own boiler is also making noise
- False alarm vp-081 (hard_negative,broad_condition_or_third_party, p=0.97): my husband has a bad back so he can't get down to the basement to look at the furnace
- False alarm vp-082 (hard_negative,broad_condition_or_third_party, p=0.76): I broke my wrist last month so I can't reset the thermostat myself
- False alarm vp-084 (hard_negative,broad_condition_or_third_party, p=0.99): my neighbor upstairs is 88 and she's always complaining about the heat in the building
- False alarm vp-086 (hard_negative,broad_condition_or_third_party, p=0.93): I have allergies so that dusty smell from the vents really bothers me
- False alarm vp-087 (hard_negative,broad_condition_or_third_party, p=0.84): my son has ADHD and he keeps playing with the thermostat
- False alarm vp-089 (hard_negative,broad_condition_or_third_party, p=0.78): the old lady next door said her heat's out too
