"""Build the STT test set: cases.jsonl + 16 kHz mono WAV per case (edge-tts neural voices).
Run from the repo root: .venv/bin/python test_cases/make_cases.py"""
import asyncio, json, os, subprocess, tempfile
import edge_tts
D = os.path.dirname(os.path.abspath(__file__))
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
 ("fm04","formatting",["en"],"My lesson is on March 5th, 2027.",EN2,"date"),
]
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
 ("pm15",["en","es"],"I want los tacos, pero no quiero la frijoles.","I want los tacos, pero no quiero los frijoles.","la frijoles","en-US-AvaMultilingualNeural"),
]
async def planted(c):
    cid, langs, text, good, bad, voice = c
    r = await one((cid, "planted_mistake", langs, text, voice, "planted learner mistake"))
    r.update(intended_correct=good, planted_mistake=bad); return r
# two-speaker clips: learner then tutor, different voices, 0.9 s gap
TWO = [
 ("sp01",["Can we practice ordering food?","Sure, let's start with the drinks."],[EN,EN2]),
 ("sp02",["How do I say good night in German?","You say gute Nacht."],[EN2,"en-GB-SoniaNeural"]),
 ("sp03",["I'm ready for the quiz.","Great, here is your first question."],["en-AU-NatashaNeural",EN2]),
 ("sp04",["That was really hard.","You did well, keep going."],[EN2,EN]),
]
def to_wav(src, dst, pre=500, post=250):
    subprocess.run(["ffmpeg","-y","-loglevel","error","-i",src,"-af",f"adelay={pre},apad=pad_dur={post/1000}",
                    "-ar","16000","-ac","1","-sample_fmt","s16",dst], check=True)
async def tts(text, voice, out):
    for a in range(4):
        try: await edge_tts.Communicate(text, voice).save(out); return
        except Exception: await asyncio.sleep(2)
    raise RuntimeError(f"tts failed {voice}")
async def one(c):
    cid, cat, langs, text, voice, notes = c
    wav = f"{D}/audio/{cid}.wav"
    if not os.path.exists(wav):
        mp3 = wav[:-4]+".mp3"; await tts(text, voice, mp3); to_wav(mp3, wav); os.remove(mp3)
    return {"id":cid,"category":cat,"languages":langs,"reference":text,"speakers":[{"speaker":"A","voice":voice,"text":text}],"audio":f"audio/{cid}.wav","notes":notes}
async def two(c):
    cid, texts, voices = c
    wav = f"{D}/audio/{cid}.wav"
    if not os.path.exists(wav):
        parts = []
        for i,(t,v) in enumerate(zip(texts,voices)):
            mp3 = f"{D}/audio/{cid}_{i}.mp3"; await tts(t, v, mp3)
            p = f"{D}/audio/{cid}_{i}.wav"; to_wav(mp3, p, 500 if i==0 else 900, 0 if i==0 else 250); os.remove(mp3); parts.append(p)
        lst = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False); lst.write("".join(f"file '{p}'\n" for p in parts)); lst.close()
        subprocess.run(["ffmpeg","-y","-loglevel","error","-f","concat","-safe","0","-i",lst.name,"-c","copy",wav], check=True)
        for p in parts: os.remove(p)
    return {"id":cid,"category":"two_speaker","languages":["en"],"reference":" ".join(texts),
            "speakers":[{"speaker":s,"voice":v,"text":t} for s,t,v in zip("AB",texts,voices)],"audio":f"audio/{cid}.wav","notes":"learner then tutor, 0.9 s gap"}
async def main():
    sem = asyncio.Semaphore(8)
    async def g(f, c):
        async with sem: return await f(c)
    rows = await asyncio.gather(*[g(one,c) for c in C], *[g(two,c) for c in TWO], *[g(planted,c) for c in PLANTED])
    with open(f"{D}/cases.jsonl","w") as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False)+"\n")
    print(len(rows), "cases")
asyncio.run(main())
