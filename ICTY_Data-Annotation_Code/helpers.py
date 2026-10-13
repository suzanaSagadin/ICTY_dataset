"""
I/O helpers — save prompts, raw responses, reasoning and JSON files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


def save_raw(
    output_dir: str,
    name: str,
    content: str,
    reasoning: Optional[str] = None,
) -> None:
    """Save the LLM's raw response content to
    ``<output_dir>/log/<name>_raw.txt`` -- content only, no reasoning
    mixed in, so this file always reflects exactly what parser.py
    receives to extract/validate JSON from.

    When reasoning is present, it is saved separately to
    ``<output_dir>/log/<name>_reasoning.txt`` for standalone inspection.
    """
    log_dir = Path(output_dir) / "log"
    log_dir.mkdir(parents=True, exist_ok=True)

    (log_dir / f"{name}_raw.txt").write_text(content, encoding="utf-8")

    if reasoning:
        (log_dir / f"{name}_reasoning.txt").write_text(reasoning, encoding="utf-8")


def save_prompt(
    output_dir: str,
    name: str,
    system_prompt: str,
    user_message: str,
) -> None:
    """Save prompts to ``<output_dir>/log/<name>_prompt.txt``."""
    log_dir = Path(output_dir) / "log"
    log_dir.mkdir(parents=True, exist_ok=True)

    text = (
        f"=== SYSTEM PROMPT ===\n{system_prompt}\n\n"
        f"=== USER MESSAGE ===\n{user_message}"
    )
    (log_dir / f"{name}_prompt.txt").write_text(text, encoding="utf-8")


def load_json(path: str) -> Any:
    """Load a JSON file and return its contents."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: Any, path: str) -> None:
    """Save data as a JSON file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
