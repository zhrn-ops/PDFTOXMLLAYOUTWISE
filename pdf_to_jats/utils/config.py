"""Application configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path


@dataclass(slots=True)
class AppConfig:
    """Runtime configuration for the application."""

    app_name: str = "PDF to JATS 1.4 Scientific Article Converter"
    openrouter_model: str = "openrouter/free"
    openrouter_fallback_models: str = ""
    openrouter_api_key: str = ""
    # Keep only text with rendered ink. The check is performed per text line.
    visible_text_only: bool = True
    visible_text_dark_threshold: int = 240
    output_dir: Path = field(default_factory=lambda: Path("output"))
    generated_xml_dir: Path = field(default_factory=lambda: Path("output") / "generated_xml")
    openrouter_settings_path: Path = field(default_factory=lambda: Path("output") / "openrouter_settings.json")


def load_config() -> AppConfig:
    """Load application configuration.

    This keeps the first pass simple and fully local while leaving room for
    JSON/YAML-backed configuration later.
    """

    config = AppConfig()
    config.output_dir.mkdir(parents=True, exist_ok=True)
    config.generated_xml_dir.mkdir(parents=True, exist_ok=True)
    if config.openrouter_settings_path.exists():
        try:
            settings = json.loads(config.openrouter_settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            settings = {}
        config.openrouter_model = settings.get("model", config.openrouter_model)
        config.openrouter_fallback_models = settings.get(
            "fallback_models", config.openrouter_fallback_models
        )
        config.openrouter_api_key = settings.get("api_key", config.openrouter_api_key)
    config.openrouter_model = os.environ.get("OPENROUTER_MODEL", config.openrouter_model)
    config.openrouter_fallback_models = os.environ.get(
        "OPENROUTER_FALLBACK_MODELS", config.openrouter_fallback_models
    )
    config.openrouter_api_key = os.environ.get("OPENROUTER_API_KEY", config.openrouter_api_key)
    return config


def save_openrouter_settings(config: AppConfig) -> None:
    """Persist OpenRouter settings for GUI-driven runs."""

    config.output_dir.mkdir(parents=True, exist_ok=True)
    settings = {
        "model": config.openrouter_model,
        "fallback_models": config.openrouter_fallback_models,
        "api_key": config.openrouter_api_key,
    }
    config.openrouter_settings_path.write_text(
        json.dumps(settings, indent=2),
        encoding="utf-8",
    )
