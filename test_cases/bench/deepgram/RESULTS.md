# STT results: deepgram_raw.jsonl

109 cases. Error rates: lower is better, except language-ID accuracy (higher is better). n = cases / reference units (words, or characters for CER); language-ID metrics are per final turn.

| KPI | Value | n |
|---|---|---|
| WER (normalized, all word-based cases) | 9.7% | 88 / 598 |
| Semantic WER (LLM judge) | 4.01% | 88 |
| FWER (raw, no normalization) | 14.26% | 88 / 582 |
| cpWER (2-speaker) | 8.89% | 4 / 45 |
| Sentence error rate | 28.44% | 109 |
| Missed entity rate | 20.59% (7/34) | 18 cases |
| Missed entity rate, entity set only | 20.83% (5/24) | 10 cases |
| Language-ID accuracy (higher is better) | 96.52% | 111 / 115 turns |
| Confident-wrong rate (wrong label, confidence ≥ 0.6) | not comparable | 0 / 115 turns |
| Language-ID accuracy en | 100.0% | 31 / 31 turns |
| Language-ID accuracy de | 91.67% | 11 / 12 turns |
| Language-ID accuracy es | 92.0% | 23 / 25 turns |
| Language-ID accuracy fr | 100.0% | 7 / 7 turns |
| Language-ID accuracy it | 100.0% | 3 / 3 turns |
| Language-ID accuracy hi | 100.0% | 1 / 1 turns |
| Language-ID accuracy ja | 100.0% | 9 / 9 turns |
| Language-ID accuracy zh | 100.0% | 7 / 7 turns |
| Language-ID accuracy ko | 100.0% | 5 / 5 turns |
| Language-ID accuracy en+hi | 100.0% | 7 / 7 turns |
| Language-ID accuracy en+es | 87.5% | 7 / 8 turns |
| WER en | 2.38% | 10 / 84 |
| WER de | 8.57% | 6 / 35 |
| WER es | 0.0% | 3 / 18 |
| WER fr | 0.0% | 2 / 14 |
| WER it | 0.0% | 1 / 6 |
| CER ja | 5.08% | 6 / 59 |
| CER zh | 0.0% | 5 / 35 |
| CER ko | 0.0% | 4 / 32 |
| Code-switching WER en_hi | 10.71% | 5 / 28 |
| Code-switching WER en_es | 26.67% | 7 / 45 |
| Formatting cases: FWER / WER | 16.22% / 7.69% | 6 |
| Missed entity rate (english) | 0.0% (0/1) | 1 cases |
| Missed entity rate (entity) | 20.83% (5/24) | 10 cases |
| Missed entity rate (formatting) | 25.0% (2/8) | 6 cases |
| Missed entity rate (planted_mistake) | 0.0% (0/1) | 1 cases |

## Hard set only (31 cases)

| KPI | Value | n |
|---|---|---|
| WER | 19.11% | 26 / 157 |
| Semantic WER | 10.19% | 26 |
| FWER | 24.05% | 26 / 158 |
| CER | 0.0% | 5 / 35 |
| Sentence error rate | 41.94% | 31 |

## Missed entities

7 of 34 entities missed after the same normalization as WER/CER.

| id | entity (as spoken) | Pingo heard |
|---|---|---|
| fm05 | veintiún euros | Son 21 euros. |
| fm04 | March 5th, 2027 | My lesson is on 03/05/2027. |
| ent02 | Siobhán | Mi profesora se llama Xiobán. |
| ent03 | June 14th | I'm meeting Priya at Starbucks on June 14. |
| ent04 | 21. April | Ich wohne in München seit dem einundzwanzigsten April. |
| ent09 | Nguyen | Dr. Ngwin booked a flight to Sao Paulo on November 3. |
| ent09 | November 3rd | Dr. Ngwin booked a flight to Sao Paulo on November 3. |

## Mislabeled language ID

Final turns whose language_code did not match the case's expected language. Code-switching cases count either expected language as correct.

4 of 115 turns.

| id | text | expected | got | confidence |
|---|---|---|---|---|
| h12 | Voi aluncear con mi friendo. | en, es | it | — |
| h17 | Si | es | id | — |
| h18 | Danke. | de | id | — |
| h20 | Quero Uncafé or favor. | es | en | — |

## Failed cases

