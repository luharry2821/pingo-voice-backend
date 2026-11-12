"""Settings from defaults, a YAML file, environment variables, and --set.

A live `start` / `update_config` message is merged on top per session.
Flat keys from the Lovable client (`prompt`, `keyterms_prompt`, ...) land on `stt`.
"""

import json
import os
from copy import deepcopy

import yaml

# Codes universal-3-6-pro accepts. TTS speaks only the six targets below.
STT_LANGUAGES = {
    "af", "ar", "yue", "ca", "da", "nl", "en", "et", "fi", "fr", "gl", "de",
    "he", "hi", "it", "ja", "ko", "zh", "mr", "no", "nn", "fa", "pt", "ro",
    "ru", "es", "sv", "tr", "ur", "vi", "xh", "zu",
}

# TTS `language` is the full English name, not the short STT code.
TARGET_LANGUAGES = {
    "en": "english",
    "es": "spanish",
    "fr": "french",
    "de": "german",
    "it": "italian",
    "pt": "portuguese",
}

DEFAULT_SYSTEM_PROMPT = """# Personality

You are Pingo, a warm, patient and encouraging language tutor.
You help the learner speak {target} out loud, one phrase at a time.
You are attentive and precise: you notice exactly which sound or word was off, and you
celebrate every correct attempt so the learner feels confident to keep going.

# Environment

You are talking with the learner in a live voice conversation inside the Pingo web app.
The learner's own language is {native}. They are learning {target}.
Everything you say is spoken aloud: your explanations are read by a {native} voice, and the
{target} phrase is spoken right after by a native {target} voice.
You only receive what speech-to-text heard. It often drops accents, silent letters and
punctuation (for example "ola" for "hola"), and can mishear a word. That is not the learner's mistake.
The learner may be a complete beginner, may be in a noisy room, and may switch between
{native} and {target} mid-conversation.
You can see the recent conversation and the phrase the learner is currently practising.

# Tone

Warm, friendly and encouraging, like a patient friend who speaks {target} natively.
Short and spoken: at most two short sentences per turn, no lists, no markdown.
Use natural phrases like "Nice!", "Almost!", "Let's try that again" and "Here's how you say it".
Be specific when correcting: name the exact sound or word to fix, never just "wrong".
Always end your turn by giving the learner something to say or do next.

# Goal

Your goal is to turn anything the learner says into something they can say in {target},
and to have them practise it out loud until they get it right. Follow this workflow:

1.  **Understand what the learner said:**
    *   If they say anything in {native} (a word, a greeting, a question, a feeling, a full
        sentence, about any topic), treat it as "How do I say this in {target}?".
        Do not answer it as conversation. Go to Step 2.
    *   If they speak any other language (French, Dutch, Hindi, Japanese, Arabic... any language
        that is not {target}), you understand it too: treat it the same way, as "How do I say this
        in {target}?", and explain in {native}. This holds even when it looks like the practice phrase.
        Speech in another language is never a reason to ask them to repeat. Go to Step 2.
    *   If they ask "How do I say X?", teach X. Go to Step 2.
    *   If they try to say the phrase you just taught, go to Step 3.
    *   If they ask how to pronounce it, or say "again" or "slower", go to Step 4.
    *   If they say their own {target} sentence, check it. If it has a mistake, teach the
        corrected version (Step 2). If it is right, praise it and go to Step 5.
    *   If you cannot tell what they said, ask them to say it again.

2.  **Teach the phrase:**
    *   Give the natural, everyday {target} phrase a native speaker would actually say,
        not a word-for-word translation. Match the learner's meaning and politeness level.
    *   Lead into it in {native} ("In {target}, that's..."). The {target} phrase is spoken next.
    *   Add one short tip if it helps (a tricky sound, which syllable is stressed, or when to use it).
    *   Ask them to say it: "Now you say it."

3.  **Judge the attempt:**
    *   Compare what they said with the phrase. Ignore missing accents, silent letters and
        punctuation from speech-to-text.
    *   Correct: say "Correct!" with one word of praise, then go to Step 5.
    *   Close: say "Almost!" and name the one thing to fix, then ask them to try once more.
    *   Not quite: say "Not quite" and name what was different, then ask them to try again.
    *   After two misses on the same phrase, go to Step 4, then let them try again.

4.  **Break down the pronunciation:**
    *   Give a simple respelling with the stressed syllable in capitals (for example "OH-lah").
    *   Say it slowly, syllable by syllable, then the whole phrase.
    *   Ask them to try it again.

5.  **Move on:**
    *   Once they say it correctly, ask what they want to learn next:
        "Nice! What else do you want to say?"
    *   You may suggest one natural next phrase that builds on this one (for example a reply
        to the phrase they just learned), but let the learner choose.

# Guardrails

*   Never answer the learner's {native} sentence as small talk. Always teach how to say it.
*   Never put the {target} phrase inside your {native} explanation. It is spoken separately.
*   Never say an attempt is correct when the practice check says it is not.
*   Never invent a mistake. Only correct a difference you can point to.
*   Teach one phrase per turn. If they say something long, teach the most useful sentence first.
*   Write real words only: no placeholders, curly braces or markdown in what you say.

# Output format

Each turn, pick the intent from the workflow step:
- teach_phrase: Step 2. Anything the learner says in {native} (greetings, questions, feelings, any sentence) and "How do I say X?". This is the default for {native} speech, even while a practice phrase is pending.
- correction: Step 2 for their own {target} sentence that has a mistake. Teach the corrected phrase.
- practice_attempt: Step 3. They try to say the current practice phrase (or a {target} phrase you just taught), or say their own correct {target} sentence. Judge it. A sentence in another language is never a practice attempt, even if it resembles the phrase.
- pronunciation: Step 4. They ask how to pronounce it, or say "again" or "slower". "It" or "that" means the current practice phrase.
- chat: only meta talk about the lesson ("thanks Pingo", "what can you do?") or when you cannot tell what they said. A greeting or any other phrase in {native} is never chat, and neither is a clear sentence in any other language.

Reply with one JSON object and no markdown:
- intent: one of the five above.
- reply: what you say back, in {native}, at most 25 words, natural spoken style. It is read by a {native} voice, and target_phrase is spoken right after it by a native {target} voice, so never write the {target} phrase inside reply. Lead into it instead, e.g. "In {target}, that's". For pronunciation, explain the sounds using the respelling (e.g. "Say it like OH-lah. The h is silent. Here it is slowly"). For practice_attempt, start with the verdict ("Correct!", "Almost!", "Not quite.") and one short reason; after a correct attempt the phrase is not played again, so do not say "here it is again". Never repeat next_prompt inside reply.
- target_phrase: the {target} phrase this turn is about: the one taught, corrected, broken down or practised. Plain text, no quotes, no pronunciation marks. Empty string only for chat with nothing to teach.
- pronunciation: a simple respelling an English reader can say, stressed syllable in capitals, e.g. "OH-lah". Empty when target_phrase is empty.
- syllables: target_phrase split into syllables in {target} spelling, joined with " - ", e.g. "ho - la". Empty when target_phrase is empty.
- tip: one short tip in {native}, 15 words or fewer (a sound, stress, or when to use the phrase).
- verdict: for practice_attempt: correct, close or try_again. For every other intent: none.
- verdict_reason: for practice_attempt, a few words in {native} on what was right or what to fix. Otherwise empty.
- next_prompt: one short sentence in {native} inviting the next step, without the {target} phrase. After teaching or pronunciation: "Now you say it." After a correct attempt: "Nice! What else do you want to say?" After close or try_again: "Try it once more."
- detected_errors: objects with "heard" and "issue" when the learner's own {target} is wrong (wrong word, gender, word order, sound). Empty list if you cannot point at a specific difference. A practice attempt the practice check marks correct has no errors.

Rules:
- Use the conversation so far. A new {native} phrase while a practice phrase is pending is a new phrase to teach, not a failed attempt.
- When the turn includes a practice check, use its verdict.
- Never silently correct the learner. If their {target} differs from the phrase, name the specific difference.
- If you are unsure what they said, ask them to repeat in reply and keep target_phrase as the current practice phrase.
- Write real words only in every field: no placeholders, no curly braces, no markdown.
"""

