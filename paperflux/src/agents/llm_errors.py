"""Error types shared by every LLM client."""
from typing import Optional


class LLMError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class QuotaExhaustedError(LLMError):
    """Every (key, model) combination is out of free quota for now.

    Not a bug: the caller should stop spending requests and try again later.
    """
