#!/usr/bin/env python3
"""Fail closed if any original evidence file was changed or removed."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(root=ROOT):
    manifest = json.loads((root / "config/legacy-sha256.json").read_text())
    failures = []
    for name, expected in manifest["files"].items():
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            failures.append(name)
    if failures:
        raise RuntimeError("original_history_changed: " + ", ".join(failures))
    return len(manifest["files"])


if __name__ == "__main__":
    print(f"Verified {verify()} original files against frozen SHA-256 manifest.")
