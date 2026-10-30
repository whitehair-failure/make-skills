#!/usr/bin/env python3
"""Record a stage-bound v2 child transcript isolation proof."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sidecarlib import (
    ISOLATION_METHODS,
    ISOLATION_STAGES,
    SidecarError,
    assert_distinct_paths,
    configure_utf8_stdio,
    load_projection,
    pretty_json,
    write_json_atomic,
)
from v2lib import build_isolation_proof_v2


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Record the separate-transcript proof required for a v2 stage."
    )
    parser.add_argument("projection", type=Path, help="source-projection.json")
    parser.add_argument(
        "--stage",
        required=True,
        choices=sorted(ISOLATION_STAGES),
        help="Grammar-analysis or semantic-audit stage",
    )
    parser.add_argument(
        "--artifact",
        type=Path,
        required=True,
        help="Artifact produced by the child (draft for grammar, audit report for audit)",
    )
    parser.add_argument(
        "--method",
        required=True,
        choices=sorted(ISOLATION_METHODS),
        help="Harness that supplies a separate transcript",
    )
    parser.add_argument(
        "--child-transcript-id",
        required=True,
        help="ID of the fresh child transcript",
    )
    parser.add_argument(
        "--parent-transcript-id",
        help="ID of the invoking transcript, if the harness exposes it",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Destination isolation proof JSON",
    )
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    try:
        projection_path = args.projection.expanduser()
        artifact_path = args.artifact.expanduser()
        output_path = args.output.expanduser()
        assert_distinct_paths(
            ("source projection", projection_path),
            ("child artifact", artifact_path),
            ("isolation proof", output_path),
        )
        projection = load_projection(projection_path)
        proof = build_isolation_proof_v2(
            projection,
            args.stage,
            artifact_path,
            args.method,
            child_transcript_id=args.child_transcript_id,
            parent_transcript_id=args.parent_transcript_id,
        )
        write_json_atomic(output_path, proof, replace=args.replace)
    except SidecarError as exc:
        print(f"ERROR [{exc.code}] {exc.path}: {exc.message}", file=sys.stderr)
        return 2
    print(pretty_json(proof), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
