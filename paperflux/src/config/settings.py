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



def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _model_chain(plural: str, singular: str, default: list[str]) -> list[str]:
    """Ordered fallback chain of model ids.

    ``GEMINI_*_MODELS`` (comma separated) wins. The legacy single
    ``GEMINI_*_MODEL`` is honoured as the first choice, followed by defaults.
    """
    chain = _csv(os.getenv(plural, ""))
    if chain:
        return chain
    legacy = os.getenv(singular, "").strip()
    if legacy:
        return [legacy] + [m for m in default if m != legacy]
    return list(default)


# Free-tier quotas are enforced PER PROJECT and PER MODEL (Flash: ~5 RPM / 20 RPD,
# Flash-Lite: ~500 RPD). A fallback chain turns each model into its own bucket.
# The analyst reads the whole PDF and needs the strongest model; the insights
# planner starts on a different Flash model so the two agents do not drain the
# same 20-requests-per-day pool.
DEFAULT_ANALYST_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]
DEFAULT_INSIGHTS_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
]
GEMINI_ANALYST_MODELS = _model_chain(
    "GEMINI_ANALYST_MODELS", "GEMINI_ANALYST_MODEL", DEFAULT_ANALYST_MODELS
)
GEMINI_INSIGHTS_MODELS = _model_chain(
    "GEMINI_INSIGHTS_MODELS", "GEMINI_INSIGHTS_MODEL", DEFAULT_INSIGHTS_MODELS
)
# Back-compat aliases (first choice of each chain)
GEMINI_ANALYST_MODEL = GEMINI_ANALYST_MODELS[0]
GEMINI_INSIGHTS_MODEL = GEMINI_INSIGHTS_MODELS[0]

# Optional second provider (any OpenAI-compatible endpoint, e.g. Groq's free tier)
# used for the text-only insights planner when Gemini quota runs out.
# Setting GROQ_API_KEY alone is enough.
_GROQ_KEY = os.getenv("GROQ_API_KEY", "").strip()
FALLBACK_LLM_API_KEYS = [
    k.strip() for k in (os.getenv("FALLBACK_LLM_API_KEY") or _GROQ_KEY).split(",") if k.strip()
]
FALLBACK_LLM_BASE_URL = os.getenv(
    "FALLBACK_LLM_BASE_URL", "https://api.groq.com/openai/v1" if _GROQ_KEY else ""
).strip()
FALLBACK_LLM_MODEL = os.getenv("FALLBACK_LLM_MODEL", "openai/gpt-oss-120b").strip()

# Requests-per-minute used by the client-side limiter. Free-tier Flash models are
# ~5 RPM; Flash-Lite and Gemma are higher. Override with GEMINI_RPM_OVERRIDES, e.g.
# "gemini-3.8-flash=5,gemini-3.5-flash-lite=15" (paid tiers can raise these a lot).
_RPM_OVERRIDES: dict[str, int] = {}
for _pair in _csv(os.getenv("GEMINI_RPM_OVERRIDES", "")):
    _name, _, _val = _pair.partition("=")
    if _name.strip() and _val.strip().isdigit():
        _RPM_OVERRIDES[_name.strip()] = int(_val)


def rpm_for(model: str) -> int:
    if model in _RPM_OVERRIDES:
        return _RPM_OVERRIDES[model]
    name = model.lower()
    if "tts" in name:
        return 3
    if "gemma" in name:
        return 15
    if "lite" in name:
        return 10
    return 5


# How many papers to process per day (top by upvotes). Free tiers are measured in
# requests per day, so this is the main budget knob: each paper costs about two
# requests (analyst + insights).
MAX_PAPERS = int(os.getenv("PAPERFLUX_MAX_PAPERS", "10"))
# Attach the PDF to the insights call as well (better grounding for numbers; costs
# input tokens, not requests).
INSIGHTS_ATTACH_PDF = os.getenv("PAPERFLUX_INSIGHTS_ATTACH_PDF", "1") not in {"0", "false", "False"}
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


# --------------------------------------------------------------------------
# Podcasts: a two-host audio conversation per paper
# --------------------------------------------------------------------------
# The script is written by a text model (Flash-Lite first: ~500 requests/day), the audio by
# one multi-speaker TTS call per episode. Free-tier TTS allows ~3 RPM / 10K TPM / 10 RPD,
# so PODCASTS_PER_DAY is enforced with a counter in MongoDB (restarts and manual runs share it).
PODCASTS_ENABLED = os.getenv("PAPERFLUX_PODCASTS", "1") not in {"0", "false", "False"}
PODCASTS_PER_DAY = int(os.getenv("PAPERFLUX_PODCASTS_PER_DAY", "10"))
PODCAST_MAX_ATTEMPTS = 2  # per paper: a paper that keeps failing must not drain the daily budget
PODCAST_TARGET_WORDS = int(os.getenv("PAPERFLUX_PODCAST_WORDS", "700"))  # ~5 min at a calm pace
DEFAULT_PODCAST_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.6-flash",
]
GEMINI_PODCAST_MODELS = _model_chain("GEMINI_PODCAST_MODELS", "GEMINI_PODCAST_MODEL", DEFAULT_PODCAST_MODELS)
# Confirm the exact id in AI Studio; unknown ids are skipped (404), so the chain also
# covers the "-preview-" naming.
DEFAULT_TTS_MODELS = ["gemini-3.8-flash-tts", "gemini-3.8-flash-preview-tts"]
GEMINI_TTS_MODELS = _model_chain("GEMINI_TTS_MODELS", "GEMINI_TTS_MODEL", DEFAULT_TTS_MODELS)
# Two calm hosts. Voice names are Gemini prebuilt voices ("Gentle" and "Smooth").
PODCAST_HOSTS = (
    {"name": os.getenv("PAPERFLUX_HOST_FEMALE", "Maya"), "gender": "female",
     "voice": os.getenv("GEMINI_TTS_VOICE_FEMALE", "Vindemiatrix")},
    {"name": os.getenv("PAPERFLUX_HOST_MALE", "Theo"), "gender": "male",
     "voice": os.getenv("GEMINI_TTS_VOICE_MALE", "Algieba")},
)


def load_podcast_keys() -> list[str]:
    """Podcast keys only, never borrowed from the analyst/insights (or legacy shared) keys:
    the daily papers need that project's free-tier quota. Use a key from its own project."""
    return collect_api_keys("GEMINI_PODCAST_API_KEY")


def load_tts_keys() -> list[str]:
    """TTS may get a project of its own; otherwise it uses the podcast project (TTS and the
    script writer are different models, so they do not share a quota bucket)."""
    return collect_api_keys("GEMINI_TTS_API_KEY") or load_podcast_keys()


def load_paper_agent_keys() -> list[str]:
    return load_agent_keys("GEMINI_ANALYST_API_KEY") + load_agent_keys("GEMINI_INSIGHTS_API_KEY")
