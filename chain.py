"""Run the chain: observe, qualify, verify, digest, then stop at the gate.

    python3 chain.py                run it
    python3 chain.py --refresh      refetch pages instead of using cache
    python3 chain.py --offline      cache only, never touch the network

Claims persist between runs in state/claims-ledger.json, so verification re-checks what an
earlier run believed against the page as it exists now.

The last thing this program does is refuse to send.
"""

import argparse
import json
import sys
from datetime import datetime, timezone

import agents
import config
import model
from store import get_store


BAR = "=" * 72


def stage(number, name, detail=""):
    print()
    print(BAR)
    print(f"  STAGE {number}  {name}")
    if detail:
        print(f"           {detail}")
    print(BAR)


def main():
    parser = argparse.ArgumentParser(description="Signal chain with a human gate.")
    parser.add_argument("--refresh", action="store_true", help="refetch pages, ignore cache")
    parser.add_argument("--offline", action="store_true", help="cache only, no network")
    args = parser.parse_args()

    for directory in (config.CACHE, config.OUT, config.OUTBOX_PENDING, config.OUTBOX_APPROVED):
        directory.mkdir(parents=True, exist_ok=True)

    targets = json.loads(config.TARGETS_FILE.read_text())
    run_id = datetime.now(timezone.utc).strftime("run-%Y%m%d-%H%M%S")
    run_dir = config.OUT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    store = get_store()
    ledger = store.load_claims()
    carried = len(ledger)
    events = []

    print()
    print(f"  signal-chain  {run_id}")
    print(f"  {len(targets)} targets   mode: {'offline' if args.offline else 'live'}")
    model_line = f"on ({model.model_name()})" if model.available() else "off, deterministic matcher only (set ANTHROPIC_API_KEY to enable)"
    print(f"  model:  {model_line}")
    print(f"  store:  {store.name}  ({store.detail})")
    print(f"  ledger: {carried} claim(s) carried in from previous runs")

    # ------------------------------------------------------------ 1. observe
    stage(1, "OBSERVE", "read public pages, fold what is seen into the claim ledger")
    all_new, all_refreshed = [], []
    for target in targets:
        observed, pages = agents.observe(target, refresh=args.refresh, offline=args.offline)
        new_ids, refreshed_ids, _ = agents.merge_observations(ledger, observed)
        all_new += new_ids
        all_refreshed += refreshed_ids
        events += [
            {"claim_id": cid, "run_id": run_id, "action": "added", "reason": None}
            for cid in new_ids
        ]
        events += [
            {"claim_id": cid, "run_id": run_id, "action": "refreshed", "reason": None}
            for cid in refreshed_ids
        ]
        print(
            f"  {target['name']:<14} {len(pages)} pages  "
            f"{len(observed)} observed  ({len(new_ids)} new, {len(refreshed_ids)} already believed)"
        )
    print()
    print(f"  ledger now holds {len(ledger)} claim(s): {len(all_new)} added this run")

    by_target = {}
    for claim in ledger.values():
        by_target.setdefault(claim.target, []).append(claim)

    # ------------------------------------------------------------ 2. qualify
    stage(2, "QUALIFY", "score the believed claims against rubric.md, before anything is checked")
    initial = {}
    for target in targets:
        name = target["name"]
        initial[name] = agents.qualify(name, by_target.get(name, []))
        print(f"  {name:<14} {initial[name]['score']:>3}/100  {initial[name]['band']}")

    # ------------------------------------------------------------ 3. verify
    stage(3, "VERIFY", "try to break every believed claim, including ones made on an earlier run")
    results = []
    survivors = {}
    for target in targets:
        name = target["name"]
        kept, dropped = agents.verify(by_target.get(name, []))
        verified = agents.qualify(name, kept)
        move = initial[name]["score"] - verified["score"]
        arrow = f"  moved -{move}" if move else ""
        print(
            f"  {name:<14} kept {len(kept):>2}  dropped {len(dropped):>2}  "
            f"{verified['score']:>3}/100  {verified['band']}{arrow}"
        )
        for claim, reason in dropped:
            print(f"       drop  {claim.dimension:<12} {reason}")
        events += [
            {"claim_id": c.id, "run_id": run_id, "action": "dropped", "reason": r}
            for c, r in dropped
        ]
        events += [
            {"claim_id": c.id, "run_id": run_id, "action": "kept", "reason": None} for c in kept
        ]
        for claim in kept:
            survivors[claim.id] = claim
        results.append(
            {
                "target": name,
                "initial": initial[name],
                "verified": verified,
                "kept": kept,
                "dropped": dropped,
            }
        )

    # A claim that failed verification leaves the ledger. Hand entered notes never had a source, so
    # they fail every run; that is the point, and the digest reports it every time rather than
    # letting them rot quietly in a file.
    dead = [c for cid, c in ledger.items() if cid not in survivors]
    ledger = {cid: c for cid, c in ledger.items() if cid in survivors}
    store.save_claims(ledger)
    store.record_events(events)

    # ------------------------------------------------------------ 4. digest
    stage(4, "DIGEST", "render the Slack message from survivors only")
    message = agents.digest(results, run_id, offline=args.offline)
    print()
    for line in message.splitlines():
        print(f"    {line}")

    # ------------------------------------------------------------ artifacts
    (run_dir / "ledger-after.json").write_text(
        json.dumps(agents.claims_to_json(list(ledger.values())), indent=2)
    )
    (run_dir / "verification.json").write_text(
        json.dumps(
            [
                {
                    "target": r["target"],
                    "score_before": r["initial"]["score"],
                    "score_after": r["verified"]["score"],
                    "kept": [c.id for c in r["kept"]],
                    "dropped": [{"id": c.id, "reason": reason} for c, reason in r["dropped"]],
                }
                for r in results
            ],
            indent=2,
        )
    )

    # ------------------------------------------------------------ the gate
    pending = config.OUTBOX_PENDING / f"{run_id}.md"
    pending.write_text(message)
    (config.OUTBOX_PENDING / f"{run_id}.manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "targets": len(targets),
                "claims_carried_in": carried,
                "claims_added": len(all_new),
                "claims_kept": len(survivors),
                "claims_dropped": len(dead),
                "status": "pending_human_approval",
            },
            indent=2,
        )
    )

    print()
    print(BAR)
    print("  GATE")
    print(BAR)
    print()
    print("  Nothing has been sent.")
    print(f"  The digest is parked at  outbox/pending/{run_id}.md")
    print()
    print("  A human decides what happens next:")
    print(f"      python3 approve.py {run_id} --i-approve")
    print()
    print("  There is no flag on this program that sends. That is the point.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
