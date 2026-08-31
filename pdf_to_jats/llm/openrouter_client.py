"""OpenRouter API client."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any
import urllib.error
import urllib.request


@dataclass(slots=True)
class OpenRouterClient:
    """Minimal OpenRouter client using the OpenAI-compatible responses endpoint."""

    model: str = "google/gemma-4-26b-a4b-it:free"
    api_key: str | None = None
    base_url: str = "https://openrouter.ai/api/v1"
    _available: bool = True
    _missing_dependency_error: RuntimeError | None = None

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = os.environ.get("OPENROUTER_API_KEY")
        if not self.api_key:
            self._available = False
            self._missing_dependency_error = RuntimeError(
                "OpenRouter support requires OPENROUTER_API_KEY to be set."
            )

    def classify(self, prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._available:
            raise self._missing_dependency_error or RuntimeError("OpenRouter client is unavailable.")

        body = json.dumps(
            {
                "model": self.model,
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
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenRouter request failed: {exc.code} {exc.reason}: {detail}") from exc

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
