#!/usr/bin/env python3
"""Deterministic finalizer: validate a semantic draft, then write the sidecar."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sidecarlib import (
    SidecarError,
    build_validation_report,
    configure_utf8_stdio,
    finalize_sidecar,
    load_projection,
    verify_frozen_translation,
    verify_source_matches_projection,
    write_json_atomic,
)


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description=(
            "Validate frozen inputs and a semantic draft, then atomically create "
            "<song>.json beside the source LRC."
        )
    )
    parser.add_argument("lrc", type=Path, help="Original local .lrc file")
    parser.add_argument(
        "--projection", type=Path, required=True, help="source-projection.json"
    )
    parser.add_argument(
        "--workdir", type=Path, required=True, help="<song>.grammar-work directory"
    )
    parser.add_argument(
        "--draft",
        type=Path,
        help="semantic-draft.json (default: <workdir>/semantic-draft.json)",
    )
    parser.add_argument(
        "--isolation",
        type=Path,
        help=(
            "isolation-proof.json from the separate analysis transcript "
            "(default: <workdir>/isolation-proof.json)"
        ),
    )
    parser.add_argument(
        "--replace", action="store_true", help="Replace an existing differing sidecar"
    )
    args = parser.parse_args()

    workdir = args.workdir.expanduser().resolve()
    draft = (args.draft or (workdir / "semantic-draft.json")).expanduser().resolve()
    isolation = (
        args.isolation or (workdir / "isolation-proof.json")
    ).expanduser().resolve()
    report_path = workdir / "validation-report.json"

    try:
        projection = load_projection(args.projection)
        manifest = workdir / "translation-manifest.json"
        translated = workdir / "translated.lrc"
        if not manifest.is_file():
            raise SidecarError("file_not_found", f"Manifest not found: {manifest}")
        if not translated.is_file():
            raise SidecarError("file_not_found", f"Frozen translation not found: {translated}")

        _, source_issues = verify_source_matches_projection(projection, args.lrc)
        if source_issues:
            write_json_atomic(
                report_path,
                build_validation_report(source_issues, projection, draft),
                replace=True,
            )
            raise SidecarError(
                source_issues[0].code,
                source_issues[0].message,
                source_issues[0].path,
            )

        manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
        _, freeze_issues = verify_frozen_translation(
            projection, translated, manifest_data
        )
        if freeze_issues:
            write_json_atomic(
                report_path,
                build_validation_report(freeze_issues, projection, draft),
                replace=True,
            )
            raise SidecarError(
                freeze_issues[0].code,
                freeze_issues[0].message,
                freeze_issues[0].path,
            )

        output, changed = finalize_sidecar(
            args.lrc,
            args.projection,
            translated,
            manifest,
            draft,
            isolation_path=isolation,
            workdir=workdir,
            report_path=report_path,
            replace=args.replace,
        )
    except SidecarError as exc:
        print(f"ERROR [{exc.code}] {exc.path}: {exc.message}", file=sys.stderr)
        print(f"validationReport: {report_path}", file=sys.stderr)
        return 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR [invalid_json] $: {exc}", file=sys.stderr)
        return 2

    action = "written" if changed else "unchanged"
    print(f"{action}: {output}")
    print(f"validationReport: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
