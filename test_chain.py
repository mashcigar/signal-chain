"""Tests. Standard library unittest, nothing to install.

    python3 test_chain.py

The verification stage is the load bearing code here, so it gets the most attention: every drop
reason has a test, and the model contract is tested with a mocked API, including the case where
the model invents a quote and stage 3 kills it.
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import agents
import config
import model
from agents import Claim


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def old_iso(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def cache_page(url, html):
    """Write a cache entry the same way fetch() does."""
    path = agents._cache_path(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": url, "retrieved_at": now_iso(), "text": html}))


class TempCache(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = config.CACHE
        config.CACHE = Path(self._tmp.name)

    def tearDown(self):
        config.CACHE = self._old
        self._tmp.cleanup()


class TestClaimIdentity(unittest.TestCase):
    def test_same_inputs_same_id(self):
        a = Claim(target="X", dimension="b2b_saas", text="t", method="fetch+match", source_url="u")
        b = Claim(target="X", dimension="b2b_saas", text="t", method="fetch+match", source_url="u")
        self.assertEqual(a.id, b.id)

    def test_different_text_different_id(self):
        a = Claim(target="X", dimension="b2b_saas", text="t1", method="fetch+match")
        b = Claim(target="X", dimension="b2b_saas", text="t2", method="fetch+match")
        self.assertNotEqual(a.id, b.id)


class TestMerge(unittest.TestCase):
    def test_new_then_refreshed(self):
        ledger = {}
        claim = Claim(target="X", dimension="gtm_gap", text="t", method="fetch+match",
                      source_url="u", retrieved_at=now_iso(), evidence="e")
        new_ids, refreshed, _ = agents.merge_observations(ledger, [claim])
        self.assertEqual(len(new_ids), 1)
        self.assertEqual(len(refreshed), 0)

        again = Claim(target="X", dimension="gtm_gap", text="t", method="fetch+match",
                      source_url="u", retrieved_at=now_iso(), evidence="e2")
        new_ids, refreshed, _ = agents.merge_observations(ledger, [again])
        self.assertEqual(len(new_ids), 0)
        self.assertEqual(len(refreshed), 1)
        self.assertEqual(ledger[claim.id].evidence, "e2")


class TestQualify(unittest.TestCase):
    def _claim(self, dimension):
        return Claim(target="X", dimension=dimension, text="t", method="fetch+match")

    def test_bands(self):
        full = [self._claim(d) for d in config.WEIGHTS]
        self.assertEqual(agents.qualify("X", full)["band"], "pursue")
        some = [self._claim("gtm_gap"), self._claim("scale_band")]  # 30 + 20 = 50
        self.assertEqual(agents.qualify("X", some)["band"], "watch")
        self.assertEqual(agents.qualify("X", [])["band"], "pass")


class TestVerify(TempCache):
    URL = "https://example.com"
    HTML = "<p>We are hiring in Revenue Operations. Book a demo today.</p>"

    def grounded_claim(self, evidence="revenue operations", retrieved=None):
        return Claim(target="X", dimension="gtm_gap", text="t", method="fetch+match",
                     source_url=self.URL, retrieved_at=retrieved or now_iso(),
                     evidence=evidence)

    def test_drops_unsourced(self):
        claim = Claim(target="X", dimension="gtm_gap", text="hearsay", method="hand-entered")
        kept, dropped = agents.verify([claim])
        self.assertEqual(kept, [])
        self.assertIn("no source url", dropped[0][1])

    def test_drops_stale(self):
        cache_page(self.URL, self.HTML)
        claim = self.grounded_claim(retrieved=old_iso(config.MAX_CLAIM_AGE_DAYS + 5))
        kept, dropped = agents.verify([claim])
        self.assertEqual(kept, [])
        self.assertIn("stale", dropped[0][1])

    def test_drops_when_evidence_missing_from_source(self):
        cache_page(self.URL, self.HTML)
        claim = self.grounded_claim(evidence="a sentence that was never on this page")
        kept, dropped = agents.verify([claim])
        self.assertEqual(kept, [])
        self.assertIn("not found in the stored copy", dropped[0][1])

    def test_keeps_grounded_claim(self):
        cache_page(self.URL, self.HTML)
        kept, dropped = agents.verify([self.grounded_claim()])
        self.assertEqual(dropped, [])
        self.assertEqual(len(kept), 1)


def fake_api_response(items):
    body = json.dumps({"content": [{"text": json.dumps(items)}]}).encode()
    response = mock.MagicMock()
    response.read.return_value = body
    return response


class TestModelContract(TempCache):
    URL = "https://example.com"
    HTML = "<p>We are hiring in Revenue Operations.</p>"

    def test_extract_parses_filters_and_lowercases(self):
        items = [
            {"dimension": "gtm_gap", "finding": "hiring", "quote": "Revenue Operations"},
            {"dimension": "not_a_dimension", "finding": "x", "quote": "y"},
            {"dimension": "b2b_saas", "finding": "no quote", "quote": ""},
        ]
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test"}):
            with mock.patch("urllib.request.urlopen", return_value=fake_api_response(items)):
                out = model.extract("we are hiring in revenue operations.", "X")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["quote"], "revenue operations")

    def test_hallucinated_quote_dies_in_verification(self):
        cache_page(self.URL, self.HTML)
        items = [{"dimension": "gtm_gap", "finding": "invented", "quote": "we have no gtm team at all"}]
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test"}):
            with mock.patch("urllib.request.urlopen", return_value=fake_api_response(items)):
                extracted = model.extract("whatever", "X")
        claim = Claim(target="X", dimension=extracted[0]["dimension"], text=extracted[0]["text"],
                      method="model-extract", source_url=self.URL, retrieved_at=now_iso(),
                      evidence=extracted[0]["quote"])
        kept, dropped = agents.verify([claim])
        self.assertEqual(kept, [])
        self.assertIn("not found in the stored copy", dropped[0][1])

    def test_no_key_means_no_model(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(model.available())
            self.assertEqual(model.extract("text", "X"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
