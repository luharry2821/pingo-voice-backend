"""Build the STT test set: cases.jsonl + one 16 kHz mono WAV per case (edge-tts neural voices).

Run from the repo root: .venv/bin/python test_cases/make_cases.py

First step of the eval pipeline: this script synthesises each test sentence
with a neural voice, converts it with ffmpeg to the exact format the browser
mic sends (16 kHz, mono, 16-bit PCM), and writes the ground truth to
cases.jsonl. stt_bench.py then streams the WAVs to each STT provider in real time
and score.py compares the transcripts with the
ground truth. Existing WAVs are reused, so re-running only fills gaps.

Case groups: C (plain sentences by language, code-switching, formatting),
ENTITY (names/places/dates, for missed entity rate), PLANTED (learner
mistakes STT must not silently fix), HARD (harder variants), PAUSES and
pause() (real silences and fillers).
Needs ffmpeg on PATH and the edge-tts package.
"""
import asyncio, json, os, subprocess, tempfile
import edge_tts
D = os.path.dirname(os.path.abspath(__file__))
# Default English voices (others are named per case to vary accents).
EN, EN2 = "en-US-JennyNeural", "en-US-GuyNeural"
# (id, category, languages, text, voice, notes)
C = [
 ("en01","english",["en"],"How do I say I'm hungry in Spanish?",EN,""),
 ("en02","english",["en"],"Can you teach me how to order a coffee in French?","en-GB-RyanNeural",""),
 ("en03","english",["en"],"What's the difference between ser and estar?",EN2,"Spanish terms inside English"),
 ("en04","english",["en"],"I keep forgetting the word for library.","en-AU-NatashaNeural",""),
 ("en05","english",["en"],"Please say that one more time, but slower.",EN,""),
 ("en06","english",["en"],"Let's practice greetings for a job interview.","en-IN-NeerjaNeural","Indian English accent"),
 ("en07","english",["en"],"Was my pronunciation of croissant okay?",EN2,""),
 ("en08","english",["en"],"I want to learn German before my trip to Berlin.","en-GB-SoniaNeural",""),
 ("en09","english",["en"],"Yes, I'm ready for the next lesson.",EN,""),
 ("en10","english",["en"],"Could you give me a harder sentence this time?","en-CA-LiamNeural",""),
 ("de01","german",["de"],"Wie spät ist es?","de-DE-ConradNeural",""),
 ("de02","german",["de"],"Ich lerne seit drei Monaten Deutsch.","de-DE-KatjaNeural",""),
 ("de03","german",["de"],"Können Sie das bitte langsamer sagen?","de-DE-AmalaNeural",""),
 ("de04","german",["de"],"Wo ist die nächste U-Bahn-Station?","de-DE-ConradNeural",""),
 ("de05","german",["de"],"Ich hätte gern ein Glas Wasser.","de-AT-IngridNeural","Austrian voice"),
 ("de06","german",["de"],"Das Wetter ist heute sehr schön.","de-CH-JanNeural","Swiss voice"),
 ("ja01","japanese",["ja"],"お水をください。","ja-JP-NanamiNeural",""),
 ("ja02","japanese",["ja"],"駅はどこですか。","ja-JP-KeitaNeural",""),
 ("ja03","japanese",["ja"],"日本語を勉強しています。","ja-JP-NanamiNeural",""),
 ("ja04","japanese",["ja"],"もう一度言ってください。","ja-JP-KeitaNeural",""),
 ("ja05","japanese",["ja"],"これはいくらですか。","ja-JP-NanamiNeural",""),
 ("zh01","chinese",["zh"],"我想喝水。","zh-CN-XiaoxiaoNeural",""),
 ("zh02","chinese",["zh"],"请问洗手间在哪里？","zh-CN-YunxiNeural",""),
 ("zh03","chinese",["zh"],"我在学习中文。","zh-CN-XiaoxiaoNeural",""),
 ("zh04","chinese",["zh"],"这个多少钱？","zh-CN-YunyangNeural",""),
 ("ko01","korean",["ko"],"물 주세요.","ko-KR-SunHiNeural",""),
 ("ko02","korean",["ko"],"화장실이 어디예요?","ko-KR-InJoonNeural",""),
 ("ko03","korean",["ko"],"한국어를 공부하고 있어요.","ko-KR-SunHiNeural",""),
 ("ko04","korean",["ko"],"다시 한 번 말해 주세요.","ko-KR-InJoonNeural",""),
 ("es01","other",["es"],"¿Dónde está el baño?","es-ES-AlvaroNeural",""),
 ("es02","other",["es"],"Quiero aprender a pedir comida en un restaurante.","es-MX-DaliaNeural",""),
 ("es03","other",["es"],"Mañana tengo una clase de inglés.","es-ES-ElviraNeural",""),
 ("fr01","other",["fr"],"Je voudrais un croissant, s'il vous plaît.","fr-FR-DeniseNeural",""),
 ("fr02","other",["fr"],"Comment dit-on merci en allemand ?","fr-FR-HenriNeural",""),
 ("it01","other",["it"],"Dov'è la stazione dei treni?","it-IT-DiegoNeural",""),
 ("hi01","codeswitch_en_hi",["en","hi"],"मुझे एक coffee चाहिए, please.","hi-IN-SwaraNeural","Hindi in Devanagari, English in Latin"),
 ("hi02","codeswitch_en_hi",["en","hi"],"Aaj मेरी Spanish class है।","hi-IN-MadhurNeural","Hindi in Devanagari, English in Latin; Aaj in Roman"),
 ("hi03","codeswitch_en_hi",["en","hi"],"मैं weekend पर movie देखने जा रहा हूँ।","hi-IN-MadhurNeural",""),
 ("hi04","codeswitch_en_hi",["en","hi"],"यह sentence बहुत difficult है।","hi-IN-SwaraNeural",""),
 ("hi05","codeswitch_en_hi",["en","hi"],"Please मुझे next lesson दिखाओ।","hi-IN-SwaraNeural",""),
 ("cs01","codeswitch_en_es",["en","es"],"Quiero un café, please.","en-US-AvaMultilingualNeural",""),
 ("cs02","codeswitch_en_es",["en","es"],"How do you say tengo hambre in English?","en-US-AndrewMultilingualNeural",""),
 ("cs03","codeswitch_en_es",["en","es"],"Mi hermano is coming to visit next week.","en-US-AvaMultilingualNeural",""),
 ("cs04","codeswitch_en_es",["en","es"],"I think the answer is muy bien.","en-US-AndrewMultilingualNeural",""),
 ("cs05","codeswitch_en_es",["en","es"],"Vamos a la playa this weekend.","en-US-AvaMultilingualNeural",""),
 ("fm01","formatting",["en"],"My class is at 7:30 on Monday.",EN,"time"),
 ("fm02","formatting",["en"],"The course costs $49.99 a month.",EN2,"currency"),
 ("fm03","formatting",["en"],"I've learned 250 words in 3 weeks.",EN,"numbers"),
 ("fm05","formatting",["es"],"Son veintiún euros.","es-ES-AlvaroNeural","Spanish number"),
 ("fm06","formatting",["es"],"Mi examen es el 15 de julio.","es-MX-DaliaNeural","Spanish date"),
 ("fm04","formatting",["en"],"My lesson is on March 5th, 2027.",EN2,"date"),
]
# entity-heavy lines (names, places, numbers, dates, brands) for missed-entity rate
ENTITY = [
 ("ent01","entity",["es"],"Vivo en Guadalajara desde el 3 de marzo.","es-MX-DaliaNeural","place + date"),
 ("ent02","entity",["es"],"Mi profesora se llama Siobhán.","es-ES-ElviraNeural","Irish name in Spanish"),
 ("ent03","entity",["en"],"I'm meeting Priya at Starbucks on June 14th.",EN,"name + brand + date"),
 ("ent04","entity",["de"],"Ich wohne in München seit dem 21. April.","de-DE-KatjaNeural","place + date"),
 ("ent05","entity",["fr"],"Je m'appelle François et j'habite à Lyon depuis 2019.","fr-FR-HenriNeural","name + place + year"),
 ("ent06","entity",["it"],"Il mio amico Giorgio lavora da Fiat a Torino.","it-IT-DiegoNeural","name + brand + place"),
 ("ent07","entity",["hi"],"मेरी सहेली का नाम अनुष्का है और वह दिल्ली में रहती है।","hi-IN-SwaraNeural","name + place"),
 ("ent08","entity",["ja"],"田中さんは東京に住んでいます。","ja-JP-NanamiNeural","name + place"),
 ("ent09","entity",["en"],"Dr. Nguyen booked a flight to São Paulo on November 3rd.",EN2,"name + place + date"),
 ("ent10","entity",["zh"],"王芳在北京的华为公司工作。","zh-CN-XiaoxiaoNeural","name + place + brand"),
]
# Entities per case id: the names, places, numbers and dates the transcript must
# contain. Used by score.py for missed entity rate.
ENTITIES = {
 "en08": ["Berlin"],
 "fm01": ["7:30", "Monday"],
 "fm02": ["$49.99"],
 "fm03": ["250", "3 weeks"],
 "fm04": ["March 5th, 2027"],
 "fm05": ["veintiún euros"],
 "fm06": ["15 de julio"],
 "h05": ["日本"],
 "ent01": ["Guadalajara", "3 de marzo"],
 "ent02": ["Siobhán"],
 "ent03": ["Priya", "Starbucks", "June 14th"],
 "ent04": ["München", "21. April"],
 "ent05": ["François", "Lyon", "2019"],
 "ent06": ["Giorgio", "Fiat", "Torino"],
 "ent07": ["अनुष्का", "दिल्ली"],
 "ent08": ["田中", "東京"],
 "ent09": ["Nguyen", "São Paulo", "November 3rd"],
 "ent10": ["王芳", "北京", "华为"],
}
# planted learner mistakes: (id, languages, spoken-with-mistake, intended_correct, planted_mistake, voice)
PLANTED = [
 ("pm01",["es"],"Yo es hambre.","Yo tengo hambre.","es","es-MX-JorgeNeural"),
 ("pm02",["es"],"La problema es muy difícil.","El problema es muy difícil.","La problema","es-ES-ElviraNeural"),
 ("pm03",["es"],"Ayer yo como pizza con mis amigos.","Ayer yo comí pizza con mis amigos.","como","es-ES-AlvaroNeural"),
 ("pm04",["es"],"Mi madre es muy cansada hoy.","Mi madre está muy cansada hoy.","es muy cansada","es-MX-DaliaNeural"),
 ("pm05",["de"],"Ich habe Hunger nicht.","Ich habe keinen Hunger.","Hunger nicht","de-DE-ConradNeural"),
 ("pm06",["de"],"Der Mädchen ist sehr nett.","Das Mädchen ist sehr nett.","Der Mädchen","de-DE-KatjaNeural"),
 ("pm07",["de"],"Gestern ich bin ins Kino gegangen.","Gestern bin ich ins Kino gegangen.","Gestern ich bin","de-DE-AmalaNeural"),
 ("pm08",["fr"],"La garçon est très grand.","Le garçon est très grand.","La garçon","fr-FR-HenriNeural"),
 ("pm09",["fr"],"J'ai allé au marché ce matin.","Je suis allé au marché ce matin.","J'ai allé","fr-FR-DeniseNeural"),
 ("pm10",["it"],"Io sono fame.","Io ho fame.","sono fame","it-IT-DiegoNeural"),
 ("pm11",["en"],"She don't like coffee.","She doesn't like coffee.","don't","en-US-JennyNeural"),
 ("pm12",["en"],"Yesterday I go to the library.","Yesterday I went to the library.","I go","en-IN-PrabhatNeural"),
 ("pm13",["ja"],"私は学生をです。","私は学生です。","学生をです","ja-JP-KeitaNeural"),
 ("pm14",["en","hi"],"मेरी brother बहुत smart है।","मेरा brother बहुत smart है।","मेरी","hi-IN-MadhurNeural"),
 ("pm16",["es"],"Yo tango hambre.","Yo tengo hambre.","tango","es-ES-AlvaroNeural"),
 ("pm17",["fr"],"Je suis allée au cinéma.","Je suis allé au cinéma.","allée","fr-FR-HenriNeural"),
 ("pm18",["es"],"Ella son muy simpática.","Ella es muy simpática.","Ella son","es-MX-DaliaNeural"),
 ("pm19",["es"],"Nosotros va al parque.","Nosotros vamos al parque.","Nosotros va","es-ES-AlvaroNeural"),
 ("pm20",["en"],"I goed to school yesterday.","I went to school yesterday.","goed","en-US-GuyNeural"),
 ("pm21",["es"],"Tengo un pero en casa.","Tengo un perro en casa.","pero","es-MX-JorgeNeural"),
 ("pm22",["es"],"Feliz ano nuevo.","Feliz año nuevo.","ano","es-ES-ElviraNeural"),
 ("pm15",["en","es"],"I want los tacos, pero no quiero la frijoles.","I want los tacos, pero no quiero los frijoles.","la frijoles","en-US-AvaMultilingualNeural"),
]
async def planted(c):
    """Build a planted-mistake case: the clip says the wrong form; also record the intended correct sentence.
    """
    cid, langs, text, good, bad, voice = c
    # Same as a plain case, plus the intended sentence and the mistake to look for.
    r = await one((cid, "planted_mistake", langs, text, voice, "planted learner mistake"))
    r.update(intended_correct=good, planted_mistake=bad); return r
