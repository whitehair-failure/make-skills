#!/usr/bin/env python3
"""Validate a finalized Schema v2 sidecar and its complete audit chain."""

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
    write_json_atomic,
)
from v2lib import validate_finalized_sidecar_v2


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Run the full v2 source, translation, audit, proof, and sidecar check."
    )
    parser.add_argument("lrc", type=Path, help="Original local .lrc file")
    parser.add_argument("sidecar", type=Path, help="Production sidecar JSON")
    parser.add_argument(
        "--projection", type=Path, required=True, help="Frozen source-projection.json"
    )
    parser.add_argument(
        "--workdir", type=Path, required=True, help="<song>.grammar-work directory"
    )
    parser.add_argument("--report", type=Path, help="Optional validation report")
    parser.add_argument("--learner-language", required=True)
    args = parser.parse_args()

    workdir = args.workdir.expanduser().resolve()
    try:
        raw_sidecar_path = args.sidecar.expanduser()
        if raw_sidecar_path.is_symlink():
            raise SidecarError(
                "unsupported_source",
                "Symlinked sidecars are not supported.",
            )
        source_path = args.lrc.expanduser()
        sidecar_path = raw_sidecar_path.resolve()
        projection_path = args.projection.expanduser()
        report_path = args.report.expanduser() if args.report is not None else None
        assert_distinct_paths(
            ("source LRC", source_path),
            ("production sidecar", sidecar_path),
            ("source projection", projection_path),
            ("frozen translation", workdir / "translated.lrc"),
            ("translation manifest", workdir / "translation-manifest.json"),
            ("semantic draft", workdir / "semantic-draft.json"),
            ("analysis candidate", workdir / "analysis-candidate.json"),
            ("candidate hash", workdir / "analysis-candidate.sha256"),
            ("semantic audit", workdir / "semantic-audit.json"),
            ("grammar isolation proof", workdir / "isolation-grammar-analysis.json"),
            ("audit isolation proof", workdir / "isolation-semantic-audit.json"),
            ("validation report", report_path),
        )
        projection = load_projection(projection_path)
        issues = validate_finalized_sidecar_v2(
            source_path,
            sidecar_path,
            projection_path,
            workdir,
            args.learner_language,
        )
        report = {
            "ok": not issues,
            "schemaVersion": 2,
            "errors": [issue.as_dict() for issue in issues],
            "projectionHash": projection["lrcProjectionHash"],
            "sidecar": sidecar_path.name,
        }
        if report_path is not None:
            write_json_atomic(report_path, report, replace=True)
    except (SidecarError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"ERROR [invalid_sidecar] $: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
