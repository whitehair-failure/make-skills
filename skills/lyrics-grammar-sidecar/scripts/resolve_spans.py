#!/usr/bin/env python3
"""Resolve and verify Unicode code point spans for Schema v2 drafts and source projections."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sidecarlib import (
    SidecarError,
    configure_utf8_stdio,
    load_projection,
    read_json,
    write_json_atomic,
)


def resolve_line_span(line_text: str, surface: str) -> list[tuple[int, int]]:
    """Return all [start, end) code-point spans where surface occurs in line_text."""
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        pos = line_text.find(surface, start)
        if pos == -1:
            break
        spans.append((pos, pos + len(surface)))
        start = pos + 1
    return spans


def resolve_draft_spans(
    draft: dict[str, Any],
    projection: dict[str, Any],
) -> tuple[int, int, list[str]]:
    """Verify and fix code point spans in draft meaningUnits and phrases.

    Returns (resolved_count, error_count, messages).
    """
    lines_by_id = {line["lineId"]: line["text"] for line in projection.get("lines", [])}

    resolved = 0
    errors = 0
    messages: list[str] = []

    def process_segments(container_name: str, item_id: str, occ_id: str, segments: list[dict[str, Any]]) -> None:
        nonlocal resolved, errors
        for seg_idx, seg in enumerate(segments):
            if not isinstance(seg, dict):
                continue
            line_id = seg.get("lineId")
            if line_id not in lines_by_id:
                messages.append(f"[{item_id}/{occ_id}] seg {seg_idx}: unknown lineId {line_id}")
                errors += 1
                continue
            line_text = lines_by_id[line_id]
            text = seg.get("text")
            if not isinstance(text, str):
                continue

            current_start = seg.get("start")
            current_end = seg.get("end")

            # Check if current span already accurately matches
            if (
                isinstance(current_start, int)
                and isinstance(current_end, int)
                and 0 <= current_start <= current_end <= len(line_text)
                and line_text[current_start:current_end] == text
            ):
                continue

            # Need to resolve
            occurrences = resolve_line_span(line_text, text)
            if not occurrences:
                messages.append(
                    f"[{item_id}/{occ_id}] seg {seg_idx}: text '{text}' not found in line '{line_text}'"
                )
                errors += 1
            elif len(occurrences) == 1:
                start, end = occurrences[0]
                seg["start"] = start
                seg["end"] = end
                resolved += 1
                messages.append(
                    f"[{item_id}/{occ_id}] resolved span [{start}:{end}] for '{text}' on {line_id}"
                )
            else:
                # Multiple occurrences: check if current_start matches one of them
                matching = [span for span in occurrences if span[0] == current_start]
                if matching:
                    seg["start"], seg["end"] = matching[0]
                else:
                    messages.append(
                        f"[{item_id}/{occ_id}] seg {seg_idx}: ambiguous '{text}' appears {len(occurrences)} times in '{line_text}'"
                    )
                    errors += 1

    # Check meaningUnits
    for mu in draft.get("meaningUnits", []):
        mu_id = mu.get("meaningUnitId", "unknown_mu")
        for occ in mu.get("occurrences", []):
            occ_id = occ.get("occurrenceId", "unknown_occ")
            process_segments("meaningUnit", mu_id, occ_id, occ.get("sourceSegments", []))

    # Check phrases
    for phrase in draft.get("phrases", []):
        p_id = phrase.get("phraseId", "unknown_phrase")
        surface = phrase.get("surface")
        for occ in phrase.get("occurrences", []):
            occ_id = occ.get("occurrenceId", "unknown_occ")
            segs = occ.get("sourceSegments", [])
            if len(segs) == 1 and isinstance(surface, str) and not segs[0].get("text"):
                segs[0]["text"] = surface
            process_segments("phrase", p_id, occ_id, segs)

    return resolved, errors, messages


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Calculate and verify Unicode code point spans for Schema v2 drafts."
    )
    parser.add_argument("draft", type=Path, nargs="?", help="Optional path to semantic-draft.json")
    parser.add_argument(
        "--projection", type=Path, required=True, help="source-projection.json"
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Update spans in semantic-draft.json directly",
    )
    parser.add_argument(
        "--line",
        help="Query line by sourceOrder (int) or lineId (str)",
    )
    parser.add_argument(
        "--text",
        help="Query substring to find in the queried line",
    )
    args = parser.parse_args()

    try:
        projection = load_projection(args.projection.expanduser())
    except SidecarError as exc:
        print(f"ERROR [{exc.code}] {exc.path}: {exc.message}", file=sys.stderr)
        return 2

    lines_by_id = {line["lineId"]: line for line in projection.get("lines", [])}
    lines_by_order = {line["sourceOrder"]: line for line in projection.get("lines", [])}

    if args.line is not None:
        target_line = None
        if args.line.isdigit():
            target_line = lines_by_order.get(int(args.line))
        if target_line is None:
            target_line = lines_by_id.get(args.line)
        if target_line is None:
            print(f"ERROR: line '{args.line}' not found in projection", file=sys.stderr)
            return 1

        line_text = target_line["text"]
        print(f"lineId: {target_line['lineId']}")
        print(f"sourceOrder: {target_line['sourceOrder']}")
        print(f"lineText: {line_text}")
        print(f"codePoints: {len(line_text)}")

        if args.text:
            spans = resolve_line_span(line_text, args.text)
            if not spans:
                print(f"ERROR: '{args.text}' not found in line text", file=sys.stderr)
                return 1
            print(f"matches: {len(spans)}")
            for start, end in spans:
                segment = {
                    "lineId": target_line["lineId"],
                    "start": start,
                    "end": end,
                    "text": args.text,
                }
                print(json.dumps(segment, ensure_ascii=False))
        return 0

    if not args.draft:
        print("ERROR: either pass a draft file or use --line to query", file=sys.stderr)
        return 1

    draft_path = args.draft.expanduser()
    try:
        draft = read_json(draft_path)
        resolved, errors, messages = resolve_draft_spans(draft, projection)
        for msg in messages:
            print(msg)
        print(f"Summary: {resolved} span(s) resolved, {errors} error(s)")
        if args.in_place and resolved > 0 and errors == 0:
            write_json_atomic(draft_path, draft, replace=True)
            print(f"updated: {draft_path}")
        return 1 if errors > 0 else 0
    except SidecarError as exc:
        print(f"ERROR [{exc.code}] {exc.path}: {exc.message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
