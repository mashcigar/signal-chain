"""The human side of the gate.

    python3 approve.py                          list what is waiting
    python3 approve.py <run-id> --i-approve     approve one digest

Approving moves the digest from pending to approved and writes an append-only audit line.
It still does not send. Wiring a real Slack call is a deliberate, separate act by whoever
owns that credential, and it belongs behind the same gate rather than inside this file.
"""

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone

import config


def list_pending():
    config.OUTBOX_PENDING.mkdir(parents=True, exist_ok=True)
    items = sorted(config.OUTBOX_PENDING.glob("*.md"))
    if not items:
        print("\n  Nothing pending.\n")
        return
    print(f"\n  {len(items)} digest(s) waiting on a human:\n")
    for item in items:
        manifest = item.with_suffix(".manifest.json")
        if manifest.exists():
            data = json.loads(manifest.read_text())
            print(
                f"    {item.stem}   {data['targets']} targets, "
                f"{data['claims_kept']} verified claims, {data['claims_dropped']} dropped"
            )
        else:
            print(f"    {item.stem}")
    print("\n  Approve with:  python3 approve.py <run-id> --i-approve\n")


def approve(run_id):
    source = config.OUTBOX_PENDING / f"{run_id}.md"
    if not source.exists():
        print(f"\n  No pending digest named {run_id}.\n")
        return 1

    manifest_path = config.OUTBOX_PENDING / f"{run_id}.manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    config.OUTBOX_APPROVED.mkdir(parents=True, exist_ok=True)
    destination = config.OUTBOX_APPROVED / f"{run_id}.md"
    shutil.move(str(source), str(destination))
    if manifest_path.exists():
        manifest["status"] = "approved"
        manifest["approved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        (config.OUTBOX_APPROVED / f"{run_id}.manifest.json").write_text(
            json.dumps(manifest, indent=2)
        )
        manifest_path.unlink()

    config.OUT.mkdir(parents=True, exist_ok=True)
    with config.AUDIT_LOG.open("a") as log:
        log.write(
            json.dumps(
                {
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "action": "approve_digest",
                    "run_id": run_id,
                    "claims_kept": manifest.get("claims_kept"),
                    "claims_dropped": manifest.get("claims_dropped"),
                }
            )
            + "\n"
        )

    print(f"\n  Approved {run_id}.")
    print(f"  Moved to outbox/approved/{run_id}.md")
    print(f"  Audit line appended to {config.AUDIT_LOG.relative_to(config.ROOT)}")
    print("\n  Still not sent. Sending is a separate act, behind this same gate.\n")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Approve a pending digest.")
    parser.add_argument("run_id", nargs="?", help="the run to approve")
    parser.add_argument(
        "--i-approve",
        action="store_true",
        help="required. approval is explicit or it is not approval",
    )
    args = parser.parse_args()

    if not args.run_id:
        list_pending()
        return 0
    if not args.i_approve:
        print("\n  Refusing. Approval must be explicit: add --i-approve\n")
        return 1
    return approve(args.run_id)


if __name__ == "__main__":
    sys.exit(main())
