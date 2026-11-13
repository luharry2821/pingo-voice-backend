# STT results: flux_raw.jsonl

93 cases. Error rates: lower is better. n = cases / reference units (words, or characters for CER); the confident-wrong rate is per final turn.

| KPI | Value | n |
|---|---|---|
| WER (normalized, all word-based cases) | 9.22% | 84 / 553 |
| Semantic WER (LLM judge) | 4.52% | 84 |
| FWER (raw, no normalization) | 16.33% | 84 / 539 |
| Sentence error rate | 31.18% | 93 |
| Missed entity rate | 19.35% (6/31) | 17 cases |
| Missed entity rate, entity set only | 19.05% (4/21) | 9 cases |
| Confident-wrong rate (wrong label, confidence ≥ 0.6) | not comparable | 0 / 97 turns |
| WER en, all monolingual clips | 4.37% | 22 / 183 |
| WER es, all monolingual clips | 11.59% | 24 / 138 |
| WER hi, all monolingual clips | 0.0% | 1 / 12 |
| WER en | 3.57% | 10 / 84 |
| WER de | 5.71% | 6 / 35 |
| WER es | 0.0% | 3 / 18 |
| WER fr | 0.0% | 2 / 14 |
| WER it | 33.33% | 1 / 6 |
| CER ja | 0.0% | 6 / 59 |
| CER zh | n/a | 0 / 0 |
| CER ko | n/a | 0 / 0 |
| Formatting cases: FWER / WER | 48.65% / 7.69% | 6 |
| Missed entity rate (english) | 0.0% (0/1) | 1 cases |
| Missed entity rate (entity) | 19.05% (4/21) | 9 cases |
| Missed entity rate (formatting) | 25.0% (2/8) | 6 cases |
| Missed entity rate (planted_mistake) | 0.0% (0/1) | 1 cases |

## Hard set only (28 cases)

| KPI | Value | n |
|---|---|---|
| WER | 15.92% | 26 / 157 |
| Semantic WER | 10.83% | 26 |
| FWER | 22.78% | 26 / 158 |
| CER | 0.0% | 2 / 18 |
| Sentence error rate | 50.0% | 28 |

## Hesitation clips (3)

Split into more than one turn: 3 of 3 (turns per clip: {'ps01': 3, 'h13': 2, 'h14': 2}). WER 8.0%.

## Latency (client-side, ms)

| Metric | p50 | p90 | p95 | n |
|---|---|---|---|---|
| ttft_ms | 1058 | 1294 | 1334 | 93 |
| ttct_ms | 982 | 1993 | 2222 | 90 |
| eot_latency_ms | 982 | 1993 | 2222 | 90 |
| ttft_from_speech_start_ms | 356 | 577 | 621 | 93 |

Rows with a provider session/request id: 93 of 93.

## Missed entities

6 of 31 entities missed after the same normalization as WER/CER.

| id | entity (as spoken) | Pingo heard |
|---|---|---|
| fm01 | 7:30 | My class is at seven thirty on Monday. |
| fm06 | 15 de julio | Mi examen es el quince de julio. |
| ent01 | 3 de marzo | Vivo en Guadalajara desde el tres de marzo. |
| ent02 | Siobhán | Mi profesora se llama Siován. |
| ent04 | 21. April | Ich wohne in München seit dem einundzwanzigsten April. |
| ent05 | 2019 | Je m'appelle François et j'habite à Lyon depuis deux mille dix neuf. |

## Mislabeled language ID

Final turns whose language_code did not match the case's expected language. Code-switching cases count either expected language as correct.

3 of 97 turns.

| id | text | expected | got | confidence |
|---|---|---|---|---|
| ps01 | Un caffè. | es | it | — |
| h12 | Vuoi annunciar con mi friend? | en, es | it | — |
| h20 | Quero un cafe or favor. | es | en | — |

## Failed cases

