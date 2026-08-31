"""The four agents, plus the claim ledger that lets a claim outlive the run that made it.

observe    reads public pages and records what it sees, with the receipt attached
qualify    scores a target against the rubric using the claims currently believed
verify     tries to break every believed claim, and records each drop with a reason
digest     renders the survivors into a Slack shaped message

The ledger is what makes verification honest. Without it, research and verification run in the
same breath off the same bytes and can never disagree, so the only thing verification can catch is
something you planted. With it, a claim recorded on Monday gets re-checked against Friday's page,
and dies when the page moves out from under it.
"""

import hashlib
import json
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import config
import model


# ---------------------------------------------------------------- claim record


@dataclass
class Claim:
    """One atomic assertion, with the receipt attached.

    A claim is deliberately small: a specific string was present at a specific URL at a specific
    time. Interpretation happens in qualify, never here. That split is what makes verify possible.
    """

    target: str
    dimension: str
    text: str
    method: str
    source_url: str = ""
    retrieved_at: str = ""
    evidence: str = ""
    id: str = ""
    first_seen: str = ""
    last_seen: str = ""

    def __post_init__(self):
        if not self.id:
            # sha256, not the builtin hash(). Python randomizes string hashing per process, so
            # builtin ids would change every run and the ledger could never match a claim to
            # itself. Found the hard way.
            stamp = f"{self.target}|{self.dimension}|{self.text}|{self.source_url}"
            self.id = "c" + hashlib.sha256(stamp.encode()).hexdigest()[:12]


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- fetch + cache


def _cache_path(url):
    slug = re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[:120]
    return config.CACHE / f"{slug}.json"


def fetch(url, refresh=False, offline=False):
    """Return (text, retrieved_at) for a public page, or (None, None).

    Cache first so a run is fast and repeatable. The cached retrieval timestamp travels with the
    content, so a claim built from cache reports when the bytes were actually obtained rather than
    when the chain happened to run.
    """
    path = _cache_path(url)

    if path.exists() and not refresh:
        blob = json.loads(path.read_text())
        return blob["text"], blob["retrieved_at"]

    if offline:
        return None, None

    request = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    try:
        raw = urllib.request.urlopen(request, timeout=config.FETCH_TIMEOUT).read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None, None

    html = raw.decode("utf-8", errors="replace")
    retrieved_at = _now()
    config.CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": url, "retrieved_at": retrieved_at, "text": html}))
    return html, retrieved_at


def visible_text(html):
    """Strip scripts, styles and tags. The raw HTML is appended so tracker signals such as
    googletagmanager stay findable, and the rubric says plainly that those are markup matches
    rather than prose."""
    body = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", body)
    return (body + " " + html).lower()


# ---------------------------------------------------------------- 1. observe


def observe(target, refresh=False, offline=False):
    """Record what is on the public pages right now. Returns (claims, pages_read)."""
    claims = []
    pages_read = []

    # Hand entered intel from the seed file. This is the CRM shaped data: someone typed it, nobody
    # attached a source. It enters on equal footing and gets judged in verification like everything
    # else.
    for note in target.get("notes", []):
        claims.append(
            Claim(
                target=target["name"],
                dimension=note["dimension"],
                text=note["text"],
                method="hand-entered",
            )
        )

    base = target["url"].rstrip("/")
    seen_pages = set()
    for path in config.PAGE_PATHS:
        url = base + path
        html, retrieved_at = fetch(url, refresh=refresh, offline=offline)
        if html is None:
            continue
        text = visible_text(html)

        # A missing /careers that quietly serves the homepage is common, and counting it as a
        # second source would inflate the evidence. Same bytes, same page, once.
        fingerprint = hashlib.sha256(text.encode()).hexdigest()
        if fingerprint in seen_pages:
            continue
        seen_pages.add(fingerprint)
        pages_read.append(url)

        for dimension, patterns in config.SIGNALS.items():
            for pattern in patterns:
                index = text.find(pattern)
                if index == -1:
                    continue
                start = max(0, index - 60)
                end = min(len(text), index + len(pattern) + 60)
                claims.append(
                    Claim(
                        target=target["name"],
                        dimension=dimension,
                        text=f"page contains {pattern!r}",
                        method="fetch+match",
                        source_url=url,
                        retrieved_at=retrieved_at,
                        evidence=text[start:end].strip(),
                    )
                )
                break  # one claim per dimension per page keeps the digest readable

        # The optional model pass. Claims born here are not trusted more than regex claims;
        # they carry a verbatim quote as evidence and stage 3 confirms it against the stored
        # bytes. A hallucinated quote dies in the drop log where everyone can see it.
        if model.available():
            for item in model.extract(text, target["name"]):
                claims.append(
                    Claim(
                        target=target["name"],
                        dimension=item["dimension"],
                        text=item["text"],
                        method="model-extract",
                        source_url=url,
                        retrieved_at=retrieved_at,
                        evidence=item["quote"],
                    )
                )

    return claims, pages_read


# ---------------------------------------------------------------- the ledger
#
# Persistence itself lives in store.py behind a two backend seam, so nothing in this file ever
# learns whether the ledger is a JSON file or a Postgres table. Merging is pure logic and belongs
# here; where the bytes land does not.


