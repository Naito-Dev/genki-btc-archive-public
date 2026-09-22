#!/usr/bin/env python3
"""Copy only the builder's allowlisted static outputs to the Pages docs source."""
import json
import shutil
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def safe_name(name):
    if not isinstance(name, str) or "\\" in name or "\n" in name or "\r" in name:
        raise ValueError("unsafe_static_path")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts or str(path) != name:
        raise ValueError("unsafe_static_path")
    return name


def stage(root=ROOT):
    root = Path(root).resolve()
    source, target = root / "_site", root / "docs"
    target.mkdir(exist_ok=True)
    manifest = json.loads((source / "build-manifest.json").read_text())
    # Never touch the historical Markdown documents already in docs/.
    names = [safe_name(n) for n in manifest["files"]] + ["build-manifest.json"]
    old_path = target / "build-manifest.json"
    old = json.loads(old_path.read_text())["files"] if old_path.exists() else []
    protected = json.loads((root / "config/legacy-sha256.json").read_text())["files"]
    for name in set(old) | set(names):
        safe_name(name)
        if "docs/" + name in protected:
            raise ValueError("static_output_overlaps_original_history")
        if (target / name).resolve() != target / name:
            raise ValueError("unsafe_static_symlink")
    for name in names:
        candidate = source / name
        if candidate.resolve() != candidate or not candidate.is_file():
            raise ValueError("unsafe_static_source")
    for name in set(old) - set(names):
        (target / name).unlink(missing_ok=True)
    for name in names:
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, path)
    # Include removed generated paths so git add -A can stage their deletions.
    return ["docs/" + name for name in sorted(set(names) | set(old))]


if __name__ == "__main__":
    print(f"Staged {len(stage())} static files for Pages.")
