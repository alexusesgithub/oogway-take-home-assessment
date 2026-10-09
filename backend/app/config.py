"""Typed application configuration.

Every knob is an environment variable (see `.env.example` at the repo root).
Switching the model provider, the model itself, or retrieval behaviour never
requires a code change.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["ollama", "anthropic"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Runtime ---------------------------------------------------------
    # Secure by default: the admin ingest endpoint is open only when APP_ENV=development.
    app_env: Literal["development", "production", "test"] = "production"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    static_dir: str = "static"

    # --- Database --------------------------------------------------------
    database_url: str = "postgresql+asyncpg://lenny:lenny@localhost:5432/lenny"
    db_pool_size: int = 5
    db_connect_timeout_seconds: float = 5.0

    # --- LLM provider toggle ----------------------------------------------
    llm_provider: ProviderName = "ollama"
    llm_fallback_provider: ProviderName | None = None
    llm_timeout_seconds: float = 300.0
    llm_max_concurrency: int = 2
    # "auto" = agentic search tool on for cloud models, off for small local models.
    agent_tools: Literal["auto", "on", "off"] = "auto"
    # "auto" = sectioned essays (outline + one call per section) for local models,
    # a single pass for cloud models. See app/services/essay.py.
    essay_strategy: Literal["auto", "single", "sectioned"] = "auto"

    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_base_url: str | None = None
    anthropic_max_output_tokens: int = 8000

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:3b"
    # Ollama defaults to a 4k context and silently truncates longer prompts.
    # When > 0 we provision a derived model with this context window pinned.
    ollama_num_ctx: int = 8192
    ollama_auto_provision: bool = True
    ollama_max_output_tokens: int = 2400

    # --- Knowledge base ---------------------------------------------------
    transcripts_repo_url: str = "https://github.com/ChatPRD/lennys-podcast-transcripts"
    transcripts_ref: str = "main"
    transcripts_dir: str = "./data/transcripts"
    auto_ingest: bool = True
    chunk_target_words: int = 280
    chunk_max_words: int = 420

    # Per-skill source budgets (top_k / top_k_local) live in each SKILL.md.
    retrieval_max_per_episode: int = 2
    retrieval_candidate_pool: int = 60

    embeddings_enabled: bool = False
    ollama_embed_model: str = "nomic-embed-text"
    embed_batch_size: int = 32

    # Protects POST /api/kb/ingest. Empty = endpoint disabled outside development.
    admin_token: SecretStr | None = None

    @field_validator("llm_fallback_provider", "anthropic_base_url", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("anthropic_api_key", "admin_token", mode="before")
    @classmethod
    def _empty_secret_to_none(cls, value: object) -> object:
        return None if value in ("", None) else value

    @property
    def ollama_runtime_model(self) -> str:
        """Name of the model actually served by Ollama (context-pinned alias)."""
        if self.ollama_num_ctx <= 0:
            return self.ollama_model
        base = self.ollama_model.replace(":", "-").replace("/", "-")
        return f"lenny-{base}:ctx{self.ollama_num_ctx}"

    def model_for(self, provider: ProviderName) -> str:
        return self.anthropic_model if provider == "anthropic" else self.ollama_model


@lru_cache
def get_settings() -> Settings:
    return Settings()
