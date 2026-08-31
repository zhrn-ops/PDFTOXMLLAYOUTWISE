"""Local transformers-based client."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


@dataclass(slots=True)
class HuggingFaceClient:
    """Local causal language model runner backed by transformers."""

    model_name: str
    token: str | None = None
    device_map: str = "auto"
    torch_dtype: str = "auto"
    max_new_tokens: int = 512
    _available: bool = True
    _missing_dependency_error: RuntimeError | None = None

    def __post_init__(self) -> None:
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:  # pragma: no cover - environment dependent
            self._available = False
            error = RuntimeError(
                "Local model support requires `torch` and `transformers` to be installed."
            )
            error.__cause__ = exc
            self._missing_dependency_error = error
            self._torch = None
            self._tokenizer = None
            self._model = None
            return

        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name, token=self.token)
        dtype = self._resolve_torch_dtype()
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            torch_dtype=dtype,
            device_map=self.device_map,
            token=self.token,
        )
        if self._tokenizer.pad_token is None:
            self._tokenizer.pad_token = self._tokenizer.eos_token

    def classify(self, prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._available:
            raise self._missing_dependency_error or RuntimeError(
                "Local model support requires `torch` and `transformers` to be installed."
            )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        text = self._tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self._tokenizer([text], return_tensors="pt")
        inputs = {key: value.to(self._model.device) for key, value in inputs.items()}

        with self._torch.no_grad():
            generated_ids = self._model.generate(
                **inputs,
                max_new_tokens=self.max_new_tokens,
                temperature=0.0,
                top_p=1.0,
                do_sample=False,
            )

        generated_ids = [
            output_ids[len(input_ids) :]
            for input_ids, output_ids in zip(inputs["input_ids"], generated_ids, strict=False)
        ]
        response = self._tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]
        return self._parse_response(response)

    def _resolve_torch_dtype(self):
        if self.torch_dtype == "auto":
            return "auto"
        return getattr(self._torch, self.torch_dtype)

    def _parse_response(self, response: str) -> dict[str, Any]:
        text = response.strip()
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
