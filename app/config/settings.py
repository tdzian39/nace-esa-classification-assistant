"""Runtime settings, loaded from environment variables and an optional ``app/.env`` file.

Rules:
- Never hardcode connection details; everything sensitive comes from the environment.
- Paths default relative to ``APP_ROOT`` (the ``app/`` directory) so they resolve
  regardless of the current working directory.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: The ``app/`` directory (repository layout root; ``/app`` inside the container).
APP_ROOT: Path = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """All configurable values. Field names map to environment variables case-insensitively."""

    model_config = SettingsConfigDict(
        env_file=APP_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Codebooks (build step 1) -------------------------------------------------------
    codebook_dir: Path = Field(
        default=APP_ROOT / "data" / "codebooks",
        description="Directory containing the four bootstrap xlsx codebooks.",
    )
    codebook_cts_ba0036_file: str = Field(
        default="CTS_BA0036_NEW.xlsx",
        description="CTS codebook BA0036: columns ID (CTS ID), VALUE (ESA code), DESCRIPTION.",
    )
    codebook_ba0036_valid_file: str = Field(
        default="BA0036_2024_jen_validni.xlsx",
        description="Valid ESA 2010 leaf codes: columns Kód, Název, Popis. Only these may be emitted.",
    )
    codebook_cts_okec_nace2_file: str = Field(
        default="CTS_OKEC_NACE2.xlsx",
        description="CTS codebook OKEC_NACE2: columns ID (CTS ID), VALUE (2-digit NACE), DESCRIPTION.",
    )
    codebook_nace_stat_file: str = Field(
        default="NACE_STAT.xlsx",
        description="NACE statistical labels: columns NACE (2-digit), Zkrtext, Text; several rows per code.",
    )
    codebook_version_label: str | None = Field(
        default=None,
        description="Optional human-readable label appended to the computed codebook version.",
    )

    # --- DWS, read-only (build step 2) --------------------------------------------------
    # Connection details never appear in code or in the repository; an empty DSN simply
    # means "DWS is not configured here", and the resolver falls back to ARES.
    dws_dsn: str | None = Field(
        default=None,
        description="ODBC DSN or connection string of the read-only DWS account. Empty disables DWS.",
    )
    dws_user: str | None = Field(default=None, description="DWS user (read-only account).")
    dws_password: SecretStr | None = Field(
        default=None, description="DWS password. Never logged, never echoed by the CLI."
    )
    dws_schema: str | None = Field(
        default=None,
        description="Schema qualifying the DWS objects. TODO: confirm the real schema name.",
    )
    dws_timeout_seconds: float = Field(
        default=30.0, gt=0, description="Per-query timeout for DWS reads."
    )

    # --- ARES fallback (build step 2) ---------------------------------------------------
    ares_enabled: bool = Field(
        default=True,
        description="Query the public ARES API for IČOs that DWS does not have.",
    )
    ares_base_url: str = Field(
        default="https://ares.gov.cz",
        description="Base URL of the public ARES REST API (ares.gov.cz).",
    )
    ares_timeout_seconds: float = Field(
        default=15.0, gt=0, description="HTTP timeout for a single ARES request."
    )
    ares_user_agent: str = Field(
        default="RBCZ-NACE-ESA-assistant/0.1 (internal Finance/MIS tool)",
        description="User-Agent sent to ARES so the operator can identify the caller.",
    )
    ares_min_interval_seconds: float = Field(
        default=0.25,
        ge=0,
        description=(
            "Minimum delay between two ARES requests. Observed on 2026-09-22: back-to-back "
            "requests start timing out, so a batch must pace itself. 0 disables the throttle."
        ),
    )
    ares_max_attempts: int = Field(
        default=3,
        ge=1,
        description="Attempts per ARES request, including the first; retries use linear backoff.",
    )

    # --- Web evidence for foreign issuers (build step 6) ---------------------------------
    # Used ONLY to describe foreign issuers. Czech subjects are answered from DWS/ARES and
    # never reach the web, and apl.czso.cz / or.justice.cz are never scraped (see
    # core.sources.web.BLOCKED_HOSTS, which enforces it).
    web_enabled: bool = Field(
        default=True, description="Allow web lookups for foreign-issuer descriptions."
    )
    web_search_url: str | None = Field(
        default=None,
        description=(
            "JSON search endpoint, with {query} where the search text goes. Empty means no "
            "search provider is configured and only user-supplied descriptions are used."
        ),
    )
    web_search_api_key: SecretStr | None = Field(
        default=None, description="API key for the search endpoint. Never logged."
    )
    web_search_api_key_header: str = Field(
        default="X-Subscription-Token",
        description="Header carrying the search API key. TODO: confirm for the chosen provider.",
    )
    web_search_results_path: str = Field(
        default="web.results",
        description=(
            "Dotted path to the result list in the search response. TODO: confirm for the "
            "chosen provider (Brave: web.results; Bing: webPages.value; Google CSE: items)."
        ),
    )
    web_search_field_url: str = Field(default="url", description="Result field holding the URL.")
    web_search_field_title: str = Field(default="title", description="Result field: title.")
    web_search_field_snippet: str = Field(
        default="description", description="Result field: snippet."
    )
    web_max_results: int = Field(
        default=4, ge=1, le=20, description="Search hits considered per issuer."
    )
    web_max_pages: int = Field(
        default=2, ge=0, le=10, description="Hits actually fetched and read."
    )
    web_timeout_seconds: float = Field(
        default=15.0, gt=0, description="HTTP timeout for one search or page fetch."
    )
    web_min_interval_seconds: float = Field(
        default=0.5, ge=0, description="Minimum delay between two outbound web requests."
    )
    web_max_description_chars: int = Field(
        default=1200,
        ge=200,
        description="Cap on the assembled description; keeps the classifier prompt small.",
    )
    web_user_agent: str = Field(
        default="RBCZ-NACE-ESA-assistant/0.1 (internal Finance/MIS tool)",
        description="User-Agent sent on web requests.",
    )

    # --- LLM classifier for foreign issuers (build step 6) -------------------------------
    # Only the public issuer name, the web-derived description and codebook labels are ever
    # put in a prompt. Nothing retrieved from DWS may reach a model (hard rule in CLAUDE.md).
    llm_enabled: bool = Field(
        default=True, description="Allow the model call. False makes the classifier abstain."
    )
    llm_provider: str = Field(default="openai", description="Provider adapter: 'openai' or 'stub'.")
    llm_model: str = Field(
        default="gpt-4o-mini",
        description=(
            "Model id. Default to the cheapest that passes /tests/golden; measure before "
            "changing. TODO: confirm the current cheapest id with the provider."
        ),
    )
    llm_api_key: SecretStr | None = Field(
        default=None,
        description="API key. Put it in app/.env, never in code. Never logged.",
    )
    llm_base_url: str = Field(
        default="https://api.openai.com/v1",
        description="API base URL; change it to point at another OpenAI-compatible endpoint.",
    )
    llm_timeout_seconds: float = Field(
        default=60.0, gt=0, description="Timeout for one model call."
    )
    llm_max_attempts: int = Field(
        default=3, ge=1, description="Attempts per call, including the first (backoff on 5xx)."
    )
    llm_temperature: float = Field(
        default=0.0,
        ge=0,
        le=2,
        description="0 so the same issuer and codebook give the same answer tomorrow.",
    )
    llm_max_output_tokens: int = Field(
        default=700,
        ge=100,
        description=(
            "Ceiling on generated tokens. Three picks with one Czech sentence each need a "
            "few hundred; the cap stops a rambling model being billed for rambling."
        ),
    )
    llm_max_suggestions: int = Field(
        default=3, ge=1, le=5, description="Ranked suggestions returned per codebook."
    )
    # --- spending limits, enforced in core/classify/budget.py ---------------------------
    # Set a cap in the provider dashboard too; that is the backstop. These stop spending
    # BEFORE the call and turn an exhausted budget into an abstention, not a crash.
    llm_max_prompt_tokens: int = Field(
        default=8_000,
        ge=0,
        description="Refuse a single request larger than this (~2x a normal issuer). 0 = off.",
    )
    llm_max_calls_per_run: int = Field(
        default=200,
        ge=0,
        description="Refuse after this many model calls in one process (2 per issuer). 0 = off.",
    )
    llm_daily_token_budget: int = Field(
        default=500_000,
        ge=0,
        description=(
            "Refuse once this many tokens have been used since midnight UTC. At ~3,400 "
            "tokens per issuer that is roughly 140 uncached issuers a day. 0 = off."
        ),
    )
    llm_usage_path: Path | None = Field(
        default=APP_ROOT / "data" / "cache" / "llm_usage.sqlite3",
        description="Usage ledger. Empty disables it AND the daily budget with it.",
    )
    llm_cache_path: Path | None = Field(
        default=APP_ROOT / "data" / "cache" / "classifications.sqlite3",
        description="SQLite cache of model answers. Empty disables caching.",
    )

    # --- Audit --------------------------------------------------------------------------
    web_user_header: str = Field(
        default="X-Remote-User",
        description=(
            "Request header carrying the signed-in user, set by the reverse proxy or SSO in "
            "front of this app. Without it every web lookup is audited as the service "
            "account, which does not satisfy 'log the requesting user'. The proxy MUST strip "
            "any client-supplied copy of this header. Empty disables the lookup."
        ),
    )
    lookup_user: str | None = Field(
        default=None,
        description="Requesting user recorded in the lookup audit log. Defaults to the OS user.",
    )

    # --- Logging ------------------------------------------------------------------------
    log_level: str = Field(default="INFO", description="Python logging level name.")

    @field_validator("codebook_dir", mode="after")
    @classmethod
    def _absolutize_codebook_dir(cls, value: Path) -> Path:
        """Relative directories are interpreted relative to ``APP_ROOT``, not the cwd."""
        return value if value.is_absolute() else (APP_ROOT / value).resolve()

    @field_validator("codebook_version_label", mode="before")
    @classmethod
    def _empty_label_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("dws_dsn", "dws_user", "dws_schema", "lookup_user", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        """An empty or whitespace-only variable means "not set", not an empty DSN."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def codebook_path(self, file_name: str) -> Path:
        """Absolute path of a codebook file inside ``codebook_dir``."""
        return self.codebook_dir / file_name

    @property
    def dws_configured(self) -> bool:
        """True when a DSN is set; otherwise DWS is skipped and ARES answers alone."""
        return bool(self.dws_dsn)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached settings instance."""
    return Settings()
