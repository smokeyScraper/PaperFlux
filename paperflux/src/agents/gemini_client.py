"""Gemini REST client built for free-tier economics.

Free-tier limits are per *project* and per *model* (RPM, TPM and RPD all count),
so this client treats every (key, model) pair as its own bucket:

* a client-side sliding-window limiter keeps us under each bucket's RPM,
* a 429 is classified: *per-minute* -> wait the server's ``retryDelay`` and retry,
  *per-day* -> mark that bucket spent until the next Pacific midnight and move on,
* models are tried in order (a fallback chain), dead/unknown models are skipped,
* ``QuotaExhaustedError`` means "nothing left today", so callers stop spending.

API keys are sent in a header (never in the URL) so they cannot leak via logs.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import httpx

from paperflux.src.agents.llm_errors import LLMError, QuotaExhaustedError
from paperflux.src.config.settings import GEMINI_API_BASE, GEMINI_API_HOST, rpm_for

logger = logging.getLogger("paperflux.gemini_client")

INLINE_PDF_MAX_BYTES = 45 * 1024 * 1024
MAX_RATE_WAIT_SECONDS = 90.0

__all__ = ["GeminiClient", "GeminiError", "QuotaExhaustedError", "parse_rate_limit"]


class GeminiError(LLMError):
    """A failed Gemini request (HTTP error, empty/blocked/truncated output)."""


# --------------------------------------------------------------------------
# Process-wide quota state (several agents may share a key and a model)
# --------------------------------------------------------------------------
_state_lock = threading.Lock()
_exhausted: dict[tuple[str, str], float] = {}
_dead_models: set[str] = set()
_limiters: dict[tuple[str, str], "_RateLimiter"] = {}


def reset_quota_state() -> None:
    """Forget exhausted buckets / dead models / limiters (used by tests)."""
    with _state_lock:
        _exhausted.clear()
        _dead_models.clear()
        _limiters.clear()


def next_pacific_midnight(now: float) -> float:
    """Free-tier RPD quotas reset at midnight Pacific time."""
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/Los_Angeles")
    except Exception:  # tzdata missing -> PST is the safe approximation
        tz = timezone(timedelta(hours=-8))
    local = datetime.fromtimestamp(now, tz)
    nxt = (local + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
    return nxt.timestamp()


def _key_id(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:8]


class _RateLimiter:
    """Sliding-window limiter: at most ``rpm`` acquisitions per 60 seconds."""

    def __init__(self, rpm: int, clock: Callable[[], float], sleep: Callable[[float], None]):
        self.rpm = max(1, int(rpm))
        self._clock = clock
        self._sleep = sleep
        self._stamps: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                while self._stamps and now - self._stamps[0] >= 60:
                    self._stamps.popleft()
                if len(self._stamps) < self.rpm:
                    self._stamps.append(now)
                    return
                wait = 60 - (now - self._stamps[0]) + 0.05
            self._sleep(wait)


@dataclass(frozen=True)
class RateLimitInfo:
    daily: bool
    retry_after: Optional[float]


def parse_rate_limit(body: str) -> RateLimitInfo:
    """Classify a 429 body: per-day exhaustion vs a per-minute blip."""
    daily = bool(re.search(r"PerDay|per day|daily|limit:\s*0\b", body, re.IGNORECASE))
    match = re.search(r'"retryDelay"\s*:\s*"([\d.]+)s"', body) or re.search(
        r"retry in ([\d.]+)\s*s", body, re.IGNORECASE
    )
    return RateLimitInfo(daily=daily, retry_after=float(match.group(1)) if match else None)


def supports_schema(model: str) -> bool:
    return not model.lower().startswith("gemma")


def supports_pdf(model: str) -> bool:
    return not model.lower().startswith("gemma")


# --------------------------------------------------------------------------
class GeminiClient:
    """Per-agent client. Keys stay on the request, never on a process-global SDK."""

    def __init__(
        self,
        api_keys: list[str],
        model: Optional[str] = None,
        models: Optional[Sequence[str]] = None,
        timeout_seconds: float = 300.0,
        max_attempts: int = 3,
        transport: Optional[httpx.BaseTransport] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ):
        if not api_keys:
            raise ValueError("GeminiClient requires at least one API key")
        chain = list(models) if models else ([model] if model else [])
        if not chain:
            raise ValueError("GeminiClient requires at least one model")
        self.api_keys = api_keys
        self.models = chain
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self._transport = transport
        self._sleep = sleep
        self._clock = clock
        self._key_index = 0
        self._key_lock = threading.Lock()

    @property
    def model(self) -> str:
        return self.models[0]

    def current_key_count(self) -> int:
        return len(self.api_keys)

    # ---- public API --------------------------------------------------------
    def generate_text(
        self,
        user_text: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.2,
        response_schema: Optional[dict[str, Any]] = None,
        max_output_tokens: int = 16384,
    ) -> str:
        return self._generate(
            lambda key: ([{"text": user_text}], None),
            needs_pdf=False,
            system_instruction=system_instruction,
            temperature=temperature,
            response_schema=response_schema,
            max_output_tokens=max_output_tokens,
        )

    def generate_speech(self, prompt: str, speakers: Sequence[tuple[str, str]]) -> tuple[bytes, int]:
        """Multi-speaker TTS. ``speakers`` are (name in the transcript, prebuilt voice).
        Returns (16-bit mono PCM, sample rate)."""
        return self._run(
            lambda key: ([{"text": prompt}], None),
            needs_pdf=False,
            schema_capable=False,
            invoke=lambda key, model, parts, _: self._call_speech(key, model, parts, speakers),
        )

    def generate_from_pdf(
        self,
        pdf_path: str,
        user_text: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.2,
        response_schema: Optional[dict[str, Any]] = None,
        max_output_tokens: int = 32768,
    ) -> str:
        pdf_bytes = Path(pdf_path).read_bytes()
        inline = len(pdf_bytes) <= INLINE_PDF_MAX_BYTES
        encoded = base64.b64encode(pdf_bytes).decode("ascii") if inline else ""

        def parts_for(key: str):
            if inline:
                return (
                    [
                        {"text": user_text},
                        {"inline_data": {"mime_type": "application/pdf", "data": encoded}},
                    ],
                    None,
                )
            file_uri, file_name = self._upload_file(key, pdf_path, pdf_bytes)
            return (
                [
                    {"text": user_text},
                    {"file_data": {"mime_type": "application/pdf", "file_uri": file_uri}},
                ],
                lambda: self._delete_file(key, file_name),
            )

        return self._generate(
            parts_for,
            needs_pdf=True,
            system_instruction=system_instruction,
            temperature=temperature,
            response_schema=response_schema,
            max_output_tokens=max_output_tokens,
        )

    # ---- orchestration -----------------------------------------------------
    def _keys_rotated(self) -> list[str]:
        with self._key_lock:
            start = self._key_index
            self._key_index = (self._key_index + 1) % len(self.api_keys)
        return self.api_keys[start:] + self.api_keys[:start]

    def _limiter(self, kid: str, model: str) -> _RateLimiter:
        with _state_lock:
            limiter = _limiters.get((kid, model))
            if limiter is None:
                limiter = _RateLimiter(rpm_for(model), self._clock, self._sleep)
                _limiters[(kid, model)] = limiter
            return limiter

    def _is_exhausted(self, kid: str, model: str) -> bool:
        with _state_lock:
            until = _exhausted.get((kid, model))
            if until is None:
                return False
            if self._clock() >= until:
                del _exhausted[(kid, model)]
                return False
            return True

    def _mark_exhausted(self, kid: str, model: str, until: Optional[float] = None) -> None:
        with _state_lock:
            _exhausted[(kid, model)] = until or next_pacific_midnight(self._clock())

    def _generate(
        self,
        parts_factory: Callable[[str], tuple[list[dict[str, Any]], Optional[Callable[[], None]]]],
        needs_pdf: bool,
        system_instruction: Optional[str],
        temperature: float,
        response_schema: Optional[dict[str, Any]],
        max_output_tokens: int,
    ) -> str:
        return self._run(
            parts_factory,
            needs_pdf=needs_pdf,
            schema_capable=response_schema is not None,
            invoke=lambda key, model, parts, use_schema: self._call(
                key, model, parts, system_instruction, temperature,
                response_schema if use_schema else None, response_schema, max_output_tokens,
            ),
        )

    def _run(
        self,
        parts_factory: Callable[[str], tuple[list[dict[str, Any]], Optional[Callable[[], None]]]],
        needs_pdf: bool,
        schema_capable: bool,
        invoke: Callable[[str, str, list[dict[str, Any]], bool], Any],
    ) -> Any:
        """Model chain x key rotation with rate limiting and quota bookkeeping."""
        quota_hits = 0
        hard_error: Optional[Exception] = None
        eligible = 0

        for model in self.models:
            if needs_pdf and not supports_pdf(model):
                continue
            with _state_lock:
                if model in _dead_models:
                    continue
            eligible += 1
            use_schema = schema_capable
            model_dead = False

            for key in self._keys_rotated():
                kid = _key_id(key)
                if self._is_exhausted(kid, model):
                    quota_hits += 1
                    continue
                attempt = 0
                while attempt < self.max_attempts:
                    self._limiter(kid, model).acquire()
                    parts, cleanup = parts_factory(key)
                    try:
                        return invoke(key, model, parts, use_schema)
                    except GeminiError as exc:
                        status = exc.status_code
                        if status == 429:
                            info = parse_rate_limit(str(exc))
                            if info.daily:
                                logger.warning("Quota spent for %s (key %s); resets at Pacific midnight", model, kid)
                                self._mark_exhausted(kid, model)
                                quota_hits += 1
                                break
                            wait = (info.retry_after + 1.0) if info.retry_after else 15.0 * (attempt + 1)
                            logger.info("Rate limited on %s; waiting %.0fs", model, wait)
                            self._sleep(min(wait, MAX_RATE_WAIT_SECONDS))
                            attempt += 1
                            continue
                        if status is None and not exc.args:
                            hard_error = exc
                            break
                        if status in (500, 502, 503, 504) or (status is None and "network" in str(exc)):
                            logger.warning("Transient error on %s: %s", model, str(exc)[:200])
                            self._sleep(min(6 * 2**attempt, 45))
                            attempt += 1
                            hard_error = exc
                            continue
                        if status in (401, 403):
                            logger.error("Key %s rejected for %s (HTTP %s)", kid, model, status)
                            self._mark_exhausted(kid, model, until=self._clock() + 86400)
                            hard_error = exc
                            break
                        if status == 404 or (status == 400 and _model_unavailable(str(exc))):
                            logger.error("Model %s unavailable, skipping it: %s", model, str(exc)[:200])
                            with _state_lock:
                                _dead_models.add(model)
                            model_dead = True
                            break
                        if status == 400 and use_schema and _schema_rejected(str(exc)):
                            logger.warning("%s rejected the response schema; retrying without it", model)
                            use_schema = False
                            continue
                        logger.error("Gemini call failed on %s: %s", model, str(exc)[:300])
                        hard_error = exc
                        break
                    finally:
                        if cleanup:
                            cleanup()
                if model_dead:
                    break

        if eligible == 0:
            raise GeminiError("No eligible Gemini model in the chain for this request")
        if quota_hits and hard_error is None:
            raise QuotaExhaustedError(
                "Gemini free-tier quota is exhausted for every configured model/key "
                "(daily limits reset at midnight Pacific time)"
            )
        raise GeminiError(f"Gemini request failed on every model: {hard_error}")

    # ---- one HTTP call -----------------------------------------------------
    def _call(
        self,
        api_key: str,
        model: str,
        parts: list[dict[str, Any]],
        system_instruction: Optional[str],
        temperature: float,
        schema_to_send: Optional[dict[str, Any]],
        schema_requested: Optional[dict[str, Any]],
        max_output_tokens: int,
    ) -> str:
        gen_config: dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
        }
        system = system_instruction
        if schema_requested is not None:
            if schema_to_send is not None and supports_schema(model):
                gen_config["responseMimeType"] = "application/json"
                gen_config["responseSchema"] = schema_to_send
            else:  # Gemma / schema rejected: ask politely, the parser is tolerant
                hint = (
                    "\n\nRespond with ONE JSON object that conforms to this JSON schema. "
                    "No markdown fences, no commentary:\n" + json.dumps(schema_requested)
                )
                system = (system or "") + hint

        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": gen_config,
            "safetySettings": [
                {"category": c, "threshold": "BLOCK_NONE"}
                for c in (
                    "HARM_CATEGORY_HATE_SPEECH",
                    "HARM_CATEGORY_HARASSMENT",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "HARM_CATEGORY_DANGEROUS_CONTENT",
                )
            ],
        }
        if system:
            if model.lower().startswith("gemma"):  # no system role: fold into the prompt
                parts.insert(0, {"text": system})
            else:
                payload["systemInstruction"] = {"parts": [{"text": system}]}

        data = self._post(api_key, model, payload)
        text = _extract_text(data)
        candidate = (data.get("candidates") or [{}])[0]
        finish = candidate.get("finishReason")
        if not text:
            raise GeminiError(
                f"Empty Gemini response (finishReason={finish}, "
                f"promptFeedback={str(data.get('promptFeedback'))[:200]})"
            )
        if finish == "MAX_TOKENS" and schema_requested is not None:
            raise GeminiError(f"Structured output was truncated by maxOutputTokens on {model}")
        return text

    def _call_speech(
        self, api_key: str, model: str, parts: list[dict[str, Any]], speakers: Sequence[tuple[str, str]]
    ) -> tuple[bytes, int]:
        voices = [
            {"speaker": name, "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}
            for name, voice in speakers
        ]
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"multiSpeakerVoiceConfig": {"speakerVoiceConfigs": voices}},
            },
        }
        data = self._post(api_key, model, payload)
        audio = _extract_audio(data)
        if audio is None:
            finish = ((data.get("candidates") or [{}])[0]).get("finishReason")
            raise GeminiError(f"TTS returned no audio on {model} (finishReason={finish})")
        pcm, mime = audio
        if "pcm" not in mime.lower() and "l16" not in mime.lower():
            raise GeminiError(f"TTS returned an unexpected audio format: {mime}")
        rate = re.search(r"rate=(\d+)", mime)
        return pcm, int(rate.group(1)) if rate else 24000

    def _post(self, api_key: str, model: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{GEMINI_API_BASE}/models/{model}:generateContent"
        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self._transport) as client:
                response = client.post(url, headers={"x-goog-api-key": api_key}, json=payload)
        except httpx.TransportError as exc:
            raise GeminiError(f"network error: {type(exc).__name__}: {exc}") from exc
        if response.status_code >= 400:
            raise GeminiError(
                f"generateContent HTTP {response.status_code}: {response.text[:800]}",
                status_code=response.status_code,
            )
        return response.json()

    # ---- Files API (only for PDFs over the inline limit) --------------------
    def _upload_file(self, api_key: str, pdf_path: str, pdf_bytes: bytes) -> tuple[str, str]:
        headers = {
            "x-goog-api-key": api_key,
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(len(pdf_bytes)),
            "X-Goog-Upload-Header-Content-Type": "application/pdf",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.timeout_seconds, transport=self._transport) as client:
            start = client.post(
                f"{GEMINI_API_HOST}/upload/v1beta/files",
                headers=headers,
                json={"file": {"display_name": Path(pdf_path).name}},
            )
            if start.status_code >= 400:
                raise GeminiError(
                    f"file upload start HTTP {start.status_code}: {start.text[:500]}",
                    status_code=start.status_code,
                )
            upload_url = start.headers.get("X-Goog-Upload-URL")
            if not upload_url:
                raise GeminiError("File upload did not return X-Goog-Upload-URL")
            finish = client.post(
                upload_url,
                headers={
                    "Content-Length": str(len(pdf_bytes)),
                    "X-Goog-Upload-Offset": "0",
                    "X-Goog-Upload-Command": "upload, finalize",
                },
                content=pdf_bytes,
            )
            if finish.status_code >= 400:
                raise GeminiError(
                    f"file upload HTTP {finish.status_code}: {finish.text[:500]}",
                    status_code=finish.status_code,
                )
            info = finish.json().get("file") or {}
            if not info.get("uri") or not info.get("name"):
                raise GeminiError(f"Unexpected file upload response: {finish.text[:500]}")
            return info["uri"], info["name"]

    def _delete_file(self, api_key: str, file_name: str) -> None:
        try:
            with httpx.Client(timeout=30.0, transport=self._transport) as client:
                client.delete(f"{GEMINI_API_BASE}/{file_name}", headers={"x-goog-api-key": api_key})
        except Exception as exc:
            logger.warning("Failed to delete uploaded Gemini file %s: %s", file_name, exc)


def _model_unavailable(message: str) -> bool:
    return bool(re.search(r"not found|is not supported|not available|no longer available|deprecated", message, re.I))


def _schema_rejected(message: str) -> bool:
    return bool(re.search(r"schema|constraint|too many states|response_mime_type|responseMimeType", message, re.I))


def _extract_audio(payload: dict[str, Any]) -> Optional[tuple[bytes, str]]:
    """Concatenate every inline audio part (long replies may be split)."""
    chunks: list[bytes] = []
    mime = ""
    for candidate in payload.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data") or {}
            kind = inline.get("mimeType") or inline.get("mime_type") or ""
            if inline.get("data") and kind.lower().startswith("audio/"):
                chunks.append(base64.b64decode(inline["data"]))
                mime = mime or kind
    return (b"".join(chunks), mime) if chunks else None


def _extract_text(payload: dict[str, Any]) -> str:
    texts: list[str] = []
    for candidate in payload.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            # skip "thought" parts if a model returns its reasoning summary
            if part.get("thought"):
                continue
            text = part.get("text")
            if text:
                texts.append(text)
    return "\n".join(texts).strip()
