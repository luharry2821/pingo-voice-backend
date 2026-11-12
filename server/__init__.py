"""Pingo backend package. The Lovable app connects to this process over one WebSocket.

Modules, in pipeline order (mic audio -> STT -> LLM tutor -> TTS -> browser):
  app.py       WebSocket server (/ws) and /health
  __main__.py  command line: server mode or file mode (one WAV)
  session.py   one learner session; orchestrates everything below
  audio.py     WAV -> 16 kHz PCM16 for file mode
  stt.py       AssemblyAI streaming speech-to-text
  practice.py  deterministic scoring of practice attempts
  llm.py       the tutor model on the AssemblyAI LLM Gateway
  tts.py       AssemblyAI text-to-speech, one session per voice
  config.py    settings: defaults, YAML, env vars, --set
  log.py       one JSONL line per final turn (eval input)
"""

# Package version (not tied to a release process).
__version__ = "0.1.0"
