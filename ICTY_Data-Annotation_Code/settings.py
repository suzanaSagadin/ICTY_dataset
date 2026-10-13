"""
Pipeline settings — single file configuration.

Edit the defaults below or override via environment variables (prefix NER_).
"""

from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field, model_validator
import os


# ── Local model presets ──────────────────────────────────────────────────────

MODEL_PRESETS = {
    "qwen3-14b": {
        "repo_id": "bartowski/Qwen_Qwen3-14B-GGUF",
        "filename": "Qwen_Qwen3-14B-Q6_K_L.gguf",
        "supports_system_prompt": True,
    },
    "qwen3-32b": {
        "repo_id": "bartowski/Qwen_Qwen3-32B-GGUF",
        "filename": "Qwen_Qwen3-32B-Q4_K_M.gguf",
        "supports_system_prompt": True,
    },
    "qwen3.5-35b": {
        "repo_id": "bartowski/Qwen_Qwen3.5-35B-A3B-GGUF",
        "filename": "Qwen_Qwen3.5-35B-A3B-Q4_K_S.gguf",
        "supports_system_prompt": True,
    },
    "gemma-3-27b": {
        "repo_id": "bartowski/google_gemma-3-27b-it-qat-GGUF",
        "filename": "google_gemma-3-27b-it-qat-Q5_K_L.gguf",
        "supports_system_prompt": False,
    },
    "mistral-24b-instruct": {
        "repo_id": "bartowski/Mistral-Small-24B-Instruct-2501-GGUF",
        "filename": "Mistral-Small-24B-Instruct-2501-Q6_K_L.gguf",
        "supports_system_prompt": True,
    },
    "deepseek-r1-distill-qwen-32b": {
        "repo_id": "bartowski/DeepSeek-R1-Distill-Qwen-32B-GGUF",
        "filename": "DeepSeek-R1-Distill-Qwen-32B-Q4_K_L.gguf",
        "supports_system_prompt": True,
    },
}


class LLMConfig(BaseSettings):
    """LLM provider configuration."""

    provider: str = Field("local", description="'local', 'openai', 'anthropic' or 'dhinfra'")

    # ── Local model ──────────────────────────────────────────────────────
    model_preset: Optional[str] = Field(
        "qwen3.5-35b",
        description="Name of a preset from MODEL_PRESETS",
    )

    # Auto-filled from preset — override explicitly if needed.
    repo_id: Optional[str] = Field(None, description="HuggingFace repo (auto-filled from preset)")
    filename: Optional[str] = Field(None, description="GGUF filename (auto-filled from preset)")
    supports_system_prompt: Optional[bool] = Field(None, description="Auto-filled from preset")

    # Or point to an already-downloaded file
    model_path: Optional[str] = Field(
        None,
        description="Path to a local .gguf file. If set, preset/repo_id/filename are ignored.",
    )

    n_ctx: int = Field(32768, description="Context window size")
    n_gpu_layers: int = Field(-1, description="-1 = offload all layers to GPU")

    # ── llama-server ─────────────────────────────────────────────────────
    llama_server_bin: str = Field(
        os.environ.get("NER_LLAMA_SERVER_BIN", "llama-server"),
        description="Path to the llama-server binary",
    )
    llama_server_port: int = Field(8181, description="Port for llama-server HTTP API")

    # ── Cloud providers ──────────────────────────────────────────────────
    model_name: Optional[str] = Field(None, description="Model name for cloud (e.g. 'gpt-4o')")
    api_key: Optional[str] = Field(None, description="API key for cloud providers")
    api_url: Optional[str] = Field(None, description="API endpoint for cloud providers")

    # ── Generation ───────────────────────────────────────────────────────
    temperature: float = Field(0.3, description="Sampling temperature")
    max_tokens: int = Field(32768, description="Max tokens for generation (must be > reasoning_budget)")
    reasoning_budget: int = Field(
        16384,
        description="Token budget for thinking: -1 = unrestricted, 0 = disable, N>0 = cap",
    )

    @model_validator(mode="after")
    def _apply_preset(self) -> "LLMConfig":
        """Fill repo_id / filename / supports_system_prompt from the preset."""
        if self.provider != "local" or self.model_path:
            if self.supports_system_prompt is None:
                self.supports_system_prompt = True
            return self

        if self.model_preset:
            preset = MODEL_PRESETS.get(self.model_preset)
            if preset is None:
                available = ", ".join(MODEL_PRESETS)
                raise ValueError(
                    f"Unknown model_preset {self.model_preset!r}. "
                    f"Available: {available}"
                )
            if self.repo_id is None:
                self.repo_id = preset["repo_id"]
            if self.filename is None:
                self.filename = preset["filename"]
            if self.supports_system_prompt is None:
                self.supports_system_prompt = preset["supports_system_prompt"]
        else:
            if not self.repo_id or not self.filename:
                raise ValueError(
                    "Either model_preset, model_path, or repo_id+filename must be set"
                )
            if self.supports_system_prompt is None:
                self.supports_system_prompt = True

        return self

    @property
    def display_name(self) -> str:
        """Human-readable model name for output directories and logs."""
        if self.provider == "local":
            if self.model_preset:
                return self.model_preset
            if self.model_path:
                return self.model_path.rsplit("/", 1)[-1].replace(".gguf", "")
            return "local"
        return self.model_name or self.provider


