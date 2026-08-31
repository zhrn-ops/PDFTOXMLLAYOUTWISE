"""Local Ollama client."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
import urllib.request


@dataclass(slots=True)
class OllamaClient:
    """Minimal Ollama API client."""

    base_url: str = "http://localhost:11434"
    model: str = "qwen"

    def classify(self, prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        body = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "format": "json",
            }
        ).encode("utf-8")
        request = urllib.request.Request(f"{self.base_url}/api/chat", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
