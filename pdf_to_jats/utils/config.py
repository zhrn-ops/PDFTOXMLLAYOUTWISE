"""Application configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path


@dataclass(slots=True)
class AppConfig:
    """Runtime configuration for the application."""

    app_name: str = "PDF to JATS 1.4 Scientific Article Converter"
    openrouter_model: str = "openrouter/free"
    openrouter_api_key: str = ""
    # Keep only text with rendered ink. The check is performed per text line.
    visible_text_only: bool = True
    visible_text_dark_threshold: int = 240
    output_dir: Path = field(default_factory=lambda: Path("output"))
    generated_xml_dir: Path = field(default_factory=lambda: Path("output") / "generated_xml")


def load_config() -> AppConfig:
    """Load application configuration.

    This keeps the first pass simple and fully local while leaving room for
    JSON/YAML-backed configuration later.
    """

    config = AppConfig()
    config.openrouter_model = os.environ.get("OPENROUTER_MODEL", config.openrouter_model)
    config.openrouter_api_key = os.environ.get("OPENROUTER_API_KEY", config.openrouter_api_key)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    config.generated_xml_dir.mkdir(parents=True, exist_ok=True)
    return config