| id | reference | Pingo heard | errors |
|---|---|---|---|
| en03 | What's the difference between ser and estar? | What's the difference between serenestar? | S: ser→serenestar; D: and; D: estar |
| de04 | Wo ist die nächste U-Bahn-Station? | Wo ist die nächste U-Bahnstation? | S: bahn→bahnstation; D: station |
| it01 | Dov'è la stazione dei treni? | Dove la stazione dei treni? | S: dov→dove; D: è |
| hi02 | Aaj मेरी Spanish class है। | आज मेरी Spanish class है. | S: aaj→आज |
| hi03 | मैं weekend पर movie देखने जा रहा हूँ। | मैं weekend पर movie देखने जा रहा हूं. | S: हूँ→हूं |
| cs02 | How do you say tengo hambre in English? | How do you say tango haembra in English? | S: tengo→tango; S: hambre→haembra |
| cs03 | Mi hermano is coming to visit next week. | Mihirmano is coming to visit next week. | S: mi→mihirmano; D: hermano |
| cs04 | I think the answer is muy bien. | I think the answer is Muibian. | S: muy→muibian; D: bien |
| fm01 | My class is at 7:30 on Monday. | My class is at seven thirty on Monday. | S: 7→730; D: 30 |
| fm06 | Mi examen es el 15 de julio. | Mi examen es el quince de julio. | S: 15→quince |
| pm13 | 私は学生をです。 | 私は学生です。 | D: を |
| pm16 | Yo tango hambre. | Yo tengo hambre. | S: tango→tengo |
| pm17 | Je suis allée au cinéma. | Je suis allé au cinéma. | S: allée→allé |
| pm18 | Ella son muy simpática. | Ellas son muy simpática. | S: ella→ellas |
| pm20 | I goed to school yesterday. | I go to school yesterday. | S: goed→go |
| pm15 | I want los tacos, pero no quiero la frijoles. | Igual los tacos, pero no quiero la frijoles. | S: i→igual; D: want |
| ps01 | Quiero... um... un café. | Quiero UM. Un caffè. | S: café→caffè |
| h01 | Quiero un caro rojo. | Quiero un carro rojo. | S: caro→carro |
| h02 | Vamos a la caza de mi abuela. | Vamos a la casa de mi abuela. | S: caza→casa |
| h11 | Te voy a textear later, okay? | Te voy a testear la té, ¿ok? | I: testear; S: textear→la; S: later→té; S: okay→ok |
| h12 | Voy a lunchear con mi friend. | Vuoi annunciar con mi friend? | S: voy→vuoi; S: a→annunciar; D: lunchear |
| h19 | I am very hungry, I want to eat now. | You're very hungry. I want to eat now. | S: i→you; S: am→are |
| h20 | Quiero un café, por favor. | Quero un cafe or favor. | S: quiero→quero; S: café→cafe; S: por→or |
| h21 | Ayer yo va al mercado y compré una manzanas y la tomates porque mi madre es muy hambre. | Ayer yo va al mercado y compré una manzana si la tomate es porque mi madre es muy hambre. | S: manzanas→manzana; S: y→si; I: tomate; S: tomates→es |
| h13 | Yo... eh... yo fui, no, yo voy al mercado. | Yo. Yo fui, no, yo voy al mercado. | D: eh |
| ent01 | Vivo en Guadalajara desde el 3 de marzo. | Vivo en Guadalajara desde el tres de marzo. | S: 3→tres |
| ent02 | Mi profesora se llama Siobhán. | Mi profesora se llama Siován. | S: siobhán→siován |
| ent04 | Ich wohne in München seit dem 21. April. | Ich wohne in München seit dem einundzwanzigsten April. | S: 21→einundzwanzigsten |
| ent05 | Je m'appelle François et j'habite à Lyon depuis 2019. | Je m'appelle François et j'habite à Lyon depuis deux mille dix neuf. | I: deux; I: mille; I: dix; S: 2019→neuf |
