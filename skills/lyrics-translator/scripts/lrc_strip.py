"""
lrc_strip.py — Strip LRC timestamps to produce a clean plain-text lyrics working file.
Supports single-file, batch-file, default output naming, and stdout printing.
Language-agnostic: applies to all languages and lyric formats.

Usage:
    python lrc_strip.py <input.lrc> [<output.txt>]
    python lrc_strip.py <input1.lrc> <input2.lrc> ...
    python lrc_strip.py --print <input.lrc>

Behavior:
    - Removes all LRC timestamp tokens [MM:SS.mmm] / [MM:SS.mm] from each line.
    - Lines that carry multiple timestamps (e.g. [00:15.80][00:20.10]lyrics)
      have all tokens removed; the lyric text is written once.
    - LRC metadata tags ([ti:], [ar:], [al:], [by:], [offset:]) are safe —
      they do not match the timestamp regex and are preserved as-is.
    - Line order and blank lines are preserved.
    - If <output.txt> is omitted, defaults to "<input.lrc>.tmp.txt".
"""

import os
import re
import sys
from pathlib import Path

if sys.stdout:
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

TIMESTAMP_RE = re.compile(r'\[\d{2}:\d{2}\.\d{2,3}\]')


def strip_text(text: str) -> str:
    lines = text.splitlines()
    stripped_lines = [TIMESTAMP_RE.sub('', line) for line in lines]
    return '\n'.join(stripped_lines)


def strip_file(lrc_path: Path, txt_path: Path) -> None:
    text = lrc_path.read_text(encoding='utf-8', errors='ignore')
    stripped = strip_text(text)
    txt_path.write_text(stripped, encoding='utf-8')


def get_default_tmp_path(lrc_path: Path) -> Path:
    return lrc_path.with_name(f"{lrc_path.name}.tmp.txt")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("Usage: python lrc_strip.py <input.lrc> [<output.txt>]", file=sys.stderr)
        print("       python lrc_strip.py <input1.lrc> <input2.lrc> ...", file=sys.stderr)
        print("       python lrc_strip.py --print <input.lrc>", file=sys.stderr)
        sys.exit(1)

    if args[0] in ('-p', '--print', '--stdout'):
        if len(args) < 2:
            print("Error: --print requires an input file", file=sys.stderr)
            sys.exit(1)
        target = Path(args[1])
        if not target.exists():
            print(f"Error: input file not found: {target}", file=sys.stderr)
            sys.exit(1)
        text = target.read_text(encoding='utf-8', errors='ignore')
        print(strip_text(text))
        return

    # Check if exactly two arguments: <input.lrc> <output.txt>
    if len(args) == 2 and not args[1].lower().endswith('.lrc'):
        lrc_path = Path(args[0])
        txt_path = Path(args[1])
        if not lrc_path.exists():
            print(f"Error: input file not found: {lrc_path}", file=sys.stderr)
            sys.exit(1)
        strip_file(lrc_path, txt_path)
        print(f"Stripped: {lrc_path} -> {txt_path}")
        return

    # Batch or single without output argument:
    success_count = 0
    for arg in args:
        lrc_path = Path(arg)
        if not lrc_path.exists():
            print(f"Warning: input file not found: {lrc_path}", file=sys.stderr)
            continue
        txt_path = get_default_tmp_path(lrc_path)
        strip_file(lrc_path, txt_path)
        print(f"Stripped: {lrc_path.name} -> {txt_path.name}")
        success_count += 1

    if success_count == 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
