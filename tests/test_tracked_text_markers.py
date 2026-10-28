"""Fail when tracked text contains a workspace marker.

Every path from `git ls-files` is read. A file is binary, and skipped, only
when its bytes contain a NUL in the first 8192 bytes. Every other file is
decoded as UTF-8 with surrogateescape, so a non-UTF-8 byte does not hide
the ASCII around it. Lockfiles in `EXCLUSIONS` are the only named skips.
In this file, only a stripped line that starts with `PATTERN = re.compile(`
is skipped.
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


def tracked_paths(repo: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return [item for item in completed.stdout.decode().split("\0") if item]


def is_binary(path: Path) -> bool:
    with path.open("rb") as handle:
        sample = handle.read(SNIFF_BYTES)
    return b"\0" in sample


def is_pattern_definition(rel: str, line: str) -> bool:
    return rel == THIS_FILE and line.strip().startswith("PATTERN = re.compile(")


def marker_hits(repo: Path = REPO) -> list[str]:
    hits: list[str] = []
    for rel in tracked_paths(repo):
        if Path(rel).name in EXCLUSIONS:
            continue
        path = repo / rel
        if not path.is_file() or is_binary(path):
            continue
        with path.open(
            encoding="utf-8", errors="surrogateescape", newline=""
        ) as handle:
            lines = handle.readlines()
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


def test_non_utf8_ascii_marker_is_caught(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    marker = "md" + "board"
    (repo / "notes.txt").write_bytes(b"\xff" + marker.encode("ascii") + b"\n")
    subprocess.run(
        ["git", "add", "notes.txt"], cwd=repo, check=True, capture_output=True
    )
    assert marker_hits(repo) == ["notes.txt:1"]