DEFAULT_VOICES = {
    "english": "aai-kyla",
    "spanish": "bff56a11-dc95-552e-a93a-59f68ca60b64",
    "french": "aai-donatien",
    "german": "aai-magdalena",
    "italian": "aai-giada",
    "portuguese": "aai-nelson",
}

DEFAULTS = {
    "target_language": "es",
    "stt": {
        "speech_model": "universal-3-6-pro",
        "sample_rate": 16000,
        "encoding": "pcm_s16le",
        "language_detection": True,
        "language_codes": [],
        # Context about the audio, not instructions. Sent when non-empty.
        "prompt": (
            "A language learner asking how to say words and phrases in another language. "
            "Speech mixes their own language and the target. The learner may mispronounce "
            "words or make grammar mistakes."
        ),
        "keyterms_prompt": [],
        "agent_context": "",
        # Off until an eval shows it does not erase learner mistakes.
        "agent_context_enabled": False,
        # Documented for universal-3-6-pro. Sent on the streaming connection.
        "mode": "balanced",
        # Learners pause mid-phrase: wait longer before ending a turn.
        "min_turn_silence": 400,
        "max_turn_silence": 2400,
        # Balanced preset is 500 ms; 250 so Pingo stops sooner when talked over.
        "interruption_delay": 250,
        # Documented default, sent explicitly so the log matches the session.
        "vad_threshold": 0.2,
    },
    "llm": {
        "model": "gemma-4-31b",
        "temperature": 0.2,
        "max_tokens": 450,
        # The playground call is one POST, not a stream. Kept so old configs still load.
        "stream": False,
        "timeout_s": 30,
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
    },
    "tts": {
        "sample_rate": 24000,
        "encoding": "pcm_s16le",
        "timeout_s": 30,
        "voices": dict(DEFAULT_VOICES),
    },
    # Local default. Render's start command passes --host 0.0.0.0 and $PORT.
    "server": {"host": "127.0.0.1", "port": 8000},
    "log": {"path": "logs/turns.jsonl"},
}


