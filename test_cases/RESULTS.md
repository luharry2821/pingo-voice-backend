# STT results (live Pingo on Render)

109 cases. Error rates: lower is better, except language-ID accuracy (higher is better). n = cases / reference units (words, or characters for CER); language-ID metrics are per final turn.

| KPI | Value | n |
|---|---|---|
| WER (normalized, all word-based cases) | 6.35% | 88 / 598 |
| Semantic WER (LLM judge) | 2.51% | 88 |
| FWER (raw, no normalization) | 8.59% | 88 / 582 |
| MER (match error rate, jiwer.mer) | 6.34% | 88 / 599 |
| cpWER (2-speaker) | 17.78% | 4 / 45 |
| Mistake-preservation rate (higher is better) | 69.44% | 36 mistakes |
| Mistake-preservation, ambiguous audio excluded | 75.0% | 32 mistakes |
| Sentence error rate | 24.77% | 109 |
| Missed entity rate | 5.88% (2/34) | 18 cases |
| Missed entity rate, entity set only | 4.17% (1/24) | 10 cases |
| Language-ID accuracy (higher is better) | 80.34% | 94 / 117 turns |
| Confident-wrong rate (wrong label, confidence ≥ 0.6) | 13.68% | 16 / 117 turns |
| Wrong labels at confidence ≥ 0.5 | 17 | 17 / 117 turns |
| Language-ID accuracy en | 87.1% | 27 / 31 turns |
| Language-ID accuracy de | 75.0% | 9 / 12 turns |
| Language-ID accuracy es | 81.48% | 22 / 27 turns |
| Language-ID accuracy fr | 100.0% | 7 / 7 turns |
| Language-ID accuracy it | 100.0% | 3 / 3 turns |
| Language-ID accuracy hi | 100.0% | 1 / 1 turns |
| Language-ID accuracy ja | 77.78% | 7 / 9 turns |
| Language-ID accuracy zh | 57.14% | 4 / 7 turns |
| Language-ID accuracy ko | 40.0% | 2 / 5 turns |
| Language-ID accuracy en+hi | 71.43% | 5 / 7 turns |
| Language-ID accuracy en+es | 87.5% | 7 / 8 turns |
| WER en | 0.0% | 10 / 84 |
| WER de | 2.86% | 6 / 35 |
| WER es | 0.0% | 3 / 18 |
| WER fr | 0.0% | 2 / 14 |
| WER it | 16.67% | 1 / 6 |
| MER en | 0.0% | 10 / 84 |
| MER de | 2.86% | 6 / 35 |
| MER es | 0.0% | 3 / 18 |
| MER fr | 0.0% | 2 / 14 |
| MER it | 16.67% | 1 / 6 |
| CER ja | 0.0% | 6 / 59 |
| CER zh | 0.0% | 5 / 35 |
| CER ko | 0.0% | 4 / 32 |
| Code-switching WER en_hi | 42.86% | 5 / 28 |
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
| MER (jiwer.mer) | 10.76% | 26 / 158 |
| Semantic WER | 7.01% | 26 |
| FWER | 15.19% | 26 / 158 |
| CER | 5.71% | 5 / 35 |
| Sentence error rate | 48.39% | 31 |
| Mistake preservation | 52.38% | 21 |
| Mistake preservation, ambiguous excluded | 58.82% | 17 |

## Missed entities

2 of 34 entities missed after the same normalization as WER/CER.

| id | entity (as spoken) | Pingo heard |
|---|---|---|
| fm05 | veintiún euros | Son 21 euros. |
| ent02 | Siobhán | Mi profesora se llama Siobhan. |

## Mislabeled language ID

Final turns whose language_code did not match the case's expected language. Code-switching cases count either expected language as correct.

23 of 117 turns.

