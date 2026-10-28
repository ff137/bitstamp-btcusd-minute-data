"""Fail when tracked text contains a workspace marker.

Every path from `git ls-files` is read. A file is binary, and skipped, only
when its bytes contain a NUL in the first 8192 bytes. Filenames, extensions,
and size are not consulted. Lockfiles in `EXCLUSIONS` are the only named
skips. In this file, only the line that states the regex literal is skipped.
"""

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
THIS_FILE = Path(__file__).resolve().relative_to(REPO).as_posix()
SNIFF_BYTES = 8192

# fmt: off
PATTERN = re.compile(r"\b(?:root|strategy|models|live-data|execution|datasets|inputs|evaluation|targets):\d+\b|mdboard|foresight-lab", re.IGNORECASE)
# fmt: on

# One-line justification per filename. This is not an extension list.
EXCLUSIONS = {
    "uv.lock": "Generated dependency lock; tool output, not authored prose.",
    "poetry.lock": "Generated dependency lock; tool output, not authored prose.",
    "package-lock.json": "Generated dependency lock; tool output, not authored prose.",
}


def tracked_paths() -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO,
        check=True,
        capture_output=True,
    )
    return [item for item in completed.stdout.decode().split("\0") if item]


def is_binary(path: Path) -> bool:
    with path.open("rb") as handle:
        sample = handle.read(SNIFF_BYTES)
    return b"\0" in sample


def is_pattern_definition(rel: str, line: str) -> bool:
    return rel == THIS_FILE and PATTERN.pattern in line


def marker_hits() -> list[str]:
    hits: list[str] = []
    for rel in tracked_paths():
        if Path(rel).name in EXCLUSIONS:
            continue
        path = REPO / rel
        if not path.is_file() or is_binary(path):
            continue
        try:
            with path.open(encoding="utf-8", newline="") as handle:
                lines = handle.readlines()
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(lines, start=1):
            if is_pattern_definition(rel, line):
                continue
            if PATTERN.search(line):
                hits.append(f"{rel}:{number}")
    return hits


def test_tracked_text_has_no_workspace_markers() -> None:
    for name, reason in EXCLUSIONS.items():
        assert "\n" not in reason and reason.strip(), name
    hits = marker_hits()
    assert not hits, "\n".join(hits)