| id | reference | Pingo heard | errors |
|---|---|---|---|
| en03 | What's the difference between ser and estar? | What's the difference between Sarah and Esther? | S: ser→sarah; S: estar→esther |
| de02 | Ich lerne seit drei Monaten Deutsch. | Ich lerne seit 3 Monaten Deutsch. | S: drei→3 |
| de04 | Wo ist die nächste U-Bahn-Station? | Wo ist die nächste U-Bahnstation? | S: bahn→bahnstation; D: station |
| ja05 | これはいくらですか。 | これはいくら | D: で; D: す; D: か |
| hi01 | मुझे एक coffee चाहिए, please. | मुझे एक कॉफी चाहिए, please. | S: coffee→कॉफी |
| hi02 | Aaj मेरी Spanish class है। | आज मेरी Spanish class है. | S: aaj→आज |
| hi03 | मैं weekend पर movie देखने जा रहा हूँ। | मैं weekend पर movie देखने जा रहा हूं. | S: हूँ→हूं |
| cs02 | How do you say tengo hambre in English? | How do you say tango hambur in English? | S: tengo→tango; S: hambre→hambur |
| cs04 | I think the answer is muy bien. | I think the answer is Muibian. | S: muy→muibian; D: bien |
| fm05 | Son veintiún euros. | Son 21 euros. | S: veintiún→21 |
| fm04 | My lesson is on March 5th, 2027. | My lesson is on 03/05/2027. | S: march→3; S: 5th→5 |
| sp02 | How do I say good night in German? You say gute Nacht. | How do I say goodnight in German? You say gutnacht. | S: good→goodnight; D: night; S: gute→gutnacht; D: nacht |
| pm07 | Gestern ich bin ins Kino gegangen. | Gestern bin ich ins Kino gegangen. | I: bin; D: bin |
| pm13 | 私は学生をです。 | 私は学生王です。 | S: を→王 |
| pm16 | Yo tango hambre. | Yo tengo hambre. | S: tango→tengo |
| pm17 | Je suis allée au cinéma. | Je suis allé au cinéma. | S: allée→allé |
| pm18 | Ella son muy simpática. | Ellas son muy simpáticas. | S: ella→ellas; S: simpática→simpáticas |
| pm20 | I goed to school yesterday. | I go to school yesterday. | S: goed→go |
| pm15 | I want los tacos, pero no quiero la frijoles. | Igual los tacos, pero no quiero las frijoles. | S: i→igual; D: want; S: la→las |
| ps01 | Quiero... um... un café. | Quiero. U m. اون كافه. | I: u; S: um→m; S: un→اون; S: café→كافه |
| h01 | Quiero un caro rojo. | Quiero un carro rojo. | S: caro→carro |
| h11 | Te voy a textear later, okay? | Te voy a testear la TER, ¿ok? | I: testear; S: textear→la; S: later→ter; S: okay→ok |
| h12 | Voy a lunchear con mi friend. | Voi aluncear con mi friendo. | S: voy→voi; S: a→aluncear; D: lunchear; S: friend→friendo |
| h17 | Sí. | Si | S: sí→si |
| h20 | Quiero un café, por favor. | Quero Uncafé or favor. | S: quiero→quero; S: un→uncafé; S: café→or; D: por |
| h21 | Ayer yo va al mercado y compré una manzanas y la tomates porque mi madre es muy hambre. | Ayer yo ba al mercado y compré una manzana si la tomate porque mi madre es muy hambre. | S: va→ba; S: manzanas→manzana; S: y→si; S: tomates→tomate |
| h13 | Yo... eh... yo fui, no, yo voy al mercado. | Jo E. Yo fui, no, yo voy al mercado. | S: yo→jo; S: eh→e |
| ent02 | Mi profesora se llama Siobhán. | Mi profesora se llama Xiobán. | S: siobhán→xiobán |
| ent03 | I'm meeting Priya at Starbucks on June 14th. | I'm meeting Priya at Starbucks on June 14. | S: 14th→14 |
| ent04 | Ich wohne in München seit dem 21. April. | Ich wohne in München seit dem einundzwanzigsten April. | S: 21→einundzwanzigsten |
| ent09 | Dr. Nguyen booked a flight to São Paulo on November 3rd. | Dr. Ngwin booked a flight to Sao Paulo on November 3. | S: nguyen→ngwin; S: 3rd→3 |