| id | text | expected | got | confidence |
|---|---|---|---|---|
| zh01 | 我想喝水。 | zh | ja | 1.0 |
| zh02 | 请问洗手间在哪里？ | zh | ja | 1.0 |
| zh03 | 我在学习中文。 | zh | ja | 1.0 |
| ko01 | 물 주세요. | ko | zh | 1.0 |
| ko02 | 화장실이 어디예요? | ko | zh | 1.0 |
| es01 | ¿Dónde está el baño? | es | ko | 1.0 |
| hi01 | मुझे एक कॉफ़ी चाहिए, प्लीज़। | en, hi | it | 0.812307 |
| cs01 | Quiero un café, please. | en, es | hi | 0.624842 |
| pm01 | Yo es hambre. | es | fr | 0.255212 |
| pm05 | Ich habe Hunger nicht. | de | es | 0.64365 |
| pm13 | 私は学生王です。 | ja | en | 0.870774 |
| pm18 | Ella son muy simpática. | es | fr | 0.530655 |
| pm20 | I go to school yesterday. | en | es | 0.880234 |
| pm21 | Tengo un perro en casa. | es | en | 0.302449 |
| h01 | Quiero un carro rojo. | es | hi | 0.622754 |
| h04 | Ich gehe in die Park. | de | nl | 0.288636 |
| h05 | 私は日本へ行きます。 | ja | nl | 0.284323 |
| h09 | 할머니, 밥 먹어? | ko | zh | 1.0 |
| h10 | मैं कल स्कूल जाएगा था। | en, hi | zh | 1.0 |
| h18 | Danke! | de | es | 0.988113 |
| h14 | I want to. | en | es | 0.442537 |
| h14 | Um. | en | es | 0.346639 |
| ent09 | Dr. Nguyen booked a flight to São Paulo on November 3rd. | en | ja | 1.0 |

## Planted mistakes

| id | said (with mistake) | intended | Pingo heard | caught? |
|---|---|---|---|---|
| pm01 | Yo es hambre. | Yo tengo hambre. | Yo es hambre. | yes |
| pm02 | La problema es muy difícil. | El problema es muy difícil. | La problema es muy difícil. | yes |
| pm03 | Ayer yo como pizza con mis amigos. | Ayer yo comí pizza con mis amigos. | Ayer yo como pizza con mis amigos. | yes |
| pm04 | Mi madre es muy cansada hoy. | Mi madre está muy cansada hoy. | Mi madre es muy cansada hoy. | yes |
| pm05 | Ich habe Hunger nicht. | Ich habe keinen Hunger. | Ich habe Hunger nicht. | yes |
| pm06 | Der Mädchen ist sehr nett. | Das Mädchen ist sehr nett. | Der Mädchen ist sehr nett. | yes |
| pm07 | Gestern ich bin ins Kino gegangen. | Gestern bin ich ins Kino gegangen. | Gestern ich bin ins Kino gegangen. | yes |
| pm08 | La garçon est très grand. | Le garçon est très grand. | La garçon est très grand. | yes |
| pm09 | J'ai allé au marché ce matin. | Je suis allé au marché ce matin. | J'ai allé au marché ce matin. | yes |
| pm10 | Io sono fame. | Io ho fame. | Io sono fame. | yes |
| pm11 | She don't like coffee. | She doesn't like coffee. | She don't like coffee. | yes |
| pm12 | Yesterday I go to the library. | Yesterday I went to the library. | Yesterday I go to the library. | yes |
| pm13 | 私は学生をです。 | 私は学生です。 | 私は学生王です。 | no |
| pm14 | मेरी brother बहुत smart है। | मेरा brother बहुत smart है। | मेरी ब्रदर बहुत स्मार्ट है। | yes |
| pm16 | Yo tango hambre. | Yo tengo hambre. | Yo tengo hambre. | no |
| pm17 | Je suis allée au cinéma. | Je suis allé au cinéma. | Je suis allé au cinéma. | no (ambiguous audio) |
| pm18 | Ella son muy simpática. | Ella es muy simpática. | Ella son muy simpática. | yes (ambiguous audio) |
| pm19 | Nosotros va al parque. | Nosotros vamos al parque. | Nosotros va al parque. | yes |
| pm20 | I goed to school yesterday. | I went to school yesterday. | I go to school yesterday. | no (ambiguous audio) |
| pm21 | Tengo un pero en casa. | Tengo un perro en casa. | Tengo un perro en casa. | no |
| pm22 | Feliz ano nuevo. | Feliz año nuevo. | Feliz año nuevo. | no |
| pm15 | I want los tacos, pero no quiero la frijoles. | I want los tacos, pero no quiero los frijoles. | Iguan los tacos, pero no quiero la frijoles. | yes |
| h01 | Quiero un caro rojo. | Quiero un carro rojo. | Quiero un carro rojo. | no |
| h02 | Vamos a la caza de mi abuela. | Vamos a la casa de mi abuela. | Vamos a la casa de mi abuela. | no |
| h03 | Je voudrais une café. | Je voudrais un café. | Je voudrais une café. | yes |
| h04 | Ich gehe in die Park. | Ich gehe in den Park. | Ich gehe in die Park. | yes |
| h05 | 私を日本へ行きます。 | 私は日本へ行きます。 | 私は日本へ行きます。 | no |
| h06 | 私は寿司が食べます。 | 私は寿司を食べます。 | 私は寿司が食べます。 | yes |
| h07 | 我有三个书。 | 我有三本书。 | 我有三个叔。 | no |
| h08 | 我买了一只车。 | 我买了一辆车。 | 我买了一只车。 | yes |
| h09 | 할머니, 밥 먹어? | 할머니, 진지 드셨어요? | 할머니, 밥 먹어? | yes |
| h10 | मैं कल school जाएगा था। | मैं कल school गया था। | मैं कल स्कूल जाएगा था। | yes |
| h21 | Ayer yo va al mercado y compré una manzanas y la tomates porque mi madre es muy hambre. | Ayer yo fui al mercado y compré unas manzanas y los tomates porque mi madre tiene mucha hambre. | Ayer yo va al mercado y compré una manzana si la tomates porque mi madre es muy hambre. | yes [yo va], no (ambiguous audio) [una manzanas], yes [la tomates], yes [es muy hambre] |

