#!/usr/bin/env python3
"""Create the canonical source projection used by every later sidecar stage."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sidecarlib import (
    SidecarError,
    assert_distinct_paths,
    configure_utf8_stdio,
    source_projection_from_lrc,
    write_json_atomic,
)


def project_single_lrc(
    lrc_path: Path,
    song_id: str | None = None,
    output_path: Path | None = None,
    require_app_projection: bool = False,
    replace: bool = False,
) -> tuple[Path, dict, bool]:
    source_path = lrc_path.expanduser().resolve()
    effective_song_id = song_id or source_path.stem
    projection = source_projection_from_lrc(source_path, effective_song_id)
    output = (
        output_path.expanduser()
        if output_path is not None
        else (
            source_path.parent
            / f"{source_path.stem}.grammar-work"
            / "source-projection.json"
        )
    )
    assert_distinct_paths(
        ("source LRC", source_path),
        ("source projection", output),
    )
    if require_app_projection:
        projection = {
            "songId": projection["songId"],
            "lineCount": projection["lineCount"],
            "lrcProjectionHash": projection["lrcProjectionHash"],
            "lines": projection["lines"],
        }
    changed = write_json_atomic(output, projection, replace=replace)
    return output, projection, changed


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Create the canonical LRC source projection used by Schema v2 (supports single file or batch)."
    )
    parser.add_argument("lrc", type=Path, nargs="+", help="Local source .lrc file(s)")
    parser.add_argument(
        "--song-id",
        help="SakuraUta songId (default: LRC stem; for batch files, each uses its stem)",
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
        help="Projection path (single file only; default: <lrc-dir>/<stem>.grammar-work/source-projection.json)",
    )
    parser.add_argument("--replace", action="store_true", help="Replace a differing projection")
    args = parser.parse_args()

    if len(args.lrc) > 1 and args.output is not None:
        print("ERROR: --output cannot be used when projecting multiple LRC files", file=sys.stderr)
        return 2

    fail_count = 0
    for lrc_file in args.lrc:
        try:
            song_id = args.song_id if len(args.lrc) == 1 else None
            output, projection, changed = project_single_lrc(
                lrc_file,
                song_id=song_id,
                output_path=args.output,
                require_app_projection=args.require_app_projection,
                replace=args.replace,
            )
            action = "written" if changed else "unchanged"
            print(f"{action}: {output}")
            print(f"projectionHash: {projection['lrcProjectionHash']}")
            print(f"lines: {projection['lineCount']}")
            contract_version = projection.get(
                "projectionVersion", "sakurauta-lrc-text-projection-v1"
            )
            print(f"contract: {contract_version}")
        except SidecarError as exc:
            print(f"ERROR [{exc.code}] {exc.path}: {exc.message}", file=sys.stderr)
            fail_count += 1
        except Exception as exc:
            print(f"ERROR: failed on {lrc_file}: {exc}", file=sys.stderr)
            fail_count += 1

    return 0 if fail_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
