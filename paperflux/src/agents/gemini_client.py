import base64
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from paperflux.src.config.settings import GEMINI_API_BASE, GEMINI_API_HOST

logger = logging.getLogger("paperflux.gemini_client")

INLINE_PDF_MAX_BYTES = 45 * 1024 * 1024


class GeminiError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class GeminiClient:
    """Per-agent Gemini client. Keys stay on the request, never on a process-global SDK."""

    def __init__(
        self,
        api_keys: list[str],
        model: str,
        timeout_seconds: float = 300.0,
        max_attempts: int = 3,
    ):
        if not api_keys:
            raise ValueError("GeminiClient requires at least one API key")
        self.api_keys = api_keys
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self._key_index = 0
        self._key_lock = threading.Lock()

    def current_key_count(self) -> int:
        return len(self.api_keys)

    def _next_key(self) -> str:
        with self._key_lock:
            key = self.api_keys[self._key_index]
            self._key_index = (self._key_index + 1) % len(self.api_keys)
            return key

    def generate_from_pdf(
        self,
        pdf_path: str,
        user_text: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.2,
    ) -> str:
        pdf_bytes = Path(pdf_path).read_bytes()
        last_error: Optional[Exception] = None

        for attempt in range(1, self.max_attempts + 1):
            api_key = self._next_key()
            file_name = None
            try:
                if len(pdf_bytes) <= INLINE_PDF_MAX_BYTES:
                    parts = [
                        {"text": user_text},
                        {
                            "inline_data": {
                                "mime_type": "application/pdf",
                                "data": base64.b64encode(pdf_bytes).decode("ascii"),
                            }
                        },
                    ]
                    return self._generate_content(
                        api_key, parts, system_instruction, temperature
                    )

                file_uri, file_name = self._upload_file(api_key, pdf_path, pdf_bytes)
                parts = [
                    {"text": user_text},
                    {
                        "file_data": {
                            "mime_type": "application/pdf",
                            "file_uri": file_uri,
                        }
                    },
                ]
                return self._generate_content(
                    api_key, parts, system_instruction, temperature
                )
            except GeminiError as exc:
                last_error = exc
                logger.error(
                    "Gemini attempt %s/%s failed for model %s: %s",
                    attempt,
                    self.max_attempts,
                    self.model,
                    exc,
                )
                if exc.status_code in {429, 503} or "quota" in str(exc).lower():
                    time.sleep(min(60 * attempt, 180))
                    continue
                if attempt >= self.max_attempts:
                    raise
            except Exception as exc:
                last_error = exc
                logger.error("Gemini attempt %s failed: %s", attempt, exc)
                if attempt >= self.max_attempts:
                    raise
                time.sleep(min(20 * attempt, 60))
            finally:
                if file_name:
                    self._delete_file(api_key, file_name)

        raise GeminiError(f"Gemini request failed after retries: {last_error}")

    def _generate_content(
        self,
        api_key: str,
        parts: list[dict[str, Any]],
        system_instruction: Optional[str],
        temperature: float,
    ) -> str:
        url = f"{GEMINI_API_BASE}/models/{self.model}:generateContent"
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": 65536,
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {
                    "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "threshold": "BLOCK_NONE",
                },
                {
                    "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                    "threshold": "BLOCK_NONE",
                },
            ],
        }
        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(url, params={"key": api_key}, json=payload)

        if response.status_code >= 400:
            raise GeminiError(
                f"generateContent HTTP {response.status_code}: {response.text[:800]}",
                status_code=response.status_code,
            )

        data = response.json()
        text = _extract_text(data)
        if not text:
            raise GeminiError(f"Empty Gemini response: {str(data)[:800]}")
        return text

    def _upload_file(
        self, api_key: str, pdf_path: str, pdf_bytes: bytes
    ) -> tuple[str, str]:
        display_name = Path(pdf_path).name
        headers = {
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(len(pdf_bytes)),
            "X-Goog-Upload-Header-Content-Type": "application/pdf",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.timeout_seconds) as client:
            start = client.post(
                f"{GEMINI_API_HOST}/upload/v1beta/files",
                params={"key": api_key},
                headers=headers,
                json={"file": {"display_name": display_name}},
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
            file_info = finish.json().get("file") or {}
            file_uri = file_info.get("uri")
            file_name = file_info.get("name")
            if not file_uri or not file_name:
                raise GeminiError(f"Unexpected file upload response: {finish.text[:500]}")
            return file_uri, file_name

    def _delete_file(self, api_key: str, file_name: str) -> None:
        try:
            with httpx.Client(timeout=30.0) as client:
                client.delete(
                    f"{GEMINI_API_BASE}/{file_name}",
                    params={"key": api_key},
                )
        except Exception as exc:
            logger.warning("Failed to delete uploaded Gemini file %s: %s", file_name, exc)


def _extract_text(payload: dict[str, Any]) -> str:
    texts: list[str] = []
    for candidate in payload.get("candidates") or []:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            text = part.get("text")
            if text:
                texts.append(text)
    return "\n".join(texts).strip()