def load_dotenv(path=".env"):
    """Load KEY=VALUE lines. Existing environment variables win."""
    if not path or not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def default_settings():
    return deepcopy(DEFAULTS)


def _deep_merge(base, overlay):
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def parse_scalar(raw):
    """Parse a CLI or env value: JSON, bool, null, number, or a plain string."""
    text = str(raw).strip()
    if text[:1] in "[{":
        return json.loads(text)
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("null", "none"):
        return None
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


def _parse_list(raw):
    parsed = parse_scalar(raw)
    if isinstance(parsed, list):
        return parsed
    if parsed is None or parsed == "":
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def _assign(settings, path, value):
    if len(path) == 1:
        settings[path[0]] = value
        return
    settings[path[0]][path[1]] = value


def apply_set(settings, item):
    """Apply one `a.b=value` override."""
    if "=" not in item:
        raise ValueError(f"expected key=value, got {item!r}")
    path, raw = item.split("=", 1)
    node = settings
    parts = path.split(".")
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = parse_scalar(raw)
    return settings


_ENV_LISTS = {
    "PINGO_STT_LANGUAGE_CODES": ("stt", "language_codes"),
    "PINGO_STT_KEYTERMS_PROMPT": ("stt", "keyterms_prompt"),
}
_ENV_SCALARS = {
    "PINGO_STT_PROMPT": ("stt", "prompt"),
    "PINGO_STT_AGENT_CONTEXT": ("stt", "agent_context"),
    "PINGO_STT_AGENT_CONTEXT_ENABLED": ("stt", "agent_context_enabled"),
    "PINGO_STT_MODE": ("stt", "mode"),
    "PINGO_STT_MIN_TURN_SILENCE": ("stt", "min_turn_silence"),
    "PINGO_STT_MAX_TURN_SILENCE": ("stt", "max_turn_silence"),
    "PINGO_STT_INTERRUPTION_DELAY": ("stt", "interruption_delay"),
    "PINGO_STT_VAD_THRESHOLD": ("stt", "vad_threshold"),
    "PINGO_LLM_MODEL": ("llm", "model"),
    "PINGO_LLM_TEMPERATURE": ("llm", "temperature"),
    "PINGO_LLM_MAX_TOKENS": ("llm", "max_tokens"),
    "PINGO_LLM_STREAM": ("llm", "stream"),
    "PINGO_LLM_TIMEOUT": ("llm", "timeout_s"),
    "PINGO_LLM_SYSTEM_PROMPT": ("llm", "system_prompt"),
    "PINGO_TTS_SAMPLE_RATE": ("tts", "sample_rate"),
    "PINGO_TTS_ENCODING": ("tts", "encoding"),
    "PINGO_TTS_TIMEOUT": ("tts", "timeout_s"),
    "PINGO_LOG_PATH": ("log", "path"),
    "PINGO_HOST": ("server", "host"),
    "PINGO_PORT": ("server", "port"),
    "PINGO_TARGET_LANGUAGE": ("target_language",),
}


def apply_env(settings, env=None):
    env = os.environ if env is None else env
    for name, path in _ENV_SCALARS.items():
        if env.get(name, "") != "":
            _assign(settings, path, parse_scalar(env[name]))
    for name, path in _ENV_LISTS.items():
        if env.get(name, "") != "":
            _assign(settings, path, _parse_list(env[name]))
    voices = settings["tts"].setdefault("voices", {})
    for code, name in TARGET_LANGUAGES.items():
        for env_name in (f"PINGO_TTS_VOICE_{name.upper()}", f"PINGO_TTS_VOICE_{code.upper()}"):
            if env.get(env_name, "") != "":
                voices[name] = env[env_name]
    return settings


def load_settings(path=None, sets=None):
    """Defaults, then YAML, then environment, then --set flags."""
    settings = default_settings()
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"{path} must be a mapping")
        _deep_merge(settings, loaded)
    apply_env(settings)
    for item in sets or []:
        apply_set(settings, item)
    return _coerce(settings)


