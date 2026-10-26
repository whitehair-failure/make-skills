#!/usr/bin/env python3
"""Record explicit, verifiable isolation evidence before semantic analysis."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sidecarlib import (
    ISOLATION_METHODS,
    SidecarError,
    build_isolation_proof,
    configure_utf8_stdio,
    load_projection,
    pretty_json,
    write_json_atomic,
)


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Record the separate-transcript proof required for formal generation."
    )
    parser.add_argument("projection", type=Path, help="source-projection.json")
    parser.add_argument(
        "--method",
        required=True,
        choices=sorted(ISOLATION_METHODS),
        help="Harness that supplies a separate transcript",
    )
    parser.add_argument(
        "--child-transcript-id",
        required=True,
        help="ID of the fresh grammar-analysis transcript",
    )
    parser.add_argument(
        "--parent-transcript-id",
        help="ID of the invoking transcript, if the harness exposes it",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Destination isolation-proof.json",
    )
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    try:
        projection = load_projection(args.projection)
        proof = build_isolation_proof(
            projection,
            args.method,
            child_transcript_id=args.child_transcript_id,
            parent_transcript_id=args.parent_transcript_id,
        )
        write_json_atomic(args.output, proof, replace=args.replace)
    except SidecarError as exc:
        print(f"ERROR [{exc.code}] {exc.path}: {exc.message}", file=sys.stderr)
        return 2
    print(pretty_json(proof), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