class FixerConfig(BaseSettings):
    """Configuration for the output fixer (auto-fix malformed LLM responses)."""

    enabled: bool = Field(True, description="Enable auto-fix via LLM when parsing/validation fails")
    llm: Optional[LLMConfig] = Field(None, description="Override LLM for fixer (defaults to main LLM)")
    temperature: float = Field(0.0, description="Temperature for fixer LLM calls")
    max_tokens: int = Field(16000, description="Max tokens for fixer LLM calls")
    reasoning_budget: int = Field(1024, description="Reasoning token budget for fixer calls")


# ── LLM choice ───────────────────────────────────────────────────────────────
# Flip this to select the LLM backend.
# Options: "local", "openai", "anthropic" or "dhinfra"
LLM_BACKEND = "dhinfra"

LLM_CONFIGS = {
    "local": LLMConfig(
        provider="local",
        model_preset="qwen3.5-35b",
    ),
    "openai": LLMConfig(
        provider="openai",
        model_name="gpt-4o-mini",
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        temperature=0,
        max_tokens=2000,
    ),
    "dhinfra": LLMConfig(
        provider="dhinfra",
        api_url="https://api.dhinfra.uni-graz.at/v1/chat/completions",
        model_name="qwen3.5-397b",
        api_key=os.environ.get("DHINFRA_API_KEY", ""),
        temperature=0,
        max_tokens=10000,
        reasoning_budget=1024,
    ),
    "anthropic": LLMConfig(
        provider="anthropic",
        model_name="YOUR_ANTHROPIC_MODEL_NAME",
        api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        temperature=0,
        max_tokens=8000,
    ),
}


class Settings(BaseSettings):
    """Main pipeline settings."""

    # LLM
    llm: LLMConfig = LLM_CONFIGS[LLM_BACKEND]

    # I/O
    input_dir: str = Field(
        "data/input/accused_utterances.json",
        description="Directory containing input JSON files (all .json files will be processed)",
    )
    output_dir: str = Field("data/output", description="Base output directory")

    # Processing
    continue_on_error: bool = Field(True, description="Keep processing if one line fails")
    max_line_errors: Optional[int] = Field(
        100,
        description=(
            "Per-document tolerance for permanently failing utterances (e.g. "
            "persistent finish_reason='length', or errors that survive the "
            "parser's fixer/retry logic). None = zero tolerance: any "
            "utterance failure leaves the document incomplete and no "
            "*_output.json is written. N>=0 = up to N failing utterances "
            "per document are tolerated -- the document still completes "
            "and a *_output.json is written, with each failed utterance "
            "represented by an explicit placeholder record "
            "(status='failed', sentences=None) rather than being silently "
            "omitted. Full failure details are additionally written to a "
            "companion *_failed.json file for traceability. Utterances "
            "that were never attempted at all (e.g. continue_on_error="
            "False stopped the loop early) do NOT count toward this "
            "tolerance -- that still leaves the document incomplete."
        ),
    )
    save_raw_output: bool = Field(True, description="Save raw LLM responses")
    save_prompts: bool = Field(True, description="Save prompts sent to LLM")
    verbose: bool = Field(True, description="Detailed logging")

    # Fixer
    fixer: FixerConfig = Field(default_factory=FixerConfig)
