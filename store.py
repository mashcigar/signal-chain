"""Where claims live between runs.

Two backends behind one interface. FileStore is the default so the project clones and runs with
nothing installed and no account. SupabaseStore activates when SUPABASE_URL and
SUPABASE_SERVICE_ROLE_KEY are present in the environment.

The agents do not know which one is in use, and that is the whole point of the seam. Moving from a
JSON file to a Postgres spine with row level security changed this file and nothing else.

No SDK. PostgREST over the standard library, so the dependency count stays at zero.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict

import config
from agents import Claim


DEMO_WORKSPACE = "00000000-0000-0000-0000-000000000001"


class FileStore:
    """Local JSON. Fine for one operator on one machine, which is what this is."""

    name = "file"
    detail = None

    def __init__(self):
        self.detail = str(config.LEDGER_FILE.relative_to(config.ROOT))

    def load_claims(self):
        if not config.LEDGER_FILE.exists():
            return {}
        raw = json.loads(config.LEDGER_FILE.read_text())
        return {cid: Claim(**data) for cid, data in raw.items()}

    def save_claims(self, ledger):
        config.LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.LEDGER_FILE.write_text(
            json.dumps({cid: asdict(c) for cid, c in ledger.items()}, indent=2)
        )

    def record_events(self, events):
        """Append only, by convention here. The database version enforces it properly."""
        config.OUT.mkdir(parents=True, exist_ok=True)
        with (config.OUT / "claim-events.jsonl").open("a") as log:
            for event in events:
                log.write(json.dumps(event) + "\n")


class SupabaseStore:
    """Postgres behind PostgREST, reached with the service role key.

    The key is read from the environment and used server side only. It never appears in a client
    bundle, in a URL, or in this repository.
    """

    name = "supabase"

    def __init__(self, url, key, workspace_id=DEMO_WORKSPACE):
        self.url = url.rstrip("/")
        self.key = key
        self.workspace_id = workspace_id
        self.detail = f"{self.url}  workspace {workspace_id[:8]}"

    # ---------------------------------------------------------------- transport

    def _request(self, method, path, body=None, prefer=None):
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer

        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{self.url}/rest/v1/{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=config.FETCH_TIMEOUT) as response:
                payload = response.read().decode()
                return json.loads(payload) if payload.strip() else []
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:300]
            raise RuntimeError(f"supabase {method} {path} failed: {error.code} {detail}") from None

    # ---------------------------------------------------------------- interface

    def load_claims(self):
        rows = self._request(
            "GET",
            f"claims?select=*&workspace_id=eq.{self.workspace_id}",
        )
        ledger = {}
        for row in rows:
            ledger[row["id"]] = Claim(
                id=row["id"],
                target=row["target"],
                dimension=row["dimension"],
                text=row["claim_text"],
                method=row["method"],
                source_url=row.get("source_url") or "",
                retrieved_at=row.get("retrieved_at") or "",
                evidence=row.get("evidence") or "",
                first_seen=row.get("first_seen") or "",
                last_seen=row.get("last_seen") or "",
            )
        return ledger

    def save_claims(self, ledger):
        keep_ids = set(ledger)

        # Claims that failed verification leave the table. The row goes; the events explaining why
        # it went do not, because claim_events cannot be deleted.
        existing = self._request(
            "GET", f"claims?select=id&workspace_id=eq.{self.workspace_id}"
        )
        stale = [row["id"] for row in existing if row["id"] not in keep_ids]
        for claim_id in stale:
            self._request(
                "DELETE",
                f"claims?id=eq.{urllib.parse.quote(claim_id)}"
                f"&workspace_id=eq.{self.workspace_id}",
            )

        if not ledger:
            return
        rows = [
            {
                "id": claim.id,
                "workspace_id": self.workspace_id,
                "target": claim.target,
                "dimension": claim.dimension,
                "claim_text": claim.text,
                "method": claim.method,
                "source_url": claim.source_url or None,
                "retrieved_at": claim.retrieved_at or None,
                "evidence": claim.evidence or None,
                "first_seen": claim.first_seen,
                "last_seen": claim.last_seen,
            }
            for claim in ledger.values()
        ]
        self._request("POST", "claims", rows, prefer="resolution=merge-duplicates")

    def record_events(self, events):
        if not events:
            return
        rows = [dict(event, workspace_id=self.workspace_id) for event in events]
        self._request("POST", "claim_events", rows)


def get_store():
    """Supabase when it is configured, local file otherwise. Never both, never a silent fallback
    that hides a misconfiguration: if the environment is half set, that is worth saying out loud."""
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    if url and key:
        return SupabaseStore(url, key, os.environ.get("SIGNAL_WORKSPACE_ID", DEMO_WORKSPACE))
    if url or key:
        raise SystemExit(
            "\n  Half configured. Set both SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY, or neither.\n"
            "  Refusing to quietly fall back to the local file when a database was clearly intended.\n"
        )
    return FileStore()
