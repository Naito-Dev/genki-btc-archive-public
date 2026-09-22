"""Discord delivery of one already saved record; no market or strategy calls."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from urllib import error, parse, request

from .core import SignalError, UTC, iso_utc, load_verified_record, parse_utc, presentation, read_json
from .storage import atomic_write, lock


def build_message(record: dict) -> str:
    data = presentation(record)
    return (f"BTC SIGNAL | {data['rule_id']}\n"
            f"対象日: {data['target_date']} (UTC確定日足)\n"
            f"判定: {data['decision']}\n"
            f"終値: {data['close']} USD / SMA100: {data['sma100']} USD\n"
            f"取得元: {data['source']} / {data['product']}\n"
            f"記録ID: {data['record_id']}"
            f"\nSHA-256: {data['result_sha256']}\n"
            "記録専用の計算結果です。保有状態・発注指示ではありません。")


def webhook_url(value: str) -> str:
    try:
        parts = parse.urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise SignalError("invalid_discord_webhook") from exc
    if (parts.scheme != "https" or parts.hostname not in {"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"}
            or parts.username or parts.password or port not in (None, 443)
            or not parts.path.startswith("/api/webhooks/") or len(parts.path.split("/")) != 5
            or not all(parts.path.split("/")[-2:]) or parts.fragment):
        raise SignalError("invalid_discord_webhook")
    return parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "wait=true", ""))


def workflow_owner() -> str:
    run = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    if not run.isdigit() or not attempt.isdigit():
        raise SignalError("notification_workflow_context_required")
    return f"{run}:{attempt}"


def prepare_notification(root: Path, record_file: str | Path, published_at: str, *,
                         owner: str | None = None, now=None) -> dict:
    """Write intent only. The workflow MUST commit this file before send_notification."""
    root = Path(root).resolve()
    published = parse_utc(published_at)
    clock = now or (lambda: datetime.now(UTC))
    record = load_verified_record(root, record_file)
    if published < parse_utc(record["generated_at_utc"]) or published > clock():
        raise SignalError("invalid_published_at")
    delivery_path = root / "delivery" / f"{record['record_id']}.json"
    with lock(root, "discord"):
        previous = read_json(delivery_path) if delivery_path.exists() else {}
        if previous and (previous.get("record_id") != record["record_id"] or previous.get("result_sha256") != record["result_sha256"]):
            raise SignalError("delivery_record_mismatch")
        if previous.get("notification_state") == "sent":
            return previous
        if previous.get("notification_state") in {"pending", "uncertain"}:
            raise SignalError("delivery_uncertain_manual_review_required")
        if previous.get("notification_state") not in {None, "failed"}:
            raise SignalError("invalid_delivery_state")
        delivery = {"schema_version": "1.0", "record_id": record["record_id"],
                    "result_sha256": record["result_sha256"], "target_date": record["target_date"],
                    "published_at_utc": iso_utc(published), "notification_state": "pending",
                    "attempt_id": uuid.uuid4().hex, "owner_workflow_run": owner or workflow_owner(),
                    "attempted_at_utc": iso_utc(clock()), "message_id": None,
                    "attempts": int(previous.get("attempts", 0)) + 1}
        atomic_write(delivery_path, delivery)
        return delivery


def send_notification(root: Path, record_file: str | Path, attempt_id: str, *,
                      owner: str | None = None, opener=request.urlopen, now=None) -> dict:
    """Send a committed intent belonging only to this workflow run and attempt."""
    root = Path(root).resolve()
    clock = now or (lambda: datetime.now(UTC))
    record = load_verified_record(root, record_file)
    delivery_path = root / "delivery" / f"{record['record_id']}.json"
    with lock(root, "discord"):
        if not delivery_path.exists():
            raise SignalError("delivery_intent_missing")
        delivery = read_json(delivery_path)
        if delivery.get("record_id") != record["record_id"] or delivery.get("result_sha256") != record["result_sha256"]:
            raise SignalError("delivery_record_mismatch")
        if delivery.get("notification_state") == "sent":
            return delivery
        if (delivery.get("attempt_id") != attempt_id or delivery.get("owner_workflow_run") != (owner or workflow_owner())):
            raise SignalError("delivery_attempt_owner_mismatch")
        if delivery.get("notification_state") != "pending" or delivery.get("send_started_at_utc"):
            raise SignalError("delivery_uncertain_manual_review_required")
        # A repeated send in the same local runner is also blocked after interruption.
        delivery["send_started_at_utc"] = iso_utc(clock())
        atomic_write(delivery_path, delivery)
        try:
            configured = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
            if not configured:
                raise SignalError("discord_webhook_missing")
            url = webhook_url(configured)
            payload = json.dumps({"content": build_message(record), "allowed_mentions": {"parse": []}}, ensure_ascii=False).encode("utf-8")
            req = request.Request(url, data=payload, headers={"Content-Type": "application/json", "User-Agent": "BTC-SIGNAL-Archive/1.0"}, method="POST")
            with opener(req, timeout=15) as response:
                status = response.status
                body = response.read(65537)
            if not 200 <= status < 300:
                raise SignalError(f"discord_http_{status}")
            if len(body) > 65536:
                raise SignalError("discord_unconfirmed_response")
            message = json.loads(body)
            message_id = str(message.get("id", "")) if isinstance(message, dict) else ""
            if not message_id.isdigit():
                raise SignalError("discord_unconfirmed_response")
            delivery.update(notification_state="sent", message_id=message_id,
                            sent_at_utc=iso_utc(clock()))
            atomic_write(delivery_path, delivery)
            return delivery
        except error.HTTPError as exc:
            code = f"discord_http_{exc.code}"
            state = "failed" if 400 <= exc.code < 500 else "uncertain"
        except (error.URLError, TimeoutError, OSError):
            code, state = "discord_delivery_uncertain", "uncertain"
        except SignalError as exc:
            code = exc.code
            state = "failed" if code in {"discord_webhook_missing", "invalid_discord_webhook"} or code.startswith("discord_http_4") else "uncertain"
        except (ValueError, TypeError, AttributeError):
            code, state = "discord_unconfirmed_response", "uncertain"
        delivery.update(notification_state=state, error_code=code,
                        finished_at_utc=iso_utc(clock()))
        atomic_write(delivery_path, delivery)
        raise SignalError(code) from None


def notify_record(root: Path, record_file: str | Path, published_at: str, *,
                  opener=request.urlopen, now=None) -> dict:
    """Isolated-process convenience for tests; production CLI uses committed two-phase intent."""
    owner = "isolated:" + uuid.uuid4().hex
    intent = prepare_notification(root, record_file, published_at, owner=owner, now=now)
    if intent["notification_state"] == "sent":
        return intent
    return send_notification(root, record_file, intent["attempt_id"], owner=owner, opener=opener, now=now)
