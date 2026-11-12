# Audio check for planted-mistake clips (hard set, Nov 11, 2026)

Does the synthetic voice really say the planted mistake? Two second routes: local **faster-whisper small** (fully independent) and **AssemblyAI pre-recorded** (universal-3-pro, prompt "transcribe verbatim, keep learner mistakes"). Verdict uses Whisper first; 'ambiguous' = neither route hears the planted form, or it is a homophone.

| id | planted | Pingo (streaming) | Whisper small | AAI pre-recorded, verbatim prompt | Audio verdict |
|---|---|---|---|---|---|
| pm16 | tango | Yo tengo hambre. | Yo tango hambre. | Yo tengo hambre. | confirmed |
| pm17 | allée | Je suis allé au cinéma. | Je suis allé au cinéma. | Je suis allé au cinéma. | ambiguous: allée/allé are homophones |
| pm18 | Ella son | Ella son muy simpática. | Ellas son muy simpática. | Ellas son muy simpática. | ambiguous: both routes heard 'Ellas son' |
| pm19 | Nosotros va | Nosotros va al parque. | Nosotros va al parque. | Nosotros va al parque. | confirmed |
| pm20 | goed | I go to school yesterday. | I go to school yesterday. | I go to school yesterday. | ambiguous: TTS says 'go' |
| pm21 | pero | Tengo un perro en casa. | Tengo un pero en casa. | Tengo un pero en casa. | confirmed |
| pm22 | ano | Feliz año nuevo. | Feliz Anonuevo. | Feliz ano nuevo. | confirmed |
| pm13 | 学生をです | 私は学生王です。 | 私は学生をです。 | 私は学生王です。 | confirmed |
| h01 | caro | Quiero un carro rojo. | Quiero un caro rojo. | Quiero un carro rojo. | confirmed |
| h02 | caza | Vamos a la casa de mi abuela. | Vamos a la caza de mi abuela. | Vamos a la caza de mi abuela. | confirmed |
| h03 | une café | Je voudrais une café. | Je voudrais une café. | Je voudrais une café. | confirmed |
| h04 | die Park | Ich gehe in die Park. | Ich gehe in die Park. | Ich gehe in die Park. | confirmed |
| h05 | 私を | 私は日本へ行きます。 | 私を日本へ行きます | 私を日本へ行きます。 | confirmed |
| h06 | 寿司が | 私は寿司が食べます。 | 私は寿司が食べます。 | 私は寿司が食べます。 | confirmed |
| h07 | 三个书 | 我有三个叔。 | 我有三个书 | 我有三个书。 | confirmed |
| h08 | 一只车 | 我买了一只车。 | 我买了一只车。 | 我买了一只车。 | confirmed |
| h09 | 밥 먹어 | 할머니, 밥 먹어? | 할머니, 밥 먹어? | 할머니, 밥 먹어? | confirmed |
| h10 | जाएगा | मैं कल स्कूल जाएगा था। | मैं कल सकोल जाएगा ता | मैं कल स्कूल जाएगा था। | confirmed |
| h21 | yo va, una manzanas, la tomates, es muy hambre | Ayer yo va al mercado y compré una manzana si la tomates porque mi madre es muy hambre. | Ayer yo va al mercado y compré una manzana y la tomates porque mi madre es muy hambre. | Ayer yo va al mercado y compré una manzana, si la tomates, porque mi madre es muy hambre. | confirmed except 'una manzanas' (ambiguous: both routes heard 'una manzana') |

Ambiguous mistakes excluded: pm17, pm18, pm20, h21 'una manzanas'. Mistake preservation (all 99 cases): 69.44% (25/36) raw, 75.0% (24/32) excluding ambiguous audio.

Takeaway: where Whisper hears the planted word (tango, pero, ano, caro, caza), live Pingo STT writes the correct real word instead. With a verbatim prompt, pre-recorded AssemblyAI keeps pero, ano and caza but still corrects tango and caro.
