"""Minimal client for OpenAI-compatible chat endpoints (Groq, OpenRouter, ...).

Used as a *text-only* fallback for the insights planner. Free tiers of these
providers are usually token-metered per day, so a per-day 429 marks the key
spent for a while instead of hammering the endpoint.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Callable, Optional

import httpx

from paperflux.src.agents.llm_errors import LLMError, QuotaExhaustedError

logger = logging.getLogger("paperflux.openai_compat")

DEFAULT_EXHAUSTED_SECONDS = 6 * 3600
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class OpenAICompatClient:
    def __init__(
        self,
        base_url: str,
        api_keys: list[str],
        model: str,
        timeout_seconds: float = 120.0,
        max_attempts: int = 3,
        transport: Optional[httpx.BaseTransport] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ):
        if not api_keys:
            raise ValueError("OpenAICompatClient requires at least one API key")
        self.base_url = base_url.rstrip("/")
        self.api_keys = api_keys
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self._transport = transport
        self._sleep = sleep
        self._clock = clock
        self._spent_until: dict[str, float] = {}
        self._lock = threading.Lock()
        self._index = 0

    def generate_text(
        self,
        user_text: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.2,
        response_schema: Optional[dict[str, Any]] = None,
        max_output_tokens: int = 8192,
    ) -> str:
        system = system_instruction or ""
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": temperature,
            "max_completion_tokens": max_output_tokens,
        }
        if response_schema is not None:
            body["response_format"] = {"type": "json_object"}
            system += (
                "\n\nRespond with ONE JSON object that conforms to this JSON schema. "
                "No markdown fences, no commentary:\n" + json.dumps(response_schema)
            )
        body["messages"] = (
            [{"role": "system", "content": system}] if system else []
        ) + [{"role": "user", "content": user_text}]

        last: Optional[Exception] = None
        skipped = 0
        for key in self._rotated():
            if self._is_spent(key):
                skipped += 1
                continue
            for attempt in range(self.max_attempts):
                try:
                    with httpx.Client(timeout=self.timeout_seconds, transport=self._transport) as client:
                        resp = client.post(
                            f"{self.base_url}/chat/completions",
                            headers={"Authorization": f"Bearer {key}"},
                            json=body,
                        )
                except httpx.TransportError as exc:
                    last = LLMError(f"network error: {type(exc).__name__}")
                    self._sleep(min(5 * 2**attempt, 30))
                    continue
                if resp.status_code == 429:
                    retry_after = _retry_after(resp)
                    daily = bool(re.search(r"per day|TPD|RPD|daily", resp.text, re.I))
                    if daily or (retry_after or 0) > 120:
                        with self._lock:
                            self._spent_until[key] = self._clock() + (retry_after or DEFAULT_EXHAUSTED_SECONDS)
                        skipped += 1
                        break
                    self._sleep(min((retry_after or 10.0) + 0.5, 60))
                    continue
                if resp.status_code >= 500:
                    last = LLMError(f"HTTP {resp.status_code}", resp.status_code)
                    self._sleep(min(5 * 2**attempt, 30))
                    continue
                if resp.status_code >= 400:
                    last = LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}", resp.status_code)
                    break
                content = _content(resp.json())
                if content:
                    return content
                last = LLMError("Empty completion")
                break
        if skipped and last is None:
            raise QuotaExhaustedError("Fallback LLM quota is exhausted for every key")
        raise LLMError(f"Fallback LLM failed: {last}")

    def _rotated(self) -> list[str]:
        with self._lock:
            start = self._index
            self._index = (self._index + 1) % len(self.api_keys)
        return self.api_keys[start:] + self.api_keys[:start]

    def _is_spent(self, key: str) -> bool:
        with self._lock:
            until = self._spent_until.get(key)
            if until is None:
                return False
            if self._clock() >= until:
                del self._spent_until[key]
                return False
            return True


def _retry_after(resp: httpx.Response) -> Optional[float]:
    value = resp.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _content(payload: dict[str, Any]) -> str:
    try:
        text = payload["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError):
        return ""
    return _THINK.sub("", text).strip()
