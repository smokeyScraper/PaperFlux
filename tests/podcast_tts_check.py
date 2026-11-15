"""Manual check of the real TTS setup: model id, both voices, MP3 encoding.

Spends ONE of the ~10 free-tier TTS requests for today and does not touch MongoDB.

    GEMINI_PODCAST_API_KEY=... python tests/podcast_tts_check.py [out.mp3]
"""
import sys

from paperflux.src.agents.gemini_client import GeminiClient
from paperflux.src.config.settings import GEMINI_TTS_MODELS, PODCAST_HOSTS, load_tts_keys
from paperflux.src.podcast.audio import duration_seconds, encode
from paperflux.src.podcast.script import tts_prompt


def main(out_path: str) -> None:
    keys = load_tts_keys()
    if not keys:
        sys.exit("Set GEMINI_PODCAST_API_KEY (or GEMINI_TTS_API_KEY) first.")
    a, b = PODCAST_HOSTS[0]["name"], PODCAST_HOSTS[1]["name"]
    script = {"turns": [
        {"speaker": a, "text": f"Hi, I'm {a}. This is a short check of the PaperFlux podcast voices."},
        {"speaker": b, "text": f"And I'm {b}. If you can hear us both, calm and clear, the setup works."},
    ]}
    client = GeminiClient(api_keys=keys, models=GEMINI_TTS_MODELS, timeout_seconds=180, max_attempts=1)
    pcm, rate = client.generate_speech(tts_prompt(script, PODCAST_HOSTS),
                                       [(h["name"], h["voice"]) for h in PODCAST_HOSTS])
    data, mime = encode(pcm, rate)
    with open(out_path, "wb") as f:
        f.write(data)
    print(f"OK: {duration_seconds(pcm, rate):.1f}s at {rate} Hz from {GEMINI_TTS_MODELS} -> {out_path} ({mime})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "paperflux-tts-check.mp3")
