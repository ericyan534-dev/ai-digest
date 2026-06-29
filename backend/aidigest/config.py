"""Application settings, loaded from environment / .env (pydantic-settings).

Secrets live ONLY in .env (gitignored). NEVER hardcode the API key.
Access settings via the cached `get_settings()` accessor everywhere.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = .../ai_digest  (this file lives at ai_digest/backend/aidigest/config.py)
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    """Typed application configuration.

    Field names map case-insensitively to env vars. Unknown env vars are
    ignored so an over-broad .env never breaks startup.
    """

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- LLM (Google Gemini) ---
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-3.5-flash", alias="GEMINI_MODEL")
    gemini_embed_model: str = Field(
        default="gemini-embedding-001", alias="GEMINI_EMBED_MODEL"
    )
    # Model for the weekly best-of-N JUDGE. Blank => same as gemini_model. Set to a
    # different model (when available) for judge independence (design §7.2/§7.3).
    weekly_judge_model: str = Field(default="", alias="JUDGE_MODEL")
    # pgvector index limit is 2000 dims; Gemini default is 3072 => always request 1536.
    embed_dim: int = Field(default=1536, alias="EMBED_DIM")

    # --- Database ---
    database_url: str = Field(
        default="postgresql://aidigest:aidigest@localhost:5432/aidigest",
        alias="DATABASE_URL",
    )

    # --- Pipeline behavior ---
    # When True, the LLM factory returns a deterministic offline mock (no key/network).
    llm_mock: bool = Field(default=False, alias="AIDIGEST_LLM_MOCK")
    daily_max_items: int = Field(default=15, alias="AIDIGEST_DAILY_MAX_ITEMS")
    timezone: str = Field(default="America/Los_Angeles", alias="AIDIGEST_TIMEZONE")
    # Karpathy-wiki export: when set, digests are also written as linked Markdown
    # notes under this dir (Obsidian-style). Blank => disabled.
    wiki_dir: str = Field(default="", alias="AIDIGEST_WIKI_DIR")

    # --- LLM call defaults (reasoning model => generous output budget) ---
    # gemini-3.5-flash spends "thoughts" tokens; keep this generous so visible
    # text is not starved and MAX_TOKENS truncation is rare.
    gemini_max_output_tokens: int = Field(default=8192, alias="GEMINI_MAX_OUTPUT_TOKENS")
    gemini_temperature: float = Field(default=0.7, alias="GEMINI_TEMPERATURE")
    http_max_retries: int = Field(default=5, alias="AIDIGEST_HTTP_MAX_RETRIES")
    http_timeout_seconds: float = Field(default=60.0, alias="AIDIGEST_HTTP_TIMEOUT")

    # --- Optional delivery (blank => channel disabled, renderers still work) ---
    resend_api_key: str = Field(default="", alias="RESEND_API_KEY")
    digest_from_email: str = Field(default="", alias="DIGEST_FROM_EMAIL")
    digest_to_email: str = Field(default="", alias="DIGEST_TO_EMAIL")
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")

    # --- Optional observability (blank => Langfuse disabled, cheap no-op tracer) ---
    langfuse_public_key: str = Field(default="", alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(default="", alias="LANGFUSE_HOST")

    # --- API security (optional; blank => open, suitable for localhost only) ---
    api_key: str = Field(default="", alias="AIDIGEST_API_KEY")
    rate_limit_per_minute: int = Field(default=60, alias="AIDIGEST_RATE_LIMIT")
    # Public base URL of the API — builds clickable email feedback links.
    public_base_url: str = Field(
        default="http://localhost:8000", alias="AIDIGEST_PUBLIC_BASE_URL"
    )
    # HMAC secret signing email feedback links (blank => links unsigned/dev only).
    feedback_link_secret: str = Field(default="", alias="AIDIGEST_LINK_SECRET")
    # Shared secret Telegram echoes back on webhook calls (blank => unchecked).
    telegram_webhook_secret: str = Field(default="", alias="TELEGRAM_WEBHOOK_SECRET")

    # --- Reddit (optional OAuth; blank => public JSON fallback, often 403) ---
    reddit_client_id: str = Field(default="", alias="REDDIT_CLIENT_ID")
    reddit_client_secret: str = Field(default="", alias="REDDIT_CLIENT_SECRET")
    reddit_user_agent: str = Field(
        default="ai-digest/0.1 (personal news digest)", alias="REDDIT_USER_AGENT"
    )

    # --- Academia enrichment (Semantic Scholar citation velocity) ---
    semantic_scholar_api_key: str = Field(default="", alias="S2_API_KEY")
    enrich_academia: bool = Field(default=True, alias="AIDIGEST_ENRICH_ACADEMIA")

    # --- Web-reader fallback (Jina Reader) for thin RSS bodies ---
    web_reader_enabled: bool = Field(default=True, alias="AIDIGEST_WEB_READER")
    web_reader_min_chars: int = Field(default=280, alias="AIDIGEST_WEB_READER_MIN_CHARS")

    # --- AI-relevance gate: hard-drop non-AI stories (HN front page is noisy) ---
    relevance_filter: bool = Field(default=True, alias="AIDIGEST_RELEVANCE_FILTER")

    @property
    def gemini_base_url(self) -> str:
        """Base URL for the Gemini generative-language REST API."""
        return "https://generativelanguage.googleapis.com/v1beta"

    @property
    def email_enabled(self) -> bool:
        return bool(self.resend_api_key and self.digest_from_email and self.digest_to_email)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def api_auth_enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def link_signing_enabled(self) -> bool:
        return bool(self.feedback_link_secret)

    @property
    def reddit_oauth_enabled(self) -> bool:
        return bool(self.reddit_client_id and self.reddit_client_secret)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached Settings instance."""
    return Settings()