# hard set, round 2: (id, category, languages, spoken, voice, intended_correct or None, planted mistake(s) or None, notes)
HARD = [
 ("h01","planted_mistake",["es"],"Quiero un caro rojo.","es-MX-JorgeNeural","Quiero un carro rojo.","caro","real-word swap"),
 ("h02","planted_mistake",["es"],"Vamos a la caza de mi abuela.","es-ES-AlvaroNeural","Vamos a la casa de mi abuela.","caza","real-word swap casa/caza (Castilian voice)"),
 ("h03","planted_mistake",["fr"],"Je voudrais une café.","fr-FR-DeniseNeural","Je voudrais un café.","une café","gender"),
 ("h04","planted_mistake",["de"],"Ich gehe in die Park.","de-DE-ConradNeural","Ich gehe in den Park.","die Park","article/case"),
 ("h05","planted_mistake",["ja"],"私を日本へ行きます。","ja-JP-KeitaNeural","私は日本へ行きます。","私を","particle swap"),
 ("h06","planted_mistake",["ja"],"私は寿司が食べます。","ja-JP-NanamiNeural","私は寿司を食べます。","寿司が","particle swap"),
 ("h07","planted_mistake",["zh"],"我有三个书。","zh-CN-YunxiNeural","我有三本书。","三个书","measure word"),
 ("h08","planted_mistake",["zh"],"我买了一只车。","zh-CN-XiaoxiaoNeural","我买了一辆车。","一只车","measure word"),
 ("h09","planted_mistake",["ko"],"할머니, 밥 먹어?","ko-KR-InJoonNeural","할머니, 진지 드셨어요?","밥 먹어","honorific: casual speech to a grandmother"),
 ("h10","planted_mistake",["en","hi"],"मैं कल school जाएगा था।","hi-IN-MadhurNeural","मैं कल school गया था।","जाएगा","Hinglish wrong verb form"),
 ("h11","codeswitch_en_es",["en","es"],"Te voy a textear later, okay?","en-US-AndrewMultilingualNeural",None,None,"mid-word switch (textear)"),
 ("h12","codeswitch_en_es",["en","es"],"Voy a lunchear con mi friend.","en-US-AvaMultilingualNeural",None,None,"mid-word switch (lunchear)"),
 ("h15","fast_speech",["en"],"Can you repeat the last sentence and then give me a new one?","en-US-GuyNeural|+40%",None,None,"+40% speaking rate"),
 ("h16","fast_speech",["es"],"No entiendo lo que dijiste, ¿puedes repetirlo más despacio?","es-MX-DaliaNeural|+40%",None,None,"+40% speaking rate"),
 ("h17","one_word",["es"],"Sí.","es-ES-ElviraNeural",None,None,"one-word turn"),
 ("h18","one_word",["de"],"Danke.","de-DE-KatjaNeural",None,None,"one-word turn"),
 ("h19","accented",["en"],"I am very hungry, I want to eat now.","es-MX-JorgeNeural",None,None,"Spanish-accented English (Spanish voice reading English)"),
 ("h20","accented",["es"],"Quiero un café, por favor.","hi-IN-MadhurNeural",None,None,"Hindi-accented Spanish"),
 ("h21","planted_mistake",["es"],"Ayer yo va al mercado y compré una manzanas y la tomates porque mi madre es muy hambre.","es-ES-AlvaroNeural",
  "Ayer yo fui al mercado y compré unas manzanas y los tomates porque mi madre tiene mucha hambre.",["yo va","una manzanas","la tomates","es muy hambre"],"long run-on, 4 mistakes"),
]
# multi-pause clips with real silence: (id, languages, segments, voice, reference)
PAUSES = [
 ("h13",["es"],["Yo","eh","yo fui, no, yo voy al mercado."],"es-MX-JorgeNeural","Yo... eh... yo fui, no, yo voy al mercado."),
 ("h14",["en"],["I want to","um","I mean, I need to practice the past tense."],"en-US-JennyNeural","I want to... um... I mean, I need to practice the past tense."),
]
async def hard(c):
    """Build a hard-set case (marked hard=True), with its planted mistake(s) if it has any."""
    cid, cat, langs, text, voice, good, bad, notes = c
    r = await one((cid, cat, langs, text, voice, notes)); r["hard"] = True
    if bad: r.update(intended_correct=good, planted_mistake=bad)
    return r