def merge_observations(ledger, observed):
    """Fold this run's observations into what was already believed.

    Returns (new_ids, refreshed_ids, unobserved). A claim that was not seen this run is NOT
    dropped here. Absence from one run is not disproof, and deciding that is verification's job.
    """
    now = _now()
    new_ids, refreshed_ids = [], []

    for claim in observed:
        existing = ledger.get(claim.id)
        if existing:
            existing.last_seen = now
            if claim.retrieved_at:
                existing.retrieved_at = claim.retrieved_at
                existing.evidence = claim.evidence
            refreshed_ids.append(claim.id)
        else:
            claim.first_seen = now
            claim.last_seen = now
            ledger[claim.id] = claim
            new_ids.append(claim.id)

    observed_ids = {c.id for c in observed}
    unobserved = [c for cid, c in ledger.items() if cid not in observed_ids]
    return new_ids, refreshed_ids, unobserved


# ---------------------------------------------------------------- 2. qualify


def qualify(target_name, claims):
    """Score against the rubric. A dimension counts when any claim supports it."""
    satisfied = {}
    for dimension in config.WEIGHTS:
        supporting = [c for c in claims if c.dimension == dimension]
        satisfied[dimension] = [c.id for c in supporting]

    score = sum(w for d, w in config.WEIGHTS.items() if satisfied[d])
    if score >= config.PURSUE_AT:
        band = "pursue"
    elif score >= config.WATCH_AT:
        band = "watch"
    else:
        band = "pass"

    return {"target": target_name, "score": score, "band": band, "dimensions": satisfied}


# ---------------------------------------------------------------- 3. verify


def verify(claims):
    """Adversarial pass over everything currently believed.

    Its only job is to break claims, including ones made on an earlier day. Returns
    (kept, dropped). Every drop carries a reason, because a claim that disappears quietly is worse
    than one that was never made.
    """
    kept, dropped = [], []
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.MAX_CLAIM_AGE_DAYS)

    for claim in claims:
        if not claim.source_url:
            dropped.append((claim, "no source url"))
            continue
        if not claim.retrieved_at:
            dropped.append((claim, "no retrieval timestamp"))
            continue

        try:
            retrieved = datetime.fromisoformat(claim.retrieved_at)
        except ValueError:
            dropped.append((claim, "unparseable retrieval timestamp"))
            continue
        if retrieved < cutoff:
            dropped.append((claim, f"stale, source older than {config.MAX_CLAIM_AGE_DAYS} days"))
            continue

        if not claim.evidence:
            dropped.append((claim, "no evidence captured"))
            continue

        # Grounding. Go back to the stored source as it exists NOW and confirm the evidence is
        # still there. This is the check that catches a claim whose page moved under it, which is
        # only possible because the claim outlived the run that made it.
        path = _cache_path(claim.source_url)
        if not path.exists():
            dropped.append((claim, "source no longer retrievable"))
            continue
        stored = visible_text(json.loads(path.read_text())["text"])
        if claim.evidence not in stored:
            dropped.append((claim, "evidence not found in the stored copy of the source"))
            continue

        kept.append(claim)

    return kept, dropped


# ---------------------------------------------------------------- 4. digest


def digest(results, run_id, offline=False):
    """Render the Slack shaped message. Survivors only, each with its receipt."""
    lines = []
    lines.append(f"*Prospect digest* `{run_id}`")
    lines.append("")
    if offline:
        lines.append("_Offline run. Pages were read from cache rather than fetched fresh._")
        lines.append("")

    pursue = [r for r in results if r["verified"]["band"] == "pursue"]
    watch = [r for r in results if r["verified"]["band"] == "watch"]
    below = [r for r in results if r["verified"]["band"] == "pass"]

    decayed = [
        (r["target"], c, reason)
        for r in results
        for c, reason in r["dropped"]
        if "not found in the stored copy" in reason
    ]

    lines.append(f"{len(pursue)} to pursue, {len(watch)} to watch, {len(below)} below the bar.")
    if decayed:
        lines.append(
            f"{len(decayed)} previously believed claim(s) died this run because the page changed."
        )
    lines.append("")

    for result in pursue + watch:
        before = result["initial"]["score"]
        after = result["verified"]["score"]
        move = "" if before == after else f"  (was {before} before verification)"
        lines.append(f"*{result['target']}*  {after}/100  {result['verified']['band']}{move}")

        # One line per dimension. The full claim set lives in the ledger and the run artifacts; a
        # digest that repeats the same evidence four times does not get read.
        shown = set()
        for claim in result["kept"]:
            if claim.dimension in shown:
                continue
            shown.add(claim.dimension)
            date = claim.retrieved_at.split("T")[0]
            others = sum(
                1 for c in result["kept"] if c.dimension == claim.dimension and c.id != claim.id
            )
            extra = f"  (+{others} more)" if others else ""
            first = claim.first_seen.split("T")[0] if claim.first_seen else date
            age = "" if first == date else f", first seen {first}"
            lines.append(f"  - {claim.dimension}: {claim.text}{extra}")
            lines.append(f"    source: {claim.source_url}  read {date}{age}")

        if result["dropped"]:
            lines.append(f"  dropped {len(result['dropped'])} unverified:")
            for claim, reason in result["dropped"]:
                lines.append(f"    - {claim.dimension}: {claim.text}  [{reason}]")
        lines.append("")

    if below:
        names = ", ".join(r["target"] for r in below)
        lines.append(f"_Below the bar: {names}_")
        lines.append("")

    lines.append("---")
    lines.append("Nothing in this digest has been sent. It is waiting for a human.")
    return "\n".join(lines)


def claims_to_json(claims):
    return [asdict(c) for c in claims]
