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


def validate_single_sidecar(
    lrc_path: Path,
    sidecar_path: Path,
    projection_path: Path,
    workdir_path: Path,
    learner_language: str,
    report_path: Path | None = None,
) -> tuple[dict, list]:
    raw_sidecar_path = sidecar_path.expanduser()
    if raw_sidecar_path.is_symlink():
        raise SidecarError(
            "unsupported_source",
            "Symlinked sidecars are not supported.",
        )
    source_path = lrc_path.expanduser()
    resolved_sidecar = raw_sidecar_path.resolve()
    resolved_projection = projection_path.expanduser()
    resolved_workdir = workdir_path.expanduser().resolve()
    resolved_report = report_path.expanduser() if report_path is not None else None
    assert_distinct_paths(
        ("source LRC", source_path),
        ("production sidecar", resolved_sidecar),
        ("source projection", resolved_projection),
        ("frozen translation", resolved_workdir / "translated.lrc"),
        ("translation manifest", resolved_workdir / "translation-manifest.json"),
        ("semantic draft", resolved_workdir / "semantic-draft.json"),
        ("analysis candidate", resolved_workdir / "analysis-candidate.json"),
        ("candidate hash", resolved_workdir / "analysis-candidate.sha256"),
        ("semantic audit", resolved_workdir / "semantic-audit.json"),
        ("grammar isolation proof", resolved_workdir / "isolation-grammar-analysis.json"),
        ("audit isolation proof", resolved_workdir / "isolation-semantic-audit.json"),
        ("validation report", resolved_report),
    )
    projection = load_projection(resolved_projection)
    issues = validate_finalized_sidecar_v2(
        source_path,
        resolved_sidecar,
        resolved_projection,
        resolved_workdir,
        learner_language,
    )
    report = {
        "ok": not issues,
        "schemaVersion": 2,
        "errors": [issue.as_dict() for issue in issues],
        "projectionHash": projection["lrcProjectionHash"],
        "sidecar": resolved_sidecar.name,
    }
    if resolved_report is not None:
        write_json_atomic(resolved_report, report, replace=True)
    return report, issues


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Run the full v2 source, translation, audit, proof, and sidecar check (single or batch)."
    )
    parser.add_argument(
        "files",
        type=Path,
        nargs="+",
        help="Single mode: <lrc> <sidecar>; Batch mode: <lrc1> <lrc2> ...",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Run batch validation for multiple LRC files",
    )
    parser.add_argument(
        "--projection", type=Path, help="Frozen source-projection.json (single mode)"
    )
    parser.add_argument(
        "--workdir", type=Path, help="<song>.grammar-work directory (single mode)"
    )
    parser.add_argument("--report", type=Path, help="Optional validation report (single mode)")
    parser.add_argument("--learner-language", required=True)
    args = parser.parse_args()

    if args.batch:
        fail_count = 0
        summary = []
        for lrc_path in args.files:
            lrc_path = lrc_path.expanduser().resolve()
            sidecar_path = lrc_path.with_suffix(".json")
            workdir = lrc_path.parent / f"{lrc_path.stem}.grammar-work"
            proj_path = workdir / "source-projection.json"
            try:
                report, issues = validate_single_sidecar(
                    lrc_path,
                    sidecar_path,
                    proj_path,
                    workdir,
                    args.learner_language,
                )
                summary.append(report)
                status = "PASS" if not issues else "FAIL"
                print(f"[{status}] {lrc_path.name} -> {sidecar_path.name}")
                if issues:
                    fail_count += 1
            except Exception as exc:
                print(f"[ERROR] {lrc_path.name}: {exc}", file=sys.stderr)
                fail_count += 1
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if fail_count == 0 else 1

    if len(args.files) != 2:
        print("ERROR: single mode requires exactly 2 positional arguments: <lrc> <sidecar>", file=sys.stderr)
        return 2
    if args.projection is None or args.workdir is None:
        print("ERROR: --projection and --workdir are required in single mode", file=sys.stderr)
        return 2

    lrc_path = args.files[0]
    sidecar_path = args.files[1]
    try:
        report, issues = validate_single_sidecar(
            lrc_path,
            sidecar_path,
            args.projection,
            args.workdir,
            args.learner_language,
            report_path=args.report,
        )
    except (SidecarError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"ERROR [invalid_sidecar] $: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
