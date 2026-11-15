"""Pipeline stage: paper -> podcast script -> one multi-speaker TTS call -> MP3 in GridFS.

Built around the free-tier TTS budget (~10 requests per day):
* the script is stored before recording, so a quota stop never pays for it twice;
* each TTS call is claimed from a per-day counter in MongoDB before it is made;
* a daily-quota error stops the stage (the rest stay pending for the next run);
* a paper that fails PODCAST_MAX_ATTEMPTS times is given up on, so it cannot drain the budget.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Sequence

from paperflux.src.agents.gemini_client import GeminiClient
from paperflux.src.agents.llm_errors import QuotaExhaustedError
from paperflux.src.config.settings import (
    GEMINI_TTS_MODELS,
    PODCAST_HOSTS,
    PODCAST_MAX_ATTEMPTS,
    PODCASTS_PER_DAY,
    load_tts_keys,
)
from paperflux.src.podcast.audio import duration_seconds, encode
from paperflux.src.podcast.script import WORDS_PER_MINUTE, tts_prompt

logger = logging.getLogger("paperflux.podcast")

READY, SCRIPTED, FAILED, GAVE_UP = "ready", "scripted", "failed", "gave_up"
STOP = "stop"  # out of budget or quota: leave the rest for the next run
MIN_AUDIO_SHARE = 0.4  # audio far shorter than the script means the reply was cut off


def needs_podcast(doc: Optional[dict[str, Any]]) -> bool:
    status = ((doc or {}).get("podcast") or {}).get("status")
    return bool(doc and doc.get("explanation")) and status not in {READY, GAVE_UP}


class PodcastProducer:
    def __init__(
        self,
        db: Any,
        writer: Optional[Any] = None,
        tts: Optional[Any] = None,
        hosts: Sequence[dict[str, str]] = PODCAST_HOSTS,
        per_day: int = PODCASTS_PER_DAY,
        max_attempts: int = PODCAST_MAX_ATTEMPTS,
    ):
        self.db = db
        self._writer = writer
        self._tts = tts
        self.hosts = hosts
        self.per_day = per_day
        self.max_attempts = max_attempts

    @property
    def writer(self) -> Any:
        if self._writer is None:
            from paperflux.src.agents.paper_podcast.agent import PodcastWriterAgent

            self._writer = PodcastWriterAgent()
        return self._writer

    @property
    def tts(self) -> Any:
        if self._tts is None:
            # A long episode takes a while to synthesise; retries are expensive at 10/day.
            self._tts = GeminiClient(api_keys=load_tts_keys(), models=GEMINI_TTS_MODELS,
                                     timeout_seconds=600, max_attempts=2)
        return self._tts

    def run(self, paper_ids: Sequence[str], on_progress: Optional[Callable[[int, int], None]] = None
            ) -> dict[str, int]:
        """Produce episodes for ``paper_ids`` in priority order (most upvoted first)."""
        docs = [self.db.collection.find_one({"paper_id": pid}) for pid in paper_ids]
        todo = [d for d in docs if needs_podcast(d)]
        counts = {READY: 0, FAILED: 0, "deferred": 0}
        for index, doc in enumerate(todo):
            if on_progress:
                on_progress(index, len(todo))
            outcome = self.produce(doc)
            if outcome == STOP:
                counts["deferred"] = len(todo) - index
                break
            counts[outcome] += 1
        if on_progress:
            on_progress(len(todo) - counts["deferred"], len(todo))
        logger.info("Podcasts: %s ready, %s failed, %s deferred", counts[READY], counts[FAILED], counts["deferred"])
        return counts

    def produce(self, doc: dict[str, Any]) -> str:
        pid = doc["paper_id"]
        pod = dict(doc.get("podcast") or {})
        if self.db.tts_requests_today() >= self.per_day:  # don't write scripts nobody can record today
            logger.info("Today's podcast budget (%s TTS requests) is used up", self.per_day)
            return STOP
        if not pod.get("turns"):
            try:
                script = self.writer.run(doc)
            except QuotaExhaustedError:
                logger.warning("Podcast writer quota is spent; leaving the rest for the next run")
                return STOP
            except Exception as exc:
                return self._fail(pid, pod, f"script: {exc}")
            pod.update(script, status=SCRIPTED, hosts=[dict(h) for h in self.hosts], updated_at=_now())
            self.db.set_podcast(pid, pod)

        if not self.db.reserve_tts_request(self.per_day):
            logger.info("Today's podcast budget (%s TTS requests) is used up", self.per_day)
            return STOP
        speakers = [(h["name"], h["voice"]) for h in self.hosts]
        try:
            pcm, rate = self.tts.generate_speech(tts_prompt(pod, self.hosts), speakers)
        except QuotaExhaustedError:
            logger.warning("TTS quota is spent; the remaining episodes wait for the next run")
            return STOP
        except Exception as exc:
            return self._fail(pid, pod, f"audio: {exc}")

        seconds = duration_seconds(pcm, rate)
        expected = pod.get("words", 0) / WORDS_PER_MINUTE * 60
        if expected and seconds < expected * MIN_AUDIO_SHARE:
            return self._fail(pid, pod, f"audio: only {seconds:.0f}s for a ~{expected:.0f}s script (cut off)")
        audio, mime = encode(pcm, rate)
        pod.update(status=READY, audio_id=self.db.save_podcast_audio(pid, audio, mime), mime=mime,
                   duration_s=round(seconds, 1), bytes=len(audio), error="", updated_at=_now())
        self.db.set_podcast(pid, pod)
        logger.info("Podcast ready for %s (%.0fs, %s KB)", pid, seconds, len(audio) // 1024)
        return READY

    def _fail(self, pid: str, pod: dict[str, Any], error: str) -> str:
        attempts = int(pod.get("attempts", 0)) + 1
        status = GAVE_UP if attempts >= self.max_attempts else FAILED
        logger.warning("Podcast for %s failed (attempt %s, %s): %s", pid, attempts, status, error[:200])
        pod.update(attempts=attempts, error=error[:300], status=status, updated_at=_now())
        self.db.set_podcast(pid, pod)
        return FAILED


def _now() -> datetime:
    return datetime.now(timezone.utc)
