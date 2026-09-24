"""
lrc_strip.py — Strip LRC timestamps to produce a clean plain-text lyrics file.

Usage:
    python lrc_strip.py <input.lrc> <output.txt>

Behavior:
    - Removes all LRC timestamp tokens [MM:SS.mmm] from each line.
    - Lines that carry multiple timestamps (e.g. [00:15.80][00:20.10]lyrics)
      have all tokens removed; the lyric text is written once.
    - LRC metadata tags ([ti:], [ar:], [al:], [by:], [offset:]) are safe —
      they do not match the timestamp regex and are preserved as-is.
    - Line order and blank lines are preserved.
"""

import re
import sys
from pathlib import Path

TIMESTAMP_RE = re.compile(r'\[\d{2}:\d{2}\.\d{2,3}\]')


def strip_lrc(lrc_path: Path, txt_path: Path) -> None:
    text = lrc_path.read_text(encoding='utf-8')
    lines = text.splitlines()

    stripped_lines = []
    for line in lines:
        stripped = TIMESTAMP_RE.sub('', line)
        stripped_lines.append(stripped)

    txt_path.write_text('\n'.join(stripped_lines), encoding='utf-8')


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python lrc_strip.py <input.lrc> <output.txt>", file=sys.stderr)
        sys.exit(1)

    lrc_path = Path(sys.argv[1])
    txt_path = Path(sys.argv[2])

    if not lrc_path.exists():
        print(f"Error: input file not found: {lrc_path}", file=sys.stderr)
        sys.exit(1)

    strip_lrc(lrc_path, txt_path)
    print(f"Stripped: {lrc_path} -> {txt_path}")


if __name__ == '__main__':
    main()
