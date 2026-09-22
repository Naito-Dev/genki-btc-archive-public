#!/usr/bin/env python3
"""Bounded production orchestration. Only workflow_dispatch on main may run it."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib import request, error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from btc_signal.core import SignalError, iso_utc, load_verified_record
from btc_signal.daily import record_daily
from btc_signal.notify import prepare_notification, send_notification
from btc_signal.storage import atomic_write
from stage_site import stage
from verify_history import verify

REPOSITORY = "Naito-Dev/genki-btc-archive-public"
SITE = "https://www.btcsignal.org/"
STAGED_SITE_PATHS = []


def command(args, *, input_text=None, timeout=90):
    result = subprocess.run(args, cwd=ROOT, input=input_text, text=True,
                            capture_output=True, timeout=timeout)
    if result.returncode:
        # Never echo environment, remote errors, or raw API response bodies.
        raise SignalError("command_failed_" + Path(args[0]).name)
    return result.stdout.strip()


def api(path, method="GET", data=None):
    args = ["gh", "api", "repos/" + REPOSITORY + "/" + path, "--method", method]
    if data is not None:
        args += ["--input", "-"]
    value = command(args, input_text=json.dumps(data) if data is not None else None)
    return json.loads(value) if value else None


def context():
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"
            or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY):
        raise SignalError("production_workflow_context_required")
    run_id, attempt = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_RUN_ATTEMPT", "")
    if not run_id.isdigit() or not attempt.isdigit():
        raise SignalError("invalid_workflow_identity")
    return run_id + "-" + attempt


def open_record_prs():
    # Refuse to calculate a replacement if a previous result is waiting in a PR.
    pulls = api("pulls?state=open&per_page=100")
    if len(pulls) >= 100:
        raise SignalError("open_pr_inventory_requires_review")
    pending = [p for p in pulls if p["head"]["ref"].startswith("codex/daily-")]
    if pending:
        return pending
    # A push can survive a failed/lost PR-create response. Preserve such results too.
    refs = api("git/matching-refs/heads/codex/daily-")
    if len(refs) > 20:
        raise SignalError("preservation_branch_inventory_requires_review")
    for ref in refs:
        associated = api("commits/" + ref["object"]["sha"] + "/pulls?per_page=100")
        merged = [p for p in associated if p.get("merged_at") and p.get("base", {}).get("ref") == "main"
                  and p.get("head", {}).get("ref") == ref["ref"].removeprefix("refs/heads/")]
        if not merged:
            raise SignalError("previous_saved_branch_needs_attention")
    return []


def build():
    global STAGED_SITE_PATHS
    verify(ROOT)
    command([sys.executable, "scripts/build_site.py", "--root", str(ROOT), "--output", str(ROOT / "_site")])
    STAGED_SITE_PATHS = stage(ROOT)
    verify(ROOT)


def persist(run_id, phase, *, include_site=True):
    """Only generated state and allowlisted static files; normal PR merge, no bypass."""
    verify(ROOT)
    global STAGED_SITE_PATHS
    changes = command(["git", "diff", "--name-status", "HEAD", "--", "records", "inputs"])
    if any(line.split("\t", 1)[0] != "A" for line in changes.splitlines()):
        # In particular, a failed site verification must not preserve a tampered old record.
        raise SignalError("committed_record_or_input_changed")
    allowed = []
    if include_site:
        docs = json.loads((ROOT / "docs/build-manifest.json").read_text())["files"]
        allowed = ["docs/" + x for x in docs] + ["docs/build-manifest.json"] + STAGED_SITE_PATHS
    for directory in ("records", "inputs", "status", "publication", "delivery"):
        if (ROOT / directory).exists():
            allowed += [str(p.relative_to(ROOT)) for p in (ROOT / directory).rglob("*.json")]
    # -A includes deletions of formerly generated site files, including nested paths.
    directories = [d for d in ("records", "inputs", "status", "publication", "delivery") if (ROOT / d).exists()]
    if include_site:
        directories.append("docs")
    if directories:
        command(["git", "add", "-A", "--", *directories])
    if not command(["git", "diff", "--cached", "--name-only"]):
        return command(["git", "rev-parse", "HEAD"])
    staged = command(["git", "diff", "--cached", "--name-only"]).splitlines()
    if not set(staged) <= set(allowed):
        raise SignalError("unapproved_staged_path")
    branch = f"codex/daily-{run_id}-{phase}"
    command(["git", "checkout", "-b", branch])
    command(["git", "-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
             "commit", "-m", f"record: {phase} ({run_id})"])
    head = command(["git", "rev-parse", "HEAD"])
    command(["git", "push", "origin", branch])
    pull = api("pulls", "POST", {"head": branch, "base": "main", "title": f"BTC SIGNAL: {phase} ({run_id})",
               "body": "保存済みの確定結果・運用状態と、その結果から生成した静的ファイルです。過去原本のSHA-256を検証済みです。売買・ルール変更・履歴の再計算はありません。"})
    print("Preservation PR: " + pull["html_url"], flush=True)
    for _ in range(6):
        current = api("pulls/" + str(pull["number"]))
        if current.get("mergeable_state") == "clean":
            break
        time.sleep(2)
    else:
        raise SignalError("saved_pr_requires_review_or_conflict_resolution")
    merged = api(f"pulls/{pull['number']}/merge", "PUT", {"sha": head, "merge_method": "squash"})
    if not merged.get("merged"):
        raise SignalError("saved_pr_not_merged")
    command(["git", "fetch", "origin", merged["sha"]])
    command(["git", "checkout", "--detach", merged["sha"]])
    STAGED_SITE_PATHS = []
    # The merged branch is no longer needed; failure here leaves a harmless branch.
    try:
        api("git/refs/heads/" + branch, "DELETE")
    except SignalError:
        print("Merged preservation branch remains; no retry.", flush=True)
    return merged["sha"]


def publish(commit):
    # GITHUB_TOKEN commits do not trigger the implicit Pages build. Request it explicitly.
    tip = api("git/ref/heads/main")
    if tip["object"]["sha"] != commit:
        raise SignalError("pages_main_changed_retry_publication")
    api("pages/builds", "POST")
    for _ in range(30):
        builds = api("pages/builds?per_page=10")
        matches = [b for b in builds if b.get("commit") == commit]
        if matches and matches[0].get("status") == "built":
            return iso_utc(datetime.now(timezone.utc))
        if matches and matches[0].get("status") == "errored":
            raise SignalError("pages_build_failed")
        time.sleep(6)
    raise SignalError("pages_build_timeout")


def verify_public(record_file):
    expected = (ROOT / record_file).read_bytes()
    # A bounded cache-convergence check; never recompute a result.
    for attempt in range(6):
        try:
            req = request.Request(SITE + record_file + "?sha256=" + hashlib.sha256(expected).hexdigest(),
                                  headers={"Cache-Control": "no-cache", "User-Agent": "BTC-SIGNAL-Archive/1.0"})
            with request.urlopen(req, timeout=15) as response:
                actual = response.read(100001)
            if actual == expected:
                return
        except (error.URLError, TimeoutError, OSError):
            pass
        if attempt < 5:
            time.sleep(5)
    raise SignalError("published_record_does_not_match")


def operate(mode, record_file):
    run_id = context()
    if mode not in {"record", "publish_only", "notify_only"}:
        raise SignalError("invalid_operation_mode")
    if open_record_prs():
        raise SignalError("previous_saved_record_pr_needs_attention")
    verify(ROOT)
    calculation_error = None
    if mode == "record":
        if record_file:
            raise SignalError("record_mode_does_not_accept_a_date")
        try:
            record, _ = record_daily(ROOT, run_id=run_id)
            record_file = f"records/{record['rule_id']}/{record['target_date']}.json"
        except SignalError as exc:
            calculation_error = exc
    else:
        if not re.fullmatch(r"records/SMA100_CLOSE_V1/\d{4}-\d{2}-\d{2}\.json", record_file):
            raise SignalError("saved_record_path_required")
        record = load_verified_record(ROOT, record_file)
    if mode != "notify_only":
        try:
            build()
        except Exception:
            # Even a broken site build must not discard a newly fixed result or failure event.
            persist(run_id, "record-preservation", include_site=False)
            raise SignalError("site_build_failed_result_preserved") from None
        commit = persist(run_id, "record-and-site")
        published_at = publish(commit)
        if calculation_error:
            # The failure status is now durably saved and visible; keep the run failed.
            raise calculation_error
        verify_public(record_file)
        publication = {"record_id": record["record_id"], "result_sha256": record["result_sha256"],
                       "published_at_utc": published_at, "commit": commit}
        atomic_write(ROOT / "publication" / (record["record_id"] + ".json"), publication)
    else:
        path = ROOT / "publication" / (record["record_id"] + ".json")
        if not path.exists():
            raise SignalError("publication_receipt_missing_use_publish_only")
        publication = json.loads(path.read_text())
        if publication.get("result_sha256") != record["result_sha256"]:
            raise SignalError("publication_receipt_mismatch")
        published_at = publication["published_at_utc"]
        verify_public(record_file)
    intent = prepare_notification(ROOT, record_file, published_at)
    if intent["notification_state"] == "sent":
        persist(run_id, "publication-receipt")
        print("Already notified; unchanged record reused.")
        return
    # Persist intent BEFORE POST: a lost runner must never cause blind duplicate delivery.
    persist(run_id, "notification-intent")
    notification_error = None
    try:
        send_notification(ROOT, record_file, intent["attempt_id"])
    except SignalError as exc:
        notification_error = exc
    # Failure receipt and displayed delivery state are useful even when Discord is down.
    build()
    final_commit = persist(run_id, "notification-receipt")
    publish(final_commit)
    verify_public(record_file)
    if notification_error:
        raise notification_error
    print("Recorded, published, and notified: " + record["record_id"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--record-file", default="")
    args = parser.parse_args()
    try:
        operate(args.mode, args.record_file)
        return 0
    except (SignalError, subprocess.SubprocessError) as exc:
        code = exc.code if isinstance(exc, SignalError) else "process_timeout_or_failure"
        print("BTC SIGNAL failed: " + code, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
