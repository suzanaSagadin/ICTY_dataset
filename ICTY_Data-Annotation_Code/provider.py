"""
LLM provider — direct HTTP calls to llama-server / OpenAI / Anthropic.

For local models a llama-server process is started automatically and
accessed via its OpenAI-compatible ``/v1/chat/completions`` endpoint.
"""

from __future__ import annotations

import ast
import atexit
import json
import re
import signal
import subprocess
import sys
import time
import urllib.request
import urllib.error

from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from settings import LLMConfig

# ── llama-server process management ─────────────────────────────────────────

_server_proc: subprocess.Popen | None = None
_server_model_key: str | None = None


def _model_key(config: "LLMConfig") -> str:
    return config.model_path or f"{config.repo_id}::{config.filename}"


def _resolve_gguf_path(config: "LLMConfig") -> str:
    """Return the local filesystem path to the GGUF file.

    If ``model_path`` is set it is returned directly.  Otherwise the file is
    downloaded (or fetched from cache) via ``huggingface_hub``.
    """
    if config.model_path:
        return config.model_path

    from huggingface_hub import hf_hub_download

    return hf_hub_download(repo_id=config.repo_id, filename=config.filename)


def _wait_for_server(port: int, timeout: float = 120.0) -> None:
    """Block until the llama-server /health endpoint returns ok."""
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                body = resp.read().decode()
                if '"status":"ok"' in body or resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionRefusedError, OSError):
            pass
        time.sleep(0.5)
    raise TimeoutError(
        f"llama-server did not become ready on port {port} within {timeout}s"
    )


def start_server(config: "LLMConfig") -> None:
    """Start (or reuse) a llama-server subprocess for the given config.

    Only one server runs at a time.  If a different model is requested the
    previous process is stopped first.
    """
    global _server_proc, _server_model_key

    key = _model_key(config)
    if _server_model_key == key and _server_proc is not None and _server_proc.poll() is None:
        return  # already running with the right model

    stop_server()

    gguf_path = _resolve_gguf_path(config)
    cmd = [
        config.llama_server_bin,
        "-m", gguf_path,
        "-c", str(config.n_ctx),
        "-ngl", str(config.n_gpu_layers),
        "--port", str(config.llama_server_port),
        "--host", "127.0.0.1",
        "--reasoning-format", "deepseek",
        "--reasoning-budget", str(config.reasoning_budget),
    ]

    print(f"\n  Starting llama-server: {' '.join(cmd)}")
    _server_proc = subprocess.Popen(
        cmd,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    _server_model_key = key

    atexit.register(stop_server)

    print(f"  Waiting for llama-server to become ready on port {config.llama_server_port} ...")
    _wait_for_server(config.llama_server_port)
    print(f"  llama-server is ready (PID {_server_proc.pid}).\n")


def stop_server() -> None:
    """Gracefully terminate the running llama-server, if any."""
    global _server_proc, _server_model_key

    if _server_proc is None:
        return

    if _server_proc.poll() is None:
        print(f"\n  Stopping llama-server (PID {_server_proc.pid}) ...")
        _server_proc.send_signal(signal.SIGTERM)
        try:
            _server_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _server_proc.kill()
            _server_proc.wait()
        print("  llama-server stopped.")

    _server_proc = None
    _server_model_key = None


# ── JSON extraction helpers ─────────────────────────────────────────────────

_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)


def extract_json_from_text(text: str) -> str:
    """Extract JSON from the model's free-form text response.

    Strips markdown fences and leading/trailing noise to isolate the JSON
    object or array the model produced.
    """
    cleaned = text.strip()

    # Strip leaked thinking block remnants (e.g. from deepseek/qwen reasoning
    # format): the real answer comes after the closing </think> tag.
    think_pos = cleaned.rfind("</think>")
    if think_pos != -1:
        cleaned = cleaned[think_pos + len("</think>"):].strip()

    fence_match = _FENCE_RE.search(cleaned)
    if fence_match:
        return fence_match.group(1).strip()

    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = cleaned.find(start_char)
        if start == -1:
            continue
        end = cleaned.rfind(end_char)
        if end > start:
            candidate = cleaned[start : end + 1]
            # Try json.loads first; if it fails, try ast.literal_eval
            # to handle Python dict syntax (single quotes)
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                try:
                    parsed = ast.literal_eval(candidate)
                    if isinstance(parsed, (dict, list)):
                        return json.dumps(parsed)
                except (SyntaxError, ValueError):
                    pass
                return candidate

    return cleaned


# ── Provider-specific exceptions ─────────────────────────────────────────────

class DHInfraTruncatedResponse(RuntimeError):
    """Raised when the DHInfra API returns empty content, typically because
    the model exhausted its token budget (reasoning and/or completion)
    before producing any answer text.

    Carries the reasoning trace (if any) and the reported finish_reason so
    the caller can persist them for inspection before the exception
    propagates -- without this, the raw response is lost entirely, since
    save_raw() is normally only called after call_llm() returns.
    """

    def __init__(
        self,
        message: str,
        *,
        reasoning: Optional[str] = None,
        finish_reason: Optional[str] = None,
    ):
        super().__init__(message)
        self.reasoning = reasoning
        self.finish_reason = finish_reason


# ── LLM call ────────────────────────────────────────────────────────────────

def _build_messages(
    config: "LLMConfig",
    system_prompt: str,
    user_message: str,
) -> List[Dict[str, str]]:
    """Build the chat-completion messages list."""
    messages: List[Dict[str, str]] = []
    if system_prompt:
        if config.provider != "local" or config.supports_system_prompt:
            messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_message})
    return messages