async def multipause(c):
    """Build a clip with real silences between segments (1.2 s gaps) by joining separately synthesised pieces.
    """
    cid, langs, segs, voice, ref = c
    wav = f"{D}/audio/{cid}.wav"
    if not os.path.exists(wav):
        parts = []
        for i, t in enumerate(segs):
            # Each piece gets its own lead-in silence: 500 ms before the first, 1.2 s
            # before the rest; 250 ms after the last.
            mp3 = f"{D}/audio/{cid}_{i}.mp3"; await tts(t, voice, mp3)
            q = f"{D}/audio/{cid}_{i}.wav"; to_wav(mp3, q, 500 if i == 0 else 1200, 250 if i == len(segs) - 1 else 0); os.remove(mp3); parts.append(q)
        # ffmpeg's concat demuxer reads a list file of the pieces to join.
        lst = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False); lst.write("".join(f"file '{q}'\n" for q in parts)); lst.close()
        subprocess.run(["ffmpeg","-y","-loglevel","error","-f","concat","-safe","0","-i",lst.name,"-c","copy",wav], check=True)
        for q in parts: os.remove(q)
    return {"id": cid, "category": "pause_filler", "languages": langs, "reference": ref, "hard": True,
            "speakers": [{"speaker": "A", "voice": voice, "text": ref}], "audio": f"audio/{cid}.wav", "notes": "1.2 s silences, filler + self-correction"}
