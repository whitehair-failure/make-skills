#!/usr/bin/env python3
"""Verify workflow phase prerequisites and persist only a deterministic phase marker."""

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
    load_projection,
    read_json,
    sha256_file,
    validate_isolation_proof,
    validate_sidecar_document,
    verify_frozen_translation,
    verify_source_matches_projection,
    write_json_atomic,
)

PHASES = {"prepared", "translated", "frozen", "analyzing", "drafted", "validated", "finalized"}


def load_status(workdir: Path) -> dict:
    path = workdir / "workflow-status.json"
    if not path.is_file():
        raise SidecarError("missing_status", "workflow-status.json is missing.")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "sakurauta.lyrics-workflow-status" or value.get("version") != 1:
        raise SidecarError("invalid_status", "Unsupported workflow status contract.")
    return value


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Check and advance a SakuraUta workflow phase.")
    parser.add_argument("workdir", type=Path)
    parser.add_argument("--phase", required=True, choices=sorted(PHASES))
    parser.add_argument("--sidecar", type=Path)
    args = parser.parse_args()

    workdir = args.workdir.expanduser().resolve()
    try:
        status = load_status(workdir)
        projection = load_projection(workdir / "source-projection.json")
        lrc_name = status.get("lrcPath")
        if not isinstance(lrc_name, str) or not lrc_name:
            raise SidecarError("invalid_status", "lrcPath is missing.")
        lrc = workdir.parent / lrc_name
        if not lrc.is_file():
            raise SidecarError("file_not_found", f"Source LRC not found beside workdir: {lrc}")

        source_report, source_issues = verify_source_matches_projection(projection, lrc)
        if source_issues:
            raise SidecarError(source_issues[0].code, source_issues[0].message)

        manifest_path = workdir / "translation-manifest.json"
        translated_path = workdir / "translated.lrc"
        if args.phase in {"frozen", "analyzing", "drafted", "validated", "finalized"}:
            if not manifest_path.is_file() or not translated_path.is_file():
                raise SidecarError("missing_frozen_translation", "Frozen translation is incomplete.")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            freeze_report, freeze_issues = verify_frozen_translation(
                projection, translated_path, manifest
            )
            if freeze_issues:
                raise SidecarError(freeze_issues[0].code, freeze_issues[0].message)
        if args.phase in {"drafted", "validated", "finalized"}:
            draft_path = workdir / "semantic-draft.json"
            isolation_path = workdir / "isolation-proof.json"
            if not draft_path.is_file():
                raise SidecarError("missing_draft", "semantic-draft.json is missing.")
            if not isolation_path.is_file():
                raise SidecarError("missing_isolation_proof", "isolation-proof.json is missing.")
            isolation_issues = validate_isolation_proof(read_json(isolation_path), projection)
            if isolation_issues:
                raise SidecarError(
                    isolation_issues[0].code,
                    isolation_issues[0].message,
                    isolation_issues[0].path,
                )
            draft = read_json(draft_path)
            draft_issues = validate_sidecar_document(
                draft,
                projection=projection,
                expected_lrc_file=projection.get("lrcFile"),
            )
            if draft_issues:
                raise SidecarError(
                    draft_issues[0].code,
                    draft_issues[0].message,
                    draft_issues[0].path,
                )

        if args.phase == "finalized":
            if args.sidecar is None or not args.sidecar.is_file():
                raise SidecarError("missing_sidecar", "Final sidecar is missing.")
            output = args.sidecar.expanduser().resolve()
            expected = lrc.with_suffix(".json").resolve()
            if output != expected:
                raise SidecarError("wrong_sidecar_path", "Sidecar must be beside the source LRC.")

        status["phase"] = args.phase
        status["translationManifest"] = manifest_path.name
        status["lastError"] = None
        write_json_atomic(workdir / "workflow-status.json", status, replace=True)
    except (SidecarError, OSError, ValueError, json.JSONDecodeError) as exc:
        code = exc.code if isinstance(exc, SidecarError) else "phase_check_failed"
        print(f"ERROR [{code}] {exc}", file=sys.stderr)
        return 2

    print(f"phase: {args.phase}")
    if args.sidecar:
        print(f"sidecar: {args.sidecar.resolve()}")
    print(f"translationSha256: {sha256_file(translated_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
