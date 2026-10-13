"""
Output parser — extract, validate, and optionally auto-fix LLM responses.

Workflow:
1. Extract JSON (markdown fences, bare JSON objects/arrays)
2. Validate against a Pydantic model
3. On failure: if the fixer is enabled, ask the LLM to repair the JSON and retry
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, TypeVar, Type, TYPE_CHECKING

from pydantic import ValidationError

from provider import extract_json_from_text, call_llm
from helpers import save_raw

if TYPE_CHECKING:
    from settings import Settings

T = TypeVar("T")


def _create_fixing_prompt(malformed_text: str, model_class: type) -> tuple[str, str]:
    """Build the system + user messages for the fixer LLM call."""
    schema = model_class.model_json_schema()
    system = (
        "You are an expert at fixing malformed JSON. Your task is to convert "
        "the text I provide into valid JSON that can be parsed by Python's "
        "json.loads() function and validated by Pydantic.\n\n"
        f"Expected schema:\n{json.dumps(schema, indent=2)}\n\n"
        "Fix any syntax errors and ensure the structure matches the schema. "
        "Return ONLY the fixed JSON without any explanation."
    )
    user = (
        "Fix this malformed JSON so it can be parsed and validated:\n\n"
        f"{malformed_text}\n\n"
        "Return ONLY the fixed JSON."
    )
    return system, user


def _create_schema_echo_prompt(
    model_class: type,
    original_system: str = "",
    original_user: str = "",
) -> tuple[str, str]:
    """Build prompts for when the LLM returned the schema instead of data."""
    schema_hint = json.dumps(model_class.model_json_schema(), indent=2)

    if original_system and original_user:
        system = (
            original_system + "\n\n"
            "CRITICAL: Your previous attempt was WRONG — you returned the JSON Schema "
            "definition instead of actual data. Do NOT return the schema. "
            "Return a JSON object with REAL data conforming to the schema."
        )
        user = original_user
    else:
        system = (
            "You returned the JSON Schema definition instead of actual data. "
            "I need you to produce real output data.\n\n"
            f"Expected schema:\n{schema_hint}\n\n"
            "Return ONLY a JSON object with real data matching this schema. "
            "Do NOT return the schema itself."
        )
        user = "Return ONLY the correct JSON output with real data."
    return system, user


def fix_with_llm(
    malformed_text: str,
    model_class: type,
    settings: "Settings",
) -> str:
    """Ask the LLM to repair malformed JSON."""
    fixer_cfg = settings.fixer
    llm_cfg = fixer_cfg.llm if fixer_cfg.llm is not None else settings.llm

    system_prompt, user_message = _create_fixing_prompt(malformed_text, model_class)

    print("    Attempting to fix malformed output with LLM ...")
    _reasoning, fixed_content = call_llm(
        llm_cfg,
        system_prompt,
        user_message,
        temperature=fixer_cfg.temperature,
        max_tokens=fixer_cfg.max_tokens,
        reasoning_budget=fixer_cfg.reasoning_budget,
    )
    return fixed_content


def parse_and_validate(
    content: str,
    model_class: "Type[T]",
    settings: "Settings",
    *,
    name: str = "",
    output_dir: str = "",
    original_system: str = "",
    original_user: str = "",
) -> tuple["T", Optional[Dict[str, Any]]]:

    if not content or not content.strip():
        raise ValueError(
            f"[{name}] LLM returned empty content — cannot parse."
        )

    json_str = extract_json_from_text(content)

    try:
        data = json.loads(json_str)

        # --------------------------------------------------------------
        # Special case: model returned the JSON Schema instead of data
        # --------------------------------------------------------------
        if isinstance(data, dict) and "$defs" in data:
            if not settings.fixer.enabled:
                raise ValueError(
                    "LLM returned the JSON Schema definition instead of "
                    "conforming data."
                )

            print(
                "    Schema-echo detected: LLM returned the schema "
                "instead of data, retrying ..."
            )

            fixer_cfg = settings.fixer
            llm_cfg = (
                fixer_cfg.llm
                if fixer_cfg.llm is not None
                else settings.llm
            )

            sys_prompt, usr_msg = _create_schema_echo_prompt(
                model_class,
                original_system,
                original_user,
            )

            _reasoning, retry_content = call_llm(
                llm_cfg,
                sys_prompt,
                usr_msg,
                temperature=fixer_cfg.temperature,
                max_tokens=fixer_cfg.max_tokens,
                reasoning_budget=fixer_cfg.reasoning_budget,
            )

            if output_dir:
                save_raw(
                    output_dir,
                    f"{name}_schema_retry",
                    retry_content,
                )

            retry_json = extract_json_from_text(retry_content)
            retry_data = json.loads(retry_json)

            validated = model_class.model_validate(retry_data)

            return validated, {
                "type": "schema_retry",
            }

        # --------------------------------------------------------------
        # Normal successful path
        # --------------------------------------------------------------
        validated = model_class.model_validate(data)

        return validated, None

    except (json.JSONDecodeError, ValidationError) as exc:
        if not settings.fixer.enabled:
            raise

        print(
            f"    Parse/validation failed "
            f"({type(exc).__name__}): {exc}"
        )
        print(
            f"    Extracted JSON snippet: "
            f"{json_str[:300]!r}"
        )

        fixed_content = fix_with_llm(
            content,
            model_class,
            settings,
        )

        if output_dir:
            save_raw(
                output_dir,
                f"{name}_fixed",
                fixed_content,
            )

        fixed_json = extract_json_from_text(fixed_content)
        fixed_data = json.loads(fixed_json)

        validated = model_class.model_validate(fixed_data)

        return validated, {
            "type": "fixer",
            "trigger": type(exc).__name__,
        }
