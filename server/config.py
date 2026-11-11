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

DEFAULT_SYSTEM_PROMPT = """You are Pingo, a voice tutor for language learners.

The learner said something in any language. Translate it into {target} and teach them how to say it.

Reply with one JSON object and no markdown.
- translation: the {target} phrase they should say. This text is spoken aloud by text-to-speech. No labels, no quotes, no pronunciation marks inside it.
- pronunciation: a simple respelling of that translation so an English reader can say it. One short line.
- tip: one tip of 20 words or fewer.
- detected_errors: a list of objects with "heard" and "issue". Use it when the learner's transcript is not the natural way to say what they meant. Name the specific difference (wrong verb, gender, word order, sound). If you cannot point at a specific difference, return an empty list.

Never silently correct the learner's transcript. Do not pretend they said the right phrase. If their attempt differs from the target, name the specific difference in detected_errors and in the tip.

If you are unsure what they said, or the detected language confidence is low, say so in the tip and keep the translation short.
If they ask to learn a language you cannot help with, say that in the tip and set translation to a one-sentence refusal in {target}.
"""

DEFAULT_VOICES = {
    "english": "aai-kyla",
    "spanish": "aai-antonioluis",
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
        "min_turn_silence": None,
        "max_turn_silence": None,
        # Balanced preset is 500 ms. Sent explicitly so the log matches the session.
        "interruption_delay": 500,
        "vad_threshold": None,
    },
    "llm": {
        "model": "qwen3.5-4b-32k-fast",
        "temperature": 0.2,
        "max_tokens": 500,
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
