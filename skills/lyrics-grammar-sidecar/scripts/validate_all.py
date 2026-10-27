#!/usr/bin/env python3
"""Validate a sidecar against a fresh projection and frozen translation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sidecarlib import (
    SidecarError,
    configure_utf8_stdio,
    load_projection,
    validation_report,
    verify_frozen_translation,
    verify_source_matches_projection,
    validate_sidecar_document,
    write_json_atomic,
)


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Run the full offline sidecar integrity check."
    )
    parser.add_argument("lrc", type=Path, help="Original local .lrc file")
    parser.add_argument("sidecar", type=Path, help="Sidecar JSON to validate")
    parser.add_argument(
        "--projection", type=Path, required=True, help="Frozen source-projection.json"
    )
    parser.add_argument(
        "--workdir", type=Path, required=True, help="<song>.grammar-work directory"
    )
    parser.add_argument(
        "--report", type=Path, help="Optional validation-report.json destination"
    )
    parser.add_argument(
        "--learner-language",
        required=True,
        help="Language for learner-facing prose, e.g. zh-CN, ja, en.",
    )
    args = parser.parse_args()

    try:
        projection = load_projection(args.projection)
        translated = args.workdir / "translated.lrc"
        manifest_path = args.workdir / "translation-manifest.json"
        source_report, source_issues = verify_source_matches_projection(
            projection, args.lrc
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        freeze_report, freeze_issues = verify_frozen_translation(
            projection, translated, manifest
        )
        document = json.loads(args.sidecar.read_text(encoding="utf-8"))
        sidecar_issues = validate_sidecar_document(
            document,
            projection=projection,
            expected_lrc_file=projection.get("lrcFile", args.lrc.name),
            require_projection=True,
            learner_language=args.learner_language,
        )
        issues = [*source_issues, *freeze_issues, *sidecar_issues]
        report = validation_report(
            issues,
            source=source_report,
            translation=freeze_report,
            sidecar=str(args.sidecar.name),
            projectionHash=projection["lrcProjectionHash"],
        )
        if args.report is not None:
            write_json_atomic(args.report, report, replace=True)
    except FileNotFoundError as exc:
        print(f"ERROR [file_not_found] $: {exc}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"ERROR [invalid_json] $: {exc}", file=sys.stderr)
        return 2
    except SidecarError as exc:
        print(f"ERROR [{exc.code}] {exc.path}: {exc.message}", file=sys.stderr)
        return 2

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