def to_wav(src, dst, pre=500, post=250):
    # adelay adds silence before the speech, apad after; then resample to 16 kHz mono
    # s16.
    """ffmpeg: convert src to 16 kHz mono PCM16 WAV, with `pre` ms of leading and `post` ms of trailing silence.
    """
    subprocess.run(["ffmpeg","-y","-loglevel","error","-i",src,"-af",f"adelay={pre},apad=pad_dur={post/1000}",
                    "-ar","16000","-ac","1","-sample_fmt","s16",dst], check=True)
async def tts(text, voice, out):
    """Synthesise `text` with an edge-tts voice to an MP3, retrying up to 4 times.

    voice may carry a rate suffix, "voice|+40%", for the fast-speech cases.
    """
    voice, _, rate = voice.partition("|")  # "voice|+40%" = faster speech
    for a in range(4):
        try: await edge_tts.Communicate(text, voice, rate=rate or "+0%").save(out); return
        except Exception: await asyncio.sleep(2)
    raise RuntimeError(f"tts failed {voice}")
async def one(c):
    """Build a one-speaker case: synthesise the WAV if missing and return its cases.jsonl row."""
    cid, cat, langs, text, voice, notes = c
    wav = f"{D}/audio/{cid}.wav"
    # Skip synthesis if the WAV already exists (keeps runs reproducible and fast).
    if not os.path.exists(wav):
        mp3 = wav[:-4]+".mp3"; await tts(text, voice, mp3); to_wav(mp3, wav); os.remove(mp3)
    return {"id":cid,"category":cat,"languages":langs,"reference":text,"speakers":[{"speaker":"A","voice":voice,"text":text}],"audio":f"audio/{cid}.wav","notes":notes}
