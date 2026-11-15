"""Raw TTS PCM -> a file small enough to store and stream.

Gemini TTS returns 16-bit mono PCM at 24 kHz (~2.9 MB per minute). MP3 at 64 kbps is
~0.5 MB per minute, which matters for MongoDB free-tier storage and for every listener's
download. ``lameenc`` ships wheels, so no ffmpeg or system packages are needed.
"""
from __future__ import annotations

import io
import logging
import wave

try:
    import lameenc
except ImportError:  # pragma: no cover - exercised only when the wheel is missing
    lameenc = None

logger = logging.getLogger("paperflux.podcast.audio")

MP3_KBPS = 64
LEAD_IN_SECONDS = 0.35


def duration_seconds(pcm: bytes, rate: int) -> float:
    return len(pcm) / 2 / max(1, rate)


def _with_lead_in(pcm: bytes, rate: int) -> bytes:
    pcm = pcm[: len(pcm) - (len(pcm) % 2)]  # whole 16-bit samples only
    silence = b"\x00\x00" * int(rate * LEAD_IN_SECONDS)
    return silence + pcm + silence


def to_wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buf.getvalue()


def encode(pcm: bytes, rate: int) -> tuple[bytes, str]:
    """(file bytes, mime type): MP3 when the encoder is available, WAV otherwise."""
    pcm = _with_lead_in(pcm, rate)
    if lameenc is None:
        logger.warning("lameenc is not installed; storing WAV (about 6x larger than MP3)")
        return to_wav(pcm, rate), "audio/wav"
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(MP3_KBPS)
    encoder.set_in_sample_rate(rate)
    encoder.set_channels(1)
    encoder.set_quality(2)
    return bytes(encoder.encode(pcm) + encoder.flush()), "audio/mpeg"
