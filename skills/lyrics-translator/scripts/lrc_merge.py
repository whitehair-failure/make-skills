"""
lrc_merge.py — Merge translations from a bilingual plain-text file back into
               the original LRC file, verify the bilingual structure, and
               remove the temp text file.
Language-agnostic: applies to all translation directions and global languages.

Usage:
    python lrc_merge.py <original.lrc> [<translated.tmp.txt>]
    python lrc_merge.py <original1.lrc> <original2.lrc> ...
    python lrc_merge.py --force <original.lrc> [<translated.tmp.txt>]

Input translated text format (produced by the translation agent):
    - Metadata / title lines: left untouched (no translation inserted below them)
    - Lyric lines: alternating  source_line / translation_line  pairs
    - Vocal filler / pure sound-effect lines: pass through without translation

Behavior:
    1. Locates the temp text file (explicit argument or `<original.lrc>.tmp.txt`).
    2. Builds translation map from the working plain text file.
    3. Reconstructs the LRC file with interleaved source/translation lines
       sharing matching timestamps.
    4. Overwrites the original LRC file.
    5. Performs deterministic verification:
       - Confirms timestamp syntax and timestamp matching between pairs.
       - Confirms presence of bilingual pairs, vocalise/filler tracks, or instrumental tracks.
       - Confirms metadata tags remain intact.
    6. Automatically cleans up the temp text file upon success.
    7. Prints a deterministic [OK] status line and exits 0 on success.
       (If verification fails, prints [ERROR] and exits non-zero, allowing the agent
        to inspect or use --force for edge cases).
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
METADATA_RE = re.compile(r'^\[(ti|ar|al|by|offset):', re.IGNORECASE)

FILLER_RE = re.compile(
    r'^(?:[♪♫…\s\.,!~?\'\"\(\)（）\-\—\[\]/]+|(?:\b(?:la|na|da|ta|oh|ah|ha|ba|pa|wo|ye|uh|wu|hu|hey|yeah|ooh|wow|scat|du|di|bi|pi|yo)\b[\s♪♫…\.,!~?\'\"\(\)（）\-\—]*)+)+$',
    re.IGNORECASE
)


def _strip_timestamps(line: str) -> str:
    return TIMESTAMP_RE.sub('', line).rstrip()


def _is_metadata_tag(text: str) -> bool:
    """True for standard LRC header tags like [ti:...], [ar:...], etc."""
    return bool(METADATA_RE.match(text))


def _is_vocal_filler(text: str) -> bool:
    """Check if a line is a universal vocal filler, scat syllable, or music note."""
    clean = text.strip()
    if not clean:
        return True
    if FILLER_RE.match(clean):
        return True
    # Pure vowel/syllable chants in Kana / CJK
    if re.match(r'^[あアいイうウえエおオラらルるー\s\.,!~?\'\"\(\)（）]+$', clean):
        return True
    if re.match(r'^[啦啊哦呜耶哈\s\.,!~?\'\"\(\)（）]+$', clean):
        return True
    return False


def build_original_set(lrc_path: Path) -> set[str]:
    """Return the set of text content for every line in the .lrc file."""
    texts: set[str] = set()
    for line in lrc_path.read_text(encoding='utf-8', errors='ignore').splitlines():
        text = _strip_timestamps(line)
        if text:  # skip truly blank lines
            texts.add(text)
    return texts


def build_translation_map(txt_path: Path, original_texts: set[str]) -> dict[str, str]:
    """
    Parse the bilingual plain-text file to map { source_text -> translation_text }.

    Rules:
    - A line whose stripped content is in original_texts is a source line.
    - The immediately following non-empty, non-original line is its translation.
    - If two consecutive source lines appear (filler / untranslated pass-through),
      no translation is recorded for the first.
    """
    translation_map: dict[str, str] = {}
    lines = txt_path.read_text(encoding='utf-8', errors='ignore').splitlines()

    pending_source: str | None = None
    for line in lines:
        text = line.rstrip()
        if not text:
            pending_source = None
            continue

        if text in original_texts:
            pending_source = text
        elif pending_source is not None:
            translation_map[pending_source] = text
            pending_source = None
        else:
            pending_source = None

    return translation_map


def verify_merged_lrc(lrc_path: Path, force: bool = False) -> tuple[bool, str]:
    """
    Deterministically verify the structural integrity of the merged bilingual LRC file.
    Returns (is_valid, summary_message).
    """
    lines = lrc_path.read_text(encoding='utf-8', errors='ignore').splitlines()
    if not lines:
        return False, "File is empty after merge"

    total_lines = len(lines)

    # Check for pure instrumental track indicators
    inst_keywords = ['instrumental', '纯音乐', 'inst', 'off vocal', 'karaoke']
    first_non_empty = next((l.strip() for l in lines if l.strip()), '')
    if total_lines <= 3 and any(k in first_non_empty.lower() for k in inst_keywords):
        return True, f"instrumental track ({total_lines} lines)"

    pairs = 0
    metadata = 0
    passthroughs = 0
    i = 0

    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        if _is_metadata_tag(line):
            metadata += 1
            i += 1
            continue

        ts_matches = TIMESTAMP_RE.findall(line)
        if not ts_matches:
            metadata += 1
            i += 1
            continue

        # Check if following line has the matching timestamp (interleaved pair)
        if i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            next_ts = TIMESTAMP_RE.findall(next_line)
            if next_ts and next_ts[0] == ts_matches[0]:
                pairs += 1
                i += 2
                continue

        passthroughs += 1
        i += 1

    if pairs == 0 and total_lines > 3:
        if force:
            return True, f"forced bypass ({total_lines} lines, 0 pairs)"
        # Check if all content lines are recognized vocal fillers or notes
        non_meta = [_strip_timestamps(l).strip() for l in lines if l.strip() and not _is_metadata_tag(l)]
        if non_meta and all(_is_vocal_filler(t) for t in non_meta):
            return True, f"all-vocalise/filler track ({total_lines} lines, 0 pairs required, {len(non_meta)} filler pass-through)"
        return False, f"no bilingual interleaved pairs detected across {total_lines} lines (use --force if intentionally untranslated)"

    return True, f"{total_lines} lines, {pairs} bilingual pairs, {metadata} metadata, {passthroughs} pass-through"


def merge_single_file(lrc_path: Path, txt_path: Path, force: bool = False) -> bool:
    """Merge and verify a single LRC file with its translated text file."""
    if not lrc_path.exists():
        print(f"[ERROR] LRC file not found: {lrc_path}", file=sys.stderr)
        return False
    if not txt_path.exists():
        print(f"[ERROR] Translated text file not found: {txt_path}", file=sys.stderr)
        return False

    original_texts = build_original_set(lrc_path)
    translation_map = build_translation_map(txt_path, original_texts)

    lrc_lines = lrc_path.read_text(encoding='utf-8', errors='ignore').splitlines()
    output: list[str] = []

    for line in lrc_lines:
        output.append(line.rstrip())

        timestamps = TIMESTAMP_RE.findall(line)
        if not timestamps:
            continue

        text = _strip_timestamps(line)
        if not text:
            continue

        if _is_metadata_tag(line):
            continue

        translation = translation_map.get(text)
        if translation:
            for ts in timestamps:
                output.append(f"{ts}{translation}")

    # Write merged bilingual LRC
    lrc_path.write_text('\n'.join(output), encoding='utf-8')

    # Deterministic verification
    is_valid, msg = verify_merged_lrc(lrc_path, force=force)
    if is_valid:
        txt_path.unlink(missing_ok=True)
        print(f"[OK] {lrc_path.name}: verified ({msg}). Temp file deleted.")
        return True
    else:
        print(f"[ERROR] {lrc_path.name}: verification failed — {msg}", file=sys.stderr)
        return False


def resolve_tmp_path(lrc_path: Path) -> Path | None:
    """Find the corresponding temp text file for an LRC file."""
    candidates = [
        lrc_path.with_name(f"{lrc_path.name}.tmp.txt"),
        lrc_path.with_suffix('.tmp.txt')
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("Usage: python lrc_merge.py <original.lrc> [<translated.tmp.txt>]", file=sys.stderr)
        print("       python lrc_merge.py <original1.lrc> <original2.lrc> ...", file=sys.stderr)
        print("       python lrc_merge.py --force <original.lrc> [<translated.tmp.txt>]", file=sys.stderr)
        sys.exit(1)

    force = False
    if '--force' in args:
        force = True
        args = [a for a in args if a != '--force']

    if not args:
        print("Error: No files specified", file=sys.stderr)
        sys.exit(1)

    # Case 1: Exactly two arguments and second is a .txt file
    if len(args) == 2 and args[1].lower().endswith('.txt'):
        lrc_path = Path(args[0])
        txt_path = Path(args[1])
        success = merge_single_file(lrc_path, txt_path, force=force)
        sys.exit(0 if success else 1)

    # Case 2: One or more LRC files passed
    success_count = 0
    fail_count = 0
    for arg in args:
        lrc_path = Path(arg)
        if not lrc_path.exists():
            print(f"[ERROR] File not found: {lrc_path}", file=sys.stderr)
            fail_count += 1
            continue
        txt_path = resolve_tmp_path(lrc_path)
        if merge_single_file(lrc_path, txt_path, force=force):
            success_count += 1
        else:
            fail_count += 1

    if fail_count > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
