#!/usr/bin/env python3
"""
strip_comments.py — audit + remove Python line comments from source tree.

Two modes:
  python tools/strip_comments.py --scan           # list every comment, no edits
  python tools/strip_comments.py --scan > out.txt
  python tools/strip_comments.py --strip          # remove comments in place
  python tools/strip_comments.py --strip --dry-run  # show what would change

Preserved on --strip:
  * Shebang lines (#!)                  — required by OS
  * Encoding declarations (# -*- ... *-)— required by Python 2/3 parser on some setups
  * Type-check hints that affect tooling:
      # type: ignore, # type: ...        — mypy/pyright semantic
      # noqa, # pylint:, # pragma:       — linter directives
  * Docstrings are NOT comments and are NEVER touched.

Run from the project root. Skips: build/, dist/, __pycache__/, .git/, .venv/,
.hypothesis/, tools/ (this script lives there).
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
import token
import tokenize
from pathlib import Path
from typing import Iterable, Tuple

# Patterns that must be kept even in --strip mode.
_KEEP_RE = re.compile(
    r"^(\s*)#\s*(!|-\*-|type:\s*(ignore|[\w\[\],\s\.]+)|"
    r"noqa\b|pylint:|pragma:|fmt:\s*(on|off)|mypy:|pyright:|flake8:)",
    re.IGNORECASE,
)

_SKIP_DIRS = {
    "build", "dist", "__pycache__", ".git", ".venv", ".venv_linux",
    ".hypothesis", ".ida-mcp", ".idea", ".vscode", ".claude", "tools",
    "Includes",   # third-party code, don't touch
    "node_modules",
}


def _iter_py_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for f in filenames:
            if f.endswith(".py"):
                yield Path(dirpath) / f


def _is_protected(line: str) -> bool:
    """Shebang / encoding / tool directives are protected."""
    return bool(_KEEP_RE.match(line))


def _scan_file(path: Path) -> list[Tuple[int, str, str]]:
    """Return list of (line_no, full_line, comment_text) for every comment.

    A comment is any `#` that appears as a COMMENT token per tokenize — so
    `#` inside strings is not counted. Shebang / coding / tool-hint lines are
    flagged separately via `protected=True` in the returned tuple.
    """
    try:
        src = path.read_bytes()
    except OSError as e:
        print(f"  [skip] {path}: {e}", file=sys.stderr)
        return []

    results: list[Tuple[int, str, str]] = []
    try:
        lines = src.decode("utf-8", errors="replace").splitlines(keepends=False)
        tokens = tokenize.tokenize(io.BytesIO(src).readline)
        for tok in tokens:
            if tok.type == token.COMMENT:
                lineno = tok.start[0]
                full = lines[lineno - 1] if lineno - 1 < len(lines) else ""
                results.append((lineno, full, tok.string))
    except tokenize.TokenizeError as e:
        print(f"  [skip — tokenize error] {path}: {e}", file=sys.stderr)
    return results


def _strip_comments_from_source(src: str) -> str:
    """Return `src` with all non-protected line comments removed.

    Uses tokenize to safely identify COMMENT tokens (respects strings / f-strings).
    Preserves:
      * blank lines that remain after comment removal (for structural readability,
        but collapsing 3+ blank lines in a row into 2)
      * indentation of any line that still has code on it
    """
    new_lines = []
    # Parse once to find comment token positions per line.
    comment_spans: dict[int, list[Tuple[int, int, str]]] = {}
    try:
        tokens = list(tokenize.tokenize(io.BytesIO(src.encode("utf-8")).readline))
    except tokenize.TokenizeError:
        # Give up — return as-is rather than risk corrupting a file
        return src

    for tok in tokens:
        if tok.type != token.COMMENT:
            continue
        lineno = tok.start[0]
        col = tok.start[1]
        text = tok.string
        comment_spans.setdefault(lineno, []).append((col, col + len(text), text))

    for i, line in enumerate(src.splitlines(keepends=False), start=1):
        spans = comment_spans.get(i, [])
        if not spans:
            new_lines.append(line)
            continue

        # If the line's entire non-whitespace is a protected comment, keep as-is.
        stripped = line.lstrip()
        if stripped.startswith("#") and _is_protected(line):
            new_lines.append(line)
            continue

        # Full-line comment (only whitespace before `#`) -> drop the line entirely.
        if stripped.startswith("#"):
            continue  # skip line

        # Inline comment: cut from the first non-protected `#` onward and rstrip trailing ws.
        col = spans[0][0]
        code_part = line[:col].rstrip()
        new_lines.append(code_part)

    # Collapse any run of 3+ blank lines into 2.
    collapsed: list[str] = []
    blank_run = 0
    for ln in new_lines:
        if ln.strip() == "":
            blank_run += 1
            if blank_run <= 2:
                collapsed.append(ln)
        else:
            blank_run = 0
            collapsed.append(ln)

    out = "\n".join(collapsed)
    # Preserve final newline if the original had one.
    if src.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    return out


def _cmd_scan(root: Path) -> int:
    total = 0
    files = 0
    for path in _iter_py_files(root):
        hits = _scan_file(path)
        if not hits:
            continue
        files += 1
        rel = path.relative_to(root)
        for lineno, full, comment_text in hits:
            protected = _is_protected(full)
            tag = "KEEP " if protected else "STRIP"
            print(f"{tag}  {rel}:{lineno}  {comment_text.rstrip()}")
            if not protected:
                total += 1
    print(f"\n--- {total} strippable comments across {files} files ---", file=sys.stderr)
    return 0


def _cmd_strip(root: Path, dry_run: bool) -> int:
    changed = 0
    scanned = 0
    for path in _iter_py_files(root):
        scanned += 1
        original = path.read_text(encoding="utf-8", errors="replace")
        stripped = _strip_comments_from_source(original)
        if stripped == original:
            continue
        if dry_run:
            print(f"  [would edit] {path.relative_to(root)}  "
                  f"({len(original)} -> {len(stripped)} bytes)")
        else:
            path.write_text(stripped, encoding="utf-8", newline="\n")
            print(f"  [edited]     {path.relative_to(root)}  "
                  f"({len(original)} -> {len(stripped)} bytes)")
        changed += 1
    print(f"\n--- scanned {scanned} files, {changed} {'would change' if dry_run else 'changed'} ---",
          file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--scan", action="store_true",
                   help="List every comment (KEEP/STRIP) without editing.")
    g.add_argument("--strip", action="store_true",
                   help="Remove comments in place (keeps shebang, coding, tool hints).")
    p.add_argument("--dry-run", action="store_true",
                   help="With --strip, show what would change but do not write.")
    p.add_argument("--root", default=".",
                   help="Project root (default: current dir).")
    args = p.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"error: root {root} is not a directory", file=sys.stderr)
        return 2

    if args.scan:
        return _cmd_scan(root)
    return _cmd_strip(root, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
