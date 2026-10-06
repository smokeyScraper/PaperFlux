import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# MongoDB configurations
DB_NAME = os.getenv("DB_NAME", "papers_summary_database")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "papers")
METADATA_COLLECTION = os.getenv("METADATA_COLLECTION", "metadata")

# API and URL configurations
HF_API_URL = "https://huggingface.co/api/daily_papers"
PDF_BASE_URL = "https://arxiv.org/pdf/{id}.pdf"

# Storage configurations
TEMP_DIR = "temp_papers"

# Agent models — separate keys mean both can use Flash-class models
# without sharing the same RPM/RPD quota.
GEMINI_ANALYST_MODEL = os.getenv("GEMINI_ANALYST_MODEL", "gemini-3.5-flash")
GEMINI_INSIGHTS_MODEL = os.getenv("GEMINI_INSIGHTS_MODEL", "gemini-3.5-flash")
GEMINI_API_HOST = os.getenv(
    "GEMINI_API_HOST", "https://generativelanguage.googleapis.com"
)
GEMINI_API_BASE = os.getenv("GEMINI_API_BASE", f"{GEMINI_API_HOST}/v1beta")

# How many papers to analyze concurrently. Each paper still runs
# analyst then insights sequentially. Keep this low on free tiers.
MAX_PAPER_WORKERS = int(os.getenv("PAPERFLUX_MAX_WORKERS", "2"))

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = PACKAGE_ROOT / "agents"


def collect_api_keys(prefix: str) -> list[str]:
    """Collect env keys named PREFIX, PREFIX2, PREFIX_2, PREFIX_3, ..."""
    found: list[tuple[str, str]] = []
    for env_key, value in os.environ.items():
        if not value or not str(value).strip():
            continue
        if env_key == prefix:
            found.append((env_key, value.strip()))
            continue
        suffix = env_key[len(prefix) :]
        if env_key.startswith(prefix) and suffix and (
            suffix.isdigit() or (suffix.startswith("_") and suffix[1:].isdigit())
        ):
            found.append((env_key, value.strip()))
    found.sort(key=lambda item: item[0])
    return [value for _, value in found]


def load_agent_keys(agent_prefix: str) -> list[str]:
    """Prefer agent-specific keys; fall back to legacy GEMINI_API_KEY*."""
    keys = collect_api_keys(agent_prefix)
    if keys:
        return keys
    return collect_api_keys("GEMINI_API_KEY")