## Failed cases

| id | reference | Pingo heard | errors |
|---|---|---|---|
| de02 | Ich lerne seit drei Monaten Deutsch. | Ich lerne seit 3 Monaten Deutsch. | S: drei→3 |
| it01 | Dov'è la stazione dei treni? | Dove è la stazione dei treni? | S: dov→dove |
| hi01 | मुझे एक coffee चाहिए, please. | मुझे एक कॉफ़ी चाहिए, प्लीज़। | S: coffee→कॉफ़ी; S: please→प्लीज़ |
| hi02 | Aaj मेरी Spanish class है। | आज मेरी स्पैनिश क्लास है। | S: aaj→आज; S: spanish→स्पैनिश; S: class→क्लास |
| hi03 | मैं weekend पर movie देखने जा रहा हूँ। | मैं वीकेंड पर मूवी देखने जा रहा हूँ। | S: weekend→वीकेंड; S: movie→मूवी |
| hi04 | यह sentence बहुत difficult है। | यह सेंटेंस बहुत डिफिकल्ट है। | S: sentence→सेंटेंस; S: difficult→डिफिकल्ट |
| hi05 | Please मुझे next lesson दिखाओ। | प्लीज़ मुझे नेक्स्ट लेसन दिखाओ। | S: please→प्लीज़; S: next→नेक्स्ट; S: lesson→लेसन |
| cs02 | How do you say tengo hambre in English? | How do you say tango hambra in English? | S: tengo→tango; S: hambre→hambra |
| fm05 | Son veintiún euros. | Son 21 euros. | S: veintiún→21 |
| pm13 | 私は学生をです。 | 私は学生王です。 | S: を→王 |
| pm14 | मेरी brother बहुत smart है। | मेरी ब्रदर बहुत स्मार्ट है। | S: brother→ब्रदर; S: smart→स्मार्ट |
| pm16 | Yo tango hambre. | Yo tengo hambre. | S: tango→tengo |
| pm17 | Je suis allée au cinéma. | Je suis allé au cinéma. | S: allée→allé |
| pm20 | I goed to school yesterday. | I go to school yesterday. | S: goed→go |
| pm21 | Tengo un pero en casa. | Tengo un perro en casa. | S: pero→perro |
| pm22 | Feliz ano nuevo. | Feliz año nuevo. | S: ano→año |
| pm15 | I want los tacos, pero no quiero la frijoles. | Iguan los tacos, pero no quiero la frijoles. | S: i→iguan; D: want |
| ps01 | Quiero... um... un café. | Quiero. Ume. Un café. | S: um→ume |
| h01 | Quiero un caro rojo. | Quiero un carro rojo. | S: caro→carro |
| h02 | Vamos a la caza de mi abuela. | Vamos a la casa de mi abuela. | S: caza→casa |
| h05 | 私を日本へ行きます。 | 私は日本へ行きます。 | S: を→は |
| h07 | 我有三个书。 | 我有三个叔。 | S: 书→叔 |
| h10 | मैं कल school जाएगा था। | मैं कल स्कूल जाएगा था। | S: school→स्कूल |
| h11 | Te voy a textear later, okay? | Te voy a testear la ter, ¿ok? | I: testear; S: textear→la; S: later→ter; S: okay→ok |
| h21 | Ayer yo va al mercado y compré una manzanas y la tomates porque mi madre es muy hambre. | Ayer yo va al mercado y compré una manzana si la tomates porque mi madre es muy hambre. | S: manzanas→manzana; S: y→si |
| h13 | Yo... eh... yo fui, no, yo voy al mercado. | Yo. Yo fui, no, yo voy al mercado. | D: eh |
| ent02 | Mi profesora se llama Siobhán. | Mi profesora se llama Siobhan. | S: siobhán→siobhan |