async def pause():
    """Quiero... um... un café with 1.5 s of real silence around the filler."""
    cid = "ps01"; wav = f"{D}/audio/{cid}.wav"; segs = ["Quiero", "um", "un café"]
    if not os.path.exists(wav):
        parts = []
        for i, t in enumerate(segs):
            mp3 = f"{D}/audio/{cid}_{i}.mp3"; await tts(t, "es-MX-JorgeNeural", mp3)
            q = f"{D}/audio/{cid}_{i}.wav"; to_wav(mp3, q, 500 if i == 0 else 1500, 250 if i == 2 else 0); os.remove(mp3); parts.append(q)
        lst = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False); lst.write("".join(f"file '{q}'\n" for q in parts)); lst.close()
        subprocess.run(["ffmpeg","-y","-loglevel","error","-f","concat","-safe","0","-i",lst.name,"-c","copy",wav], check=True)
        for q in parts: os.remove(q)
    return {"id": cid, "category": "pause_filler", "languages": ["es"], "reference": "Quiero... um... un café.",
            "speakers": [{"speaker": "A", "voice": "es-MX-JorgeNeural", "text": "Quiero... um... un café."}], "audio": f"audio/{cid}.wav",
            "notes": "1.5 s silence before and after the filler; checks the turn is not cut and the filler is kept"}