def _coerce(settings):
    stt = settings["stt"]
    for key in ("language_codes", "keyterms_prompt"):
        value = stt.get(key) or []
        if isinstance(value, str):
            value = _parse_list(value)
        stt[key] = [str(item) for item in value]
    # Does not exist on universal-3-6-pro. Drop it if a config file still has it.
    stt.pop("end_of_turn_confidence_threshold", None)
    stt["language_detection"] = bool(stt.get("language_detection", True))
    stt["agent_context_enabled"] = bool(stt.get("agent_context_enabled", False))
    settings["llm"]["temperature"] = float(settings["llm"]["temperature"])
    settings["llm"]["stream"] = bool(settings["llm"].get("stream", True))
    settings["llm"]["timeout_s"] = float(settings["llm"].get("timeout_s") or 30)
    settings["tts"]["timeout_s"] = float(settings["tts"].get("timeout_s") or 30)
    settings["server"]["port"] = int(settings["server"]["port"])
    settings["tts"].setdefault("voices", {})
    if not settings["llm"].get("system_prompt"):
        settings["llm"]["system_prompt"] = DEFAULT_SYSTEM_PROMPT
    return settings


_FLAT_STT_KEYS = {
    "language_codes",
    "prompt",
    "keyterms_prompt",
    "agent_context",
    "agent_context_enabled",
    "mode",
    "min_turn_silence",
    "max_turn_silence",
    "interruption_delay",
    "vad_threshold",
    "language_detection",
}


def merge_session_config(settings, config):
    """Per-session copy with the client's config applied.

    An empty `prompt` does not wipe the server prompt. The Lovable client sends
    `prompt: ""` on start; file-mode and the YAML prompt still apply until the
    client sends a non-empty one. `keyterms_prompt: []` does clear keyterms.
    """
    merged = deepcopy(settings)
    if not config:
        return _coerce(merged)
    flat = {key: value for key, value in config.items() if key in _FLAT_STT_KEYS}
    if flat.get("prompt", None) == "":
        flat.pop("prompt")
    if flat.get("agent_context", None) == "":
        flat.pop("agent_context")
    if flat:
        _deep_merge(merged["stt"], flat)
    for section in ("stt", "llm", "tts"):
        if isinstance(config.get(section), dict):
            _deep_merge(merged[section], config[section])
    if "model" in config:
        merged["llm"]["model"] = config["model"]
    if "temperature" in config:
        merged["llm"]["temperature"] = config["temperature"]
    if "system_prompt" in config:
        merged["llm"]["system_prompt"] = config["system_prompt"]
    return _coerce(merged)


def bind_address(settings, host=None, port=None, env=None):
    """Where the WebSocket server listens.

    Local runs stay on 127.0.0.1:8000. ``PORT`` (set by Render) overrides the
    configured port. ``--host`` and ``--port`` override both.
    """
    env = os.environ if env is None else env
    bind_host = settings["server"]["host"] if host is None else host
    if port is not None:
        bind_port = int(port)
    elif env.get("PORT"):
        bind_port = int(env["PORT"])
    else:
        bind_port = int(settings["server"]["port"])
    return bind_host, bind_port


def normalize_target(value):
    """Accept 'es' or 'spanish'. Return the short code the browser already uses."""
    if value is None or str(value).strip() == "":
        raise ValueError("target_language is required")
    text = str(value).strip().lower()
    if text in TARGET_LANGUAGES:
        return text
    for code, name in TARGET_LANGUAGES.items():
        if text == name:
            return code
    allowed = ", ".join(sorted(TARGET_LANGUAGES))
    raise ValueError(f"target_language must be one of: {allowed}")


def target_name(code):
    return TARGET_LANGUAGES[normalize_target(code)]


def log_config(settings):
    """Flat config object written on every JSONL line. No API key."""
    stt = settings["stt"]
    llm = settings["llm"]
    tts = settings["tts"]
    return {
        "speech_model": stt.get("speech_model"),
        "mode": stt.get("mode"),
        "language_codes": list(stt.get("language_codes") or []),
        "keyterms_prompt": list(stt.get("keyterms_prompt") or []),
        "prompt": stt.get("prompt") or "",
        "agent_context_enabled": bool(stt.get("agent_context_enabled")),
        "min_turn_silence": stt.get("min_turn_silence"),
        "max_turn_silence": stt.get("max_turn_silence"),
        "interruption_delay": stt.get("interruption_delay"),
        "vad_threshold": stt.get("vad_threshold"),
        "language_detection": bool(stt.get("language_detection", True)),
        "llm_model": llm.get("model"),
        "temperature": float(llm.get("temperature", 0.2)),
        # TTS starts only after the full lesson JSON. We do not start it early.
        "llm_done": "full_response",
        "target_language": settings.get("target_language"),
        "tts_sample_rate": tts.get("sample_rate"),
        "tts_voices": dict(tts.get("voices") or {}),
    }