def _call_local(
    config: "LLMConfig",
    messages: List[Dict[str, str]],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_budget: int | None = None,
) -> Tuple[Optional[str], str]:
    """POST to llama-server's OpenAI-compatible chat/completions endpoint."""
    start_server(config)

    url = f"http://127.0.0.1:{config.llama_server_port}/v1/chat/completions"
    payload: Dict[str, Any] = {
        "messages": messages,
        "temperature": temperature if temperature is not None else config.temperature,
        "max_tokens": max_tokens if max_tokens is not None else config.max_tokens,
    }
    if reasoning_budget is not None:
        payload["reasoning_budget"] = reasoning_budget
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        body = json.loads(resp.read().decode())

    msg = body["choices"][0]["message"]
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or None
    content = msg.get("content") or ""

    if not content:
        finish_reason = body["choices"][0].get("finish_reason", "unknown")
        print(f"    WARNING: llama-server returned empty content (finish_reason={finish_reason!r})")
        if reasoning:
            print(f"    Model spent all tokens on thinking ({len(reasoning)} chars of reasoning, 0 content)")
        print(f"    Full response (truncated): {json.dumps(body, indent=2)[:500]}")

    return reasoning, content


def _call_openai(
    config: "LLMConfig",
    messages: List[Dict[str, str]],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> Tuple[Optional[str], str]:
    """Call the OpenAI chat completions API."""
    from openai import OpenAI

    client = OpenAI(api_key=config.api_key)
    response = client.chat.completions.create(
        model=config.model_name,
        messages=messages,
        temperature=temperature if temperature is not None else config.temperature,
        max_tokens=max_tokens if max_tokens is not None else config.max_tokens,
    )
    return None, response.choices[0].message.content


def _call_dhinfra(
    config: "LLMConfig",
    messages: List[Dict[str, str]],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_budget: int | None = None,
) -> Tuple[Optional[str], str]:
    """Call the DHInfra OpenAI-compatible chat-completions API."""

    if not config.api_url:
        raise ValueError(
            "api_url must be configured for provider 'dhinfra'"
        )

    if not config.api_key:
        raise ValueError(
            "api_key must be configured for provider 'dhinfra'"
        )

    if not config.model_name:
        raise ValueError(
            "model_name must be configured for provider 'dhinfra'"
        )

    payload: Dict[str, Any] = {
        "model": config.model_name,
        "messages": messages,
        "temperature": (
            temperature
            if temperature is not None
            else config.temperature
        ),
        "max_completion_tokens": (
            max_tokens
            if max_tokens is not None
            else config.max_tokens
        ),
    }

    # DHInfra expects the reasoning limit as "thinking_token_budget".
    # Internally, the pipeline keeps the existing name "reasoning_budget".
    if reasoning_budget is not None:
        payload["thinking_token_budget"] = reasoning_budget

    data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        config.api_url,
        data=data,
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=600,
        ) as response:
            body = json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode(
            "utf-8",
            errors="replace",
        )
        raise RuntimeError(
            f"DHInfra API returned HTTP {exc.code}: {error_body}"
        ) from exc

    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not connect to the DHInfra API: {exc.reason}"
        ) from exc

    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(
            "Unexpected DHInfra API response structure: "
            f"{json.dumps(body, ensure_ascii=False)[:1000]}"
        ) from exc

    content = message.get("content") or ""
    # NOTE: DHInfra returns the reasoning trace under the key "reasoning",
    # NOT "reasoning_content" (verified against the live API on 2026-09-08
    # -- OpenAI-compatible reasoning servers are inconsistent about this
    # field name). Both keys are checked as a defensive fallback in case
    # this changes across DHInfra model/server versions.
    reasoning = message.get("reasoning") or message.get("reasoning_content") or None

    if not content:
        finish_reason = body["choices"][0].get(
            "finish_reason",
            "unknown",
        )
        raise DHInfraTruncatedResponse(
            "DHInfra API returned empty content "
            f"(finish_reason={finish_reason!r})",
            reasoning=reasoning,
            finish_reason=finish_reason,
        )

    return reasoning, content


def _call_anthropic(
    config: "LLMConfig",
    messages: List[Dict[str, str]],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> Tuple[Optional[str], str]:
    """Call the Anthropic messages API."""
    import anthropic

    client = anthropic.Anthropic(api_key=config.api_key)

    system_message = ""
    user_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_message = msg["content"]
        else:
            user_messages.append(msg)

    kwargs: Dict[str, Any] = {
        "model": config.model_name,
        "messages": user_messages,
        "temperature": temperature if temperature is not None else config.temperature,
        "max_tokens": max_tokens if max_tokens is not None else config.max_tokens,
    }
    if system_message:
        kwargs["system"] = system_message

    response = client.messages.create(**kwargs)
    return None, response.content[0].text


def call_llm(
    config: "LLMConfig",
    system_prompt: str,
    user_message: str,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_budget: int | None = None,
) -> Tuple[Optional[str], str]:
    """Call the LLM and return ``(reasoning, content)``."""

    messages = _build_messages(
        config,
        system_prompt,
        user_message,
    )

    if config.provider == "local":
        return _call_local(
            config,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_budget=reasoning_budget,
        )

    if config.provider == "openai":
        return _call_openai(
            config,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    if config.provider == "anthropic":
        return _call_anthropic(
            config,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    if config.provider == "dhinfra":
        return _call_dhinfra(
            config,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_budget=reasoning_budget,
        )

    raise ValueError(
        f"Unknown LLM provider: {config.provider!r}"
    )