async def main():
    """Build every case (8 at a time), add hard/ambiguous flags and entities, write cases.jsonl."""
    sem = asyncio.Semaphore(8)
    async def g(f, c):
        """Run one case builder under the semaphore so at most 8 TTS/ffmpeg jobs run at once."""
        async with sem: return await f(c)
    rows = await asyncio.gather(*[g(one,c) for c in C], *[g(planted,c) for c in PLANTED], pause(), *[g(hard,c) for c in HARD], *[g(multipause,c) for c in PAUSES], *[g(one,c) for c in ENTITY])
    # Clips where the audio itself is ambiguous (homophones, or the voice really sounds
    # like another word). kept as metadata from the audio check.
    AMBIGUOUS = {"pm17": "allée/allé are homophones", "pm20": "TTS audio sounds like 'go' (Whisper + AssemblyAI pre-recorded)", "pm18": "Whisper heard 'Ellas son' (would be correct)"}
    for r in rows:
        # Mark the harder round-1 cases as hard too.
        if r["id"] in ("pm16","pm17","pm18","pm19","pm20","pm21","pm22","fm05","fm06","ps01"): r["hard"] = True
        if r["id"] in AMBIGUOUS: r["audio_ambiguous"] = AMBIGUOUS[r["id"]]
        if r["id"] == "h21": r["audio_ambiguous_mistakes"] = ["una manzanas"]  # Whisper heard "una manzana"
    for r in rows:
        r["entities"] = ENTITIES.get(r["id"], [])
    with open(f"{D}/cases.jsonl","w") as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False)+"\n")
    print(len(rows), "cases")
asyncio.run(main())
