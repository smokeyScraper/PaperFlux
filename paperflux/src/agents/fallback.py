"""Try several LLM clients in order (e.g. Gemini chain first, then Groq)."""
from __future__ import annotations

import logging
from typing import Any, Optional

from paperflux.src.agents.llm_errors import LLMError, QuotaExhaustedError

logger = logging.getLogger("paperflux.fallback")


class FallbackLLM:
    def __init__(self, clients: list[Any]):
        if not clients:
            raise ValueError("FallbackLLM needs at least one client")
        self.clients = clients

    @property
    def api_keys(self) -> list[str]:
        return list(getattr(self.clients[0], "api_keys", []))

    @property
    def can_read_pdf(self) -> bool:
        return any(hasattr(c, "generate_from_pdf") for c in self.clients)

    def generate_text(self, **kwargs: Any) -> str:
        return self._run("generate_text", kwargs)

    def generate_from_pdf(self, **kwargs: Any) -> str:
        return self._run("generate_from_pdf", kwargs)

    def _run(self, method: str, kwargs: dict[str, Any]) -> str:
        last: Optional[Exception] = None
        quota_only = True
        tried = 0
        for client in self.clients:
            fn = getattr(client, method, None)
            if fn is None:
                continue
            tried += 1
            try:
                return fn(**kwargs)
            except QuotaExhaustedError as exc:
                last = exc
                logger.warning("%s is out of quota, trying the next provider", type(client).__name__)
            except LLMError as exc:
                last = exc
                quota_only = False
                logger.warning("%s failed (%s), trying the next provider", type(client).__name__, str(exc)[:160])
        if tried == 0:
            raise LLMError(f"No configured client supports {method}")
        if quota_only and isinstance(last, QuotaExhaustedError):
            raise last
        raise LLMError(f"All providers failed; last error: {last}")
