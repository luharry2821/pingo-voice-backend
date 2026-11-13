# STT results: assemblyai_raw.jsonl

109 cases. Error rates: lower is better, except language-ID accuracy (higher is better). n = cases / reference units (words, or characters for CER); language-ID metrics are per final turn.

| KPI | Value | n |
|---|---|---|
| WER (normalized, all word-based cases) | 5.85% | 88 / 598 |
| Semantic WER (LLM judge) | 2.51% | 88 |
| FWER (raw, no normalization) | 8.42% | 88 / 582 |
| cpWER (2-speaker) | 17.78% | 4 / 45 |
| Sentence error rate | 22.94% | 109 |
| Missed entity rate | 5.88% (2/34) | 18 cases |
| Missed entity rate, entity set only | 4.17% (1/24) | 10 cases |
| Language-ID accuracy (higher is better) | 94.92% | 112 / 118 turns |
| Confident-wrong rate (wrong label, confidence ≥ 0.6) | 1.69% | 2 / 118 turns |
| Language-ID accuracy en | 96.77% | 30 / 31 turns |
| Language-ID accuracy de | 100.0% | 12 / 12 turns |
| Language-ID accuracy es | 82.14% | 23 / 28 turns |
| Language-ID accuracy fr | 100.0% | 7 / 7 turns |
| Language-ID accuracy it | 100.0% | 3 / 3 turns |
| Language-ID accuracy hi | 100.0% | 1 / 1 turns |
| Language-ID accuracy ja | 100.0% | 9 / 9 turns |
| Language-ID accuracy zh | 100.0% | 7 / 7 turns |
| Language-ID accuracy ko | 100.0% | 5 / 5 turns |
| Language-ID accuracy en+hi | 100.0% | 7 / 7 turns |
| Language-ID accuracy en+es | 100.0% | 8 / 8 turns |
| WER en | 0.0% | 10 / 84 |
| WER de | 2.86% | 6 / 35 |
| WER es | 0.0% | 3 / 18 |
| WER fr | 0.0% | 2 / 14 |
| WER it | 16.67% | 1 / 6 |
| CER ja | 0.0% | 6 / 59 |
| CER zh | 0.0% | 5 / 35 |
| CER ko | 0.0% | 4 / 32 |
| Code-switching WER en_hi | 32.14% | 5 / 28 |
| Code-switching WER en_es | 13.33% | 7 / 45 |
| Formatting cases: FWER / WER | 2.7% / 2.56% | 6 |
| Missed entity rate (english) | 0.0% (0/1) | 1 cases |
| Missed entity rate (entity) | 4.17% (1/24) | 10 cases |
| Missed entity rate (formatting) | 12.5% (1/8) | 6 cases |
| Missed entity rate (planted_mistake) | 0.0% (0/1) | 1 cases |

## Hard set only (31 cases)

| KPI | Value | n |
|---|---|---|
| WER | 10.83% | 26 / 157 |
| Semantic WER | 7.01% | 26 |
| FWER | 15.82% | 26 / 158 |
| CER | 5.71% | 5 / 35 |
| Sentence error rate | 48.39% | 31 |

## Hesitation clips (3)

Split into more than one turn: 3 of 3 (turns per clip: {'ps01': 3, 'h13': 3, 'h14': 3}). WER 12.0%.

## Latency (client-side, ms)

| Metric | p50 | p90 | p95 | n |
|---|---|---|---|---|
| ttft_ms | 1055 | 1179 | 1265 | 109 |
| ttct_ms | 1124 | 2802 | 2835 | 101 |
| eot_latency_ms | 1124 | 2802 | 2835 | 101 |
| ttft_from_speech_start_ms | 368 | 460 | 508 | 109 |

No end-of-turn within 4 s of silence: 1 clips. Rows with a provider session/request id: 109 of 109.

## Missed entities

2 of 34 entities missed after the same normalization as WER/CER.

| id | entity (as spoken) | Pingo heard |
|---|---|---|
| fm05 | veintiún euros | Son 21 euros. |
| ent02 | Siobhán | Mi profesora se llama Siobhan. |

