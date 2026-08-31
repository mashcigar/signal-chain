"""Optional model-backed observer. Off by default, on when ANTHROPIC_API_KEY is set.

The contract is the interesting part. The model is treated exactly like the regex matcher: an
untrusted claim generator. Every claim it emits must name a rubric dimension and carry a verbatim
quote from the page it read. Verification later confirms that quote against the stored bytes, the
same check every other claim faces. A hallucinated quote dies in stage 3 with a reason, the same
way hearsay does. Nothing downstream knows or cares whether a claim came from a model or a
substring match, and that is the point.

Standard library only, like everything else here. The API is reached with urllib, no SDK.
"""

import json
import os
import sys
import urllib.error
import urllib.request

import config

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
MAX_PAGE_CHARS = 15000
MAX_CLAIMS = 8

DIMENSION_MEANINGS = {
    "b2b_saas": "a B2B software company with a sales assisted motion",
    "paid_motion": "money already moving through paid advertising channels",
    "gtm_gap": "a visible go to market or revenue operations gap, such as a live hiring req",
    "scale_band": "past the point of having nothing to operate: customers, pricing, enterprise",
}


def available():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def model_name():
    return os.environ.get("SIGNAL_MODEL", "claude-sonnet-5")


def extract(page_text, target_name):
    """Ask the model for claims about one page. Returns a list of dicts:
    {dimension, text, quote}, where quote is a verbatim lowercase substring of the page text
    the model was shown. Claims with unknown dimensions or empty quotes are discarded here;
    claims whose quote is not actually on the page are left for verification to kill, because
    catching the model lying is stage 3's job and it should show up in the drop log."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return []

    excerpt = page_text[:MAX_PAGE_CHARS]
    dims = "\n".join(f"- {d}: {m}" for d, m in DIMENSION_MEANINGS.items())
    prompt = (
        f"You are reading the visible text of a public web page belonging to {target_name}.\n"
        f"Identify evidence for any of these dimensions:\n{dims}\n\n"
        "Respond with a JSON array only, no prose. Each item:\n"
        '{"dimension": "<one of the four keys>", "finding": "<one short sentence>", '
        '"quote": "<an EXACT contiguous substring copied from the page text below>"}\n'
        "At most two items per dimension. If the page shows nothing for a dimension, omit it.\n"
        "The quote must be copied verbatim from the text. Do not paraphrase inside the quote.\n\n"
        f"PAGE TEXT:\n{excerpt}"
    )

    body = json.dumps(
        {
            "model": model_name(),
            "max_tokens": 1200,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode()

    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "x-api-key": key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        },
    )
    try:
        raw = urllib.request.urlopen(request, timeout=60).read()
        payload = json.loads(raw)
        text = payload["content"][0]["text"].strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("[") :]
        items = json.loads(text[text.find("[") : text.rfind("]") + 1])
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError,
            KeyError, IndexError, ValueError) as error:
        print(f"  model extraction failed: {type(error).__name__}", file=sys.stderr)
        return []

    out = []
    for item in items[:MAX_CLAIMS]:
        if not isinstance(item, dict):
            continue
        dimension = item.get("dimension")
        quote = str(item.get("quote", "")).strip().lower()
        finding = str(item.get("finding", "")).strip()[:140]
        if dimension not in config.WEIGHTS or not quote:
            continue
        out.append(
            {"dimension": dimension, "text": f"model observed: {finding}", "quote": quote}
        )
    return out
