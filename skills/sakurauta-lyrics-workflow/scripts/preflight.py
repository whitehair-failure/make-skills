#!/usr/bin/env python3
"""Create a fail-closed SakuraUta lyrics workflow work area."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

GENERATOR_SCRIPTS = Path(__file__).resolve().parents[2] / "lyrics-grammar-sidecar" / "scripts"
sys.path.insert(0, str(GENERATOR_SCRIPTS))

from sidecarlib import (  # noqa: E402
    SidecarError,
    configure_utf8_stdio,
    ensure_local_lrc,
    source_projection_from_lrc,
    write_json_atomic,
)

STATUS_SCHEMA = "sakurauta.lyrics-workflow-status"
STATUS_VERSION = 1


def default_workdir(lrc: Path) -> Path:
    return lrc.resolve().parent / f"{lrc.stem}.grammar-work"


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Prepare a SakuraUta lyrics workflow work area.")
    parser.add_argument("lrc", type=Path)
    parser.add_argument("--target-language", required=True)
    parser.add_argument("--song-id")
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--replace-status", action="store_true")
    args = parser.parse_args()

    try:
        lrc = ensure_local_lrc(args.lrc)
        if lrc.is_symlink():
            raise SidecarError("symlink-source", "The source LRC cannot be a symlink.")
        target = args.target_language.strip().lower()
        if not target:
            raise SidecarError("missing_target_language", "A target language is required.")
        song_id = (args.song_id or lrc.stem).strip()
        if not song_id:
            raise SidecarError("missing_song_id", "A non-empty songId is required.")

        workdir = (args.workdir or default_workdir(lrc)).expanduser().resolve()
        if workdir.exists() and workdir.is_symlink():
            raise SidecarError("symlink-workdir", "The work directory cannot be a symlink.")
        if workdir.exists() and not workdir.is_dir():
            raise SidecarError("invalid_workdir", "The work directory is not a directory.")
        workdir.mkdir(parents=True, exist_ok=True)

        projection = source_projection_from_lrc(lrc, song_id)
        projection_path = workdir / "source-projection.json"
        existing_projection = None
        if projection_path.exists():
            existing_projection = json.loads(projection_path.read_text(encoding="utf-8"))
        if existing_projection is not None and existing_projection != projection:
            raise SidecarError(
                "stale_source_projection",
                "The existing projection differs from the current LRC.",
            )
        write_json_atomic(
            projection_path,
            projection,
            replace=projection_path.exists() and args.replace_status,
        )

        status_path = workdir / "workflow-status.json"
        if status_path.exists() and not args.replace_status:
            raise SidecarError(
                "status_exists",
                "workflow-status.json already exists; use --replace-status only for a new run.",
            )
        status = {
            "schema": STATUS_SCHEMA,
            "version": STATUS_VERSION,
            "lrcPath": lrc.name,
            "phase": "prepared",
            "targetLanguage": target,
            "songId": song_id,
            "translationManifest": None,
            "isolationProof": None,
            "lastError": None,
        }
        write_json_atomic(status_path, status, replace=args.replace_status)
    except (SidecarError, OSError, ValueError, json.JSONDecodeError) as exc:
        code = exc.code if isinstance(exc, SidecarError) else "preflight_failed"
        print(f"ERROR [{code}] {exc}", file=sys.stderr)
        return 2

    print(f"prepared: {workdir}")
    print(f"projection: {projection_path}")
    print(f"phase: prepared")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
