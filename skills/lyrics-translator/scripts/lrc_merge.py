"""
lrc_merge.py — Merge translations from a bilingual plain-text file back into
               the original LRC file, then remove the temp text file.

Usage:
    python lrc_merge.py <original.lrc> <translated.tmp.txt>

Input translated.tmp.txt format (produced by the translation agent):
    - Metadata / title lines: left untouched (no translation inserted below them)
    - Lyric lines: alternating  source_line / translation_line  pairs

Behavior:
    1. Build `original_texts`: set of all lyric texts in the original .lrc
       (timestamps stripped).
    2. Parse `translated.tmp.txt` to build translation_map:
           { source_text: translation_text }
       A line is a "source" if it appears in original_texts; the immediately
       following non-empty, non-source line is its translation.
    3. Walk the original .lrc line by line and output:
       - Non-lyric lines (blank, pure SFX ♪/♫, metadata tags) → unchanged
       - Lyric lines → original line first, then insert [timestamp]translation
         for each timestamp token found on that line (handles multi-timestamp
         lines by repeating the translation once per timestamp).
    4. Overwrite the original .lrc in place with the bilingual result.
    5. Delete the temp .txt file.
"""

import re
import sys
from pathlib import Path

TIMESTAMP_RE = re.compile(r'\[\d{2}:\d{2}\.\d{2,3}\]')


def _strip_timestamps(line: str) -> str:
    return TIMESTAMP_RE.sub('', line).rstrip()


def _is_metadata_tag(text: str) -> bool:
    """True for LRC header tags like [ti:...], [ar:...], [al:...] etc."""
    return bool(re.match(r'^\[(ti|ar|al|by|offset):', text, re.IGNORECASE))


def build_original_set(lrc_path: Path) -> set[str]:
    """Return the set of text content for every line in the .lrc file."""
    texts: set[str] = set()
    for line in lrc_path.read_text(encoding='utf-8').splitlines():
        text = _strip_timestamps(line)
        if text:  # skip truly blank lines
            texts.add(text)
    return texts


def build_translation_map(txt_path: Path, original_texts: set[str]) -> dict[str, str]:
    """
    Parse the bilingual .txt to map { source_text -> translation_text }.

    Rules:
    - A line whose stripped content is in original_texts is a source line.
    - The very next non-empty, non-original line is its translation.
    - If two consecutive source lines appear (filler / untranslated pass-through),
      no translation is recorded for the first.
    """
    translation_map: dict[str, str] = {}
    lines = txt_path.read_text(encoding='utf-8').splitlines()

    pending_source: str | None = None
    for line in lines:
        text = line.rstrip()
        if not text:
            # blank line clears the pending source (no translation for it)
            pending_source = None
            continue

        if text in original_texts:
            # This is a source line — record it as pending
            pending_source = text
        elif pending_source is not None:
            # This is the translation for the pending source
            translation_map[pending_source] = text
            pending_source = None
        else:
            # Unexpected non-source line without a preceding source; skip
            pending_source = None

    return translation_map


def merge(lrc_path: Path, txt_path: Path) -> None:
    original_texts = build_original_set(lrc_path)
    translation_map = build_translation_map(txt_path, original_texts)

    lrc_lines = lrc_path.read_text(encoding='utf-8').splitlines()
    output: list[str] = []

    for line in lrc_lines:
        output.append(line.rstrip())

        timestamps = TIMESTAMP_RE.findall(line)
        if not timestamps:
            continue  # metadata tag or blank — no translation to insert

        text = _strip_timestamps(line)
        if not text:
            continue  # blank timestamp line

        # Check for metadata-like content that should never be translated
        if _is_metadata_tag(line):
            continue

        translation = translation_map.get(text)
        if translation:
            for ts in timestamps:
                output.append(f"{ts}{translation}")

    lrc_path.write_text('\n'.join(output), encoding='utf-8')
    txt_path.unlink()
    print(f"Merged: {lrc_path} (temp file deleted)")


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python lrc_merge.py <original.lrc> <translated.tmp.txt>",
              file=sys.stderr)
        sys.exit(1)

    lrc_path = Path(sys.argv[1])
    txt_path = Path(sys.argv[2])

    if not lrc_path.exists():
        print(f"Error: LRC file not found: {lrc_path}", file=sys.stderr)
        sys.exit(1)
    if not txt_path.exists():
        print(f"Error: translated text file not found: {txt_path}", file=sys.stderr)
        sys.exit(1)

    merge(lrc_path, txt_path)


if __name__ == '__main__':
    main()
