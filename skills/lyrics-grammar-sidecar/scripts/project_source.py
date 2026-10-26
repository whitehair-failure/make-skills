#!/usr/bin/env python3
"""Create the canonical source projection used by every later sidecar stage."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sidecarlib import (
    SidecarError,
    configure_utf8_stdio,
    source_projection_from_lrc,
    write_json_atomic,
)


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Create a canonical LRC source projection (v1)."
    )
    parser.add_argument("lrc", type=Path, help="Local source .lrc file")
    parser.add_argument(
        "--song-id",
        required=True,
        help="SakuraUta songId used by the App's frozen line identity contract",
    )
    parser.add_argument(
        "--require-app-projection",
        action="store_true",
        help=(
            "Write only the App GrammarSourceProjection fields "
            "(songId/lineCount/lrcProjectionHash/lines)"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Projection path (default: <lrc-dir>/<stem>.grammar-work/source-projection.json)",
    )
    parser.add_argument("--replace", action="store_true", help="Replace a differing projection")
    args = parser.parse_args()

    try:
        projection = source_projection_from_lrc(args.lrc, args.song_id)
        output = args.output or (
            args.lrc.resolve().parent
            / f"{args.lrc.stem}.grammar-work"
            / "source-projection.json"
        )
        if args.require_app_projection:
            projection = {
                "songId": projection["songId"],
                "lineCount": projection["lineCount"],
                "lrcProjectionHash": projection["lrcProjectionHash"],
                "lines": projection["lines"],
            }
        changed = write_json_atomic(output, projection, replace=args.replace)
    except SidecarError as exc:
        print(f"ERROR [{exc.code}] {exc.path}: {exc.message}", file=sys.stderr)
        return 2

    action = "written" if changed else "unchanged"
    print(f"{action}: {output}")
    print(f"projectionHash: {projection['lrcProjectionHash']}")
    print(f"lines: {projection['lineCount']}")
    contract_version = projection.get(
        "projectionVersion", "sakurauta-lrc-text-projection-v1"
    )
    print(f"contract: {contract_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
