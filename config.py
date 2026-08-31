"""Configuration for the signal chain.

Everything tunable lives here so a reader can change behavior without reading the agents.
The rubric weights below are mirrored in prose in rubric.md. If you change one, change both.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CACHE = ROOT / "cache"
OUT = ROOT / "out"
OUTBOX_PENDING = ROOT / "outbox" / "pending"
OUTBOX_APPROVED = ROOT / "outbox" / "approved"
AUDIT_LOG = OUT / "audit.jsonl"

TARGETS_FILE = DATA / "targets.json"

# Every claim currently believed, carried between runs. This file is the reason verification can
# disagree with research: a claim recorded today gets re-checked against tomorrow's page.
LEDGER_FILE = ROOT / "state" / "claims-ledger.json"

USER_AGENT = "signal-chain/0.1 (reads public marketing pages only)"
FETCH_TIMEOUT = 15
PAGE_PATHS = ["", "/careers", "/jobs"]

# A claim older than this is dropped by the verification agent.
MAX_CLAIM_AGE_DAYS = 30

# Rubric. A dimension is satisfied when at least one VERIFIED claim supports it.
# Score is the sum of the weights of satisfied dimensions. Deliberately simple,
# because a rubric you cannot explain out loud is a rubric nobody will trust.
WEIGHTS = {
    "b2b_saas": 25,
    "paid_motion": 25,
    "gtm_gap": 30,
    "scale_band": 20,
}

PURSUE_AT = 60
WATCH_AT = 35

# What the research agent looks for on a public page, per rubric dimension.
# These are literal substring matches against the visible text plus the raw HTML,
# so every claim can point at the exact characters that produced it.
SIGNALS = {
    "b2b_saas": [
        "book a demo",
        "request a demo",
        "talk to sales",
        "contact sales",
        "for teams",
        "api docs",
    ],
    "paid_motion": [
        "googletagmanager",
        "doubleclick",
        "connect.facebook.net",
        "snap.licdn.com",
        "utm_source",
        "gclid",
    ],
    "gtm_gap": [
        "revenue operations",
        "demand generation",
        "growth marketing",
        "go-to-market",
        "gtm engineer",
        "marketing operations",
        "lifecycle marketing",
    ],
    "scale_band": [
        "trusted by",
        "customer stories",
        "case studies",
        "pricing",
        "enterprise",
    ],
}
