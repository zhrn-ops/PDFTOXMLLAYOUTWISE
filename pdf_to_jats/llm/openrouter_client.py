"""OpenRouter API client."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import time
from typing import Any
import urllib.error
import urllib.request


class OpenRouterRateLimitError(RuntimeError):
    """Raised when OpenRouter or an upstream provider keeps rate-limiting."""


@dataclass(slots=True)
class OpenRouterClient:
    """Minimal OpenRouter client using the OpenAI-compatible responses endpoint."""

    model: str = "openrouter/free"
    fallback_models: tuple[str, ...] = ()
    api_key: str | None = None
    base_url: str = "https://openrouter.ai/api/v1"
    max_retries: int = 2
    retry_delay_seconds: float = 2.0
    _available: bool = True
    _missing_dependency_error: RuntimeError | None = None

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = os.environ.get("OPENROUTER_API_KEY")
        if not self.fallback_models:
            fallback_models = os.environ.get("OPENROUTER_FALLBACK_MODELS", "")
            self.fallback_models = tuple(
                model.strip() for model in fallback_models.split(",") if model.strip()
            )
        if not self.api_key:
            self._available = False
            self._missing_dependency_error = RuntimeError(
                "OpenRouter support requires OPENROUTER_API_KEY to be set."
            )

    def classify(self, prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._available:
            raise self._missing_dependency_error or RuntimeError("OpenRouter client is unavailable.")

        errors: list[str] = []
        for model in self._models_to_try():
            try:
                return self._classify_with_model(model, prompt, payload)
            except OpenRouterRateLimitError as exc:
                errors.append(str(exc))
                continue
        if errors:
            raise OpenRouterRateLimitError("OpenRouter rate limit persisted: " + " | ".join(errors))
        raise RuntimeError("OpenRouter request failed before receiving a response.")

    def _models_to_try(self) -> tuple[str, ...]:
        models = [self.model]
        models.extend(model for model in self.fallback_models if model != self.model)
        return tuple(models)

    def _classify_with_model(self, model: str, prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(
            {
                "model": model,
                "input": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                "temperature": 0.0,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/responses",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                    break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code == 429:
                    if attempt < self.max_retries:
                        time.sleep(self._retry_delay(exc, attempt))
                        continue
                    raise OpenRouterRateLimitError(
                        f"{model} returned 429 Too Many Requests: {self._extract_error_message(detail)}"
                    ) from exc
                if exc.code in {500, 502, 503, 504} and attempt < self.max_retries:
                    time.sleep(self._retry_delay(exc, attempt))
                    continue
                raise RuntimeError(
                    f"OpenRouter request failed for {model}: {exc.code} {exc.reason}: {detail}"
                ) from exc

        text = raw.get("output_text") or ""
        if not text:
            output = raw.get("output") or []
            for item in output:
                for content in item.get("content", []):
                    if content.get("type") in {"output_text", "text"}:
                        text = content.get("text", "")
                        if text:
                            break
                if text:
                    break
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(text[start : end + 1])
            raise

    def _retry_delay(self, exc: urllib.error.HTTPError, attempt: int) -> float:
        retry_after = exc.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), 30.0)
            except ValueError:
                pass
        return min(self.retry_delay_seconds * (2**attempt), 30.0)

    def _extract_error_message(self, detail: str) -> str:
        try:
            error = json.loads(detail).get("error", {})
        except json.JSONDecodeError:
            return detail
        message = error.get("message")
        metadata = error.get("metadata") or {}
        raw = metadata.get("raw")
        provider = metadata.get("provider_name")
        parts = [part for part in (message, provider, raw) if part]
        return " - ".join(parts) if parts else detail