## Mislabeled language ID

Final turns whose language_code did not match the case's expected language. Code-switching cases count either expected language as correct.

6 of 118 turns.

| id | text | expected | got | confidence |
|---|---|---|---|---|
| en07 | Was my pronunciation of croissant okay? | en | fr | 0.277492 |
| fm06 | Mi examen es el 15 de julio. | es | ca | 0.206431 |
| pm01 | Yo es hambre. | es | fr | 0.255212 |
| h17 | C. | es | vi | 0.148354 |
| h13 | 就。 | es | zh | 1.0 |
| h13 | 哎。 | es | zh | 1.0 |

## Failed cases

| id | reference | Pingo heard | errors |
|---|---|---|---|
| de02 | Ich lerne seit drei Monaten Deutsch. | Ich lerne seit 3 Monaten Deutsch. | S: drei→3 |
| it01 | Dov'è la stazione dei treni? | Dove è la stazione dei treni? | S: dov→dove |
| hi01 | मुझे एक coffee चाहिए, please. | मुझे एक कॉफ़ी चाहिए, प्लीज़। | S: coffee→कॉफ़ी; S: please→प्लीज़ |
| hi02 | Aaj मेरी Spanish class है। | आज मेरी स्पैनिश क्लास है। | S: aaj→आज; S: spanish→स्पैनिश; S: class→क्लास |
| hi03 | मैं weekend पर movie देखने जा रहा हूँ। | मैं वीकेंड पर मूवी देखने जा रहा हूँ। | S: weekend→वीकेंड; S: movie→मूवी |
| hi04 | यह sentence बहुत difficult है। | यह सेंटेंस बहुत डिफिकल्ट है। | S: sentence→सेंटेंस; S: difficult→डिफिकल्ट |
| cs02 | How do you say tengo hambre in English? | How do you say tango hambra in English? | S: tengo→tango; S: hambre→hambra |
| fm05 | Son veintiún euros. | Son 21 euros. | S: veintiún→21 |
| pm14 | मेरी brother बहुत smart है। | मेरी ब्रदर बहुत स्मार्ट है। | S: brother→ब्रदर; S: smart→स्मार्ट |
| pm16 | Yo tango hambre. | Yo tengo hambre. | S: tango→tengo |
| pm17 | Je suis allée au cinéma. | Je suis allé au cinéma. | S: allée→allé |
| pm20 | I goed to school yesterday. | I go to school yesterday. | S: goed→go |
| pm21 | Tengo un pero en casa. | Tengo un perro en casa. | S: pero→perro |
| pm22 | Feliz ano nuevo. | ¡Feliz año nuevo! | S: ano→año |
| pm15 | I want los tacos, pero no quiero la frijoles. | Igual los tacos, pero no quiero la frijoles. | S: i→igual; D: want |
| ps01 | Quiero... um... un café. | Quiero. Ume. Un café. | S: um→ume |
| h01 | Quiero un caro rojo. | Quiero un carro rojo. | S: caro→carro |
| h02 | Vamos a la caza de mi abuela. | Vamos a la casa de mi abuela. | S: caza→casa |
| h05 | 私を日本へ行きます。 | 私は日本へ行きます。 | S: を→は |
| h07 | 我有三个书。 | 我有三个叔。 | S: 书→叔 |
| h10 | मैं कल school जाएगा था। | मैं कल स्कूल जाएगा था। | S: school→स्कूल |
| h11 | Te voy a textear later, okay? | Te voy a testear la ter, ¿ok? | I: testear; S: textear→la; S: later→ter; S: okay→ok |
| h17 | Sí. | C. | S: sí→c |
| h13 | Yo... eh... yo fui, no, yo voy al mercado. | 就。 哎。 Yo fui, no, yo voy al mercado. | S: yo→就; S: eh→哎 |
| ent02 | Mi profesora se llama Siobhán. | Mi profesora se llama Siobhan. | S: siobhán→siobhan |
