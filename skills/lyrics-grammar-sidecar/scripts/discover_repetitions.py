#!/usr/bin/env python3
"""Discover deterministic maximal exact repetition candidates from a v2 draft."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sidecarlib import (
    SidecarError,
    assert_distinct_paths,
    configure_utf8_stdio,
    load_projection,
    read_json,
    write_json_atomic,
)
from v2lib import candidate_groups, discover_repetition_candidates


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Find exact repetitions for Semantic Draft Contract v2."
    )
    parser.add_argument("draft", type=Path, help="semantic-draft.json")
    parser.add_argument(
        "--projection", type=Path, required=True, help="source-projection.json"
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        help="<song>.grammar-work directory (default: draft parent)",
    )
    parser.add_argument(
        "--learner-language",
        help="Optional learner-language validation, e.g. zh-CN.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Candidate path (default: <workdir>/repetition-candidates.json)",
    )
    parser.add_argument("--replace", action="store_true")
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Update repetitionGroups in the semantic draft JSON in-place",
    )
    args = parser.parse_args()

    draft_path = args.draft.expanduser()
    workdir = (args.workdir or draft_path.parent).expanduser().resolve()
    projection_path = args.projection.expanduser()
    output = (args.output or (workdir / "repetition-candidates.json")).expanduser()
    try:
        assert_distinct_paths(
            ("semantic draft", draft_path),
            ("source projection", projection_path),
            ("repetition candidates", output),
        )
        projection = load_projection(projection_path)
        draft = read_json(draft_path)
        candidates = discover_repetition_candidates(
            draft,
            projection,
            learner_language=args.learner_language,
        )
        changed = write_json_atomic(output, candidates, replace=args.replace)
        if args.in_place:
            groups = candidate_groups(candidates)
            draft["repetitionGroups"] = groups
            write_json_atomic(draft_path, draft, replace=True)
            print(f"updated: {draft_path} (repetitionGroups: {len(groups)})")
    except SidecarError as exc:
        print(f"ERROR [{exc.code}] {exc.path}: {exc.message}", file=sys.stderr)
        return 2

    action = "written" if changed else "unchanged"
    print(f"{action}: {output}")
    print(f"candidates: {len(candidates['candidates'])}")
    print(json.dumps(candidates["candidates"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
