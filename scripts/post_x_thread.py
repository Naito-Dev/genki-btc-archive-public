#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests_oauthlib import OAuth1Session

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "x" / "last_posted.json"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Post Phase 0 X thread (2 tweets) once per latest_date")
    p.add_argument("--thread-json", required=True, help="JSON string from generate_x_thread_phase0.py")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(obj: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def require_env(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise RuntimeError(f"missing_env:{name}")
    return v


def main() -> int:
    args = parse_args()

    thread = json.loads(args.thread_json)
    latest_date = str(thread.get("latest_date") or "").strip()
    t1 = str(thread.get("tweet_1") or "")
    t2 = str(thread.get("tweet_2") or "")
    if not latest_date or not t1 or not t2:
        print("ERROR: invalid_thread_json")
        return 1

    prev = load_state()
    if str(prev.get("last_date") or "") == latest_date:
        print("NOOP: already_posted")
        return 0

    if args.dry_run:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        save_state({"last_date": latest_date, "last_posted_utc": now, "tweet_url": "DRY_RUN"})
        print("OK: dry_run_saved")
        return 0

    api_key = require_env("X_API_KEY")
    api_secret = require_env("X_API_SECRET")
    access_token = require_env("X_ACCESS_TOKEN")
    access_secret = require_env("X_ACCESS_TOKEN_SECRET")

    # OAuth 1.0a user-context for POST /2/tweets
    sess = OAuth1Session(api_key, client_secret=api_secret, resource_owner_key=access_token, resource_owner_secret=access_secret)

    def create_tweet(text: str, reply_to: str | None = None) -> str:
        payload = {"text": text}
        if reply_to:
            payload["reply"] = {"in_reply_to_tweet_id": reply_to}
        r = sess.post("https://api.twitter.com/2/tweets", json=payload, timeout=30)
        if r.status_code >= 300:
            raise RuntimeError(f"x_api_error:{r.status_code}:{r.text[:200]}")
        j = r.json()
        tid = j.get("data", {}).get("id")
        if not tid:
            raise RuntimeError("x_api_missing_tweet_id")
        return str(tid)

    id1 = create_tweet(t1)
    id2 = create_tweet(t2, reply_to=id1)

    username = os.getenv("X_USERNAME", "").strip()
    tweet_url = f"https://x.com/{username}/status/{id1}" if username else f"tweet_id:{id1}"

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    save_state({"last_date": latest_date, "last_posted_utc": now, "tweet_url": tweet_url, "tweet_id": id1, "reply_id": id2})

    print(f"OK: posted {tweet_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
