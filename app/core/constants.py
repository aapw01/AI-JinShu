"""Hard-coded internal constants that do not need user configuration."""

from dataclasses import dataclass

# ── Scheduler internals ────────────────────────────────────────────────
CREATION_DISPATCH_POLL_SECONDS = 2
CREATION_MAX_DISPATCH_BATCH = 5
CREATION_WORKER_LEASE_TTL_SECONDS = 300
CREATION_WORKER_HEARTBEAT_SECONDS = 30
CREATION_RECOVERY_POLL_SECONDS = 5

# ── Quota defaults ──────────────────────────────────────────────────────
QUOTA_ENFORCE_CONCURRENCY_LIMIT = False
QUOTA_FREE_MONTHLY_CHAPTER_LIMIT = 1_000_000
QUOTA_FREE_MONTHLY_TOKEN_LIMIT = 10_000_000_000
QUOTA_ADMIN_MONTHLY_CHAPTER_LIMIT = 10_000_000
QUOTA_ADMIN_MONTHLY_TOKEN_LIMIT = 100_000_000_000

# ── LLM output contract ────────────────────────────────────────────────
LLM_OUTPUT_MAX_SCHEMA_RETRIES = 2
LLM_OUTPUT_MIN_CHARS = 120

# ── Generation prompt defaults ─────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ChapterLengthPolicy:
    min_words: int = 2000
    target_words: int = 2600
    soft_max_words: int = 3000
    hard_ceiling_words: int = 3500


DEFAULT_CHAPTER_LENGTH_POLICY = ChapterLengthPolicy()
DEFAULT_CHAPTER_WORD_COUNT = DEFAULT_CHAPTER_LENGTH_POLICY.target_words
