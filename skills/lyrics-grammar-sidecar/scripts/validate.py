#!/usr/bin/env python3
"""Offline Schema v2, occurrence, meaning, and reference validation."""

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
    read_json,
    write_json_atomic,
)
from v2lib import validate_grammar_analysis_v2


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Validate a Schema v2 lyrics grammar sidecar without SakuraUta."
    )
    parser.add_argument("sidecar", type=Path, help="Sidecar JSON")
    parser.add_argument(
        "--projection",
        type=Path,
        help="Frozen source-projection.json for exact source and hash checks",
    )
    parser.add_argument(
        "--expected-lrc-name",
        help="Require source.lrcFile to equal this sibling basename",
    )
    parser.add_argument(
        "--learner-language",
        help="Require extensions.learnerLanguage to equal this language.",
    )
    parser.add_argument("--report", type=Path, help="Write a validation report")
    args = parser.parse_args()

    projection = None
    try:
        raw_sidecar_path = args.sidecar.expanduser()
        if raw_sidecar_path.is_symlink():
            raise SidecarError(
                "unsupported_source",
                "Symlinked sidecars are not supported.",
            )
        sidecar_path = raw_sidecar_path.resolve()
        projection_path = (
            args.projection.expanduser()
            if args.projection is not None
            else None
        )
        report_path = args.report.expanduser() if args.report is not None else None
        assert_distinct_paths(
            ("sidecar", sidecar_path),
            ("source projection", projection_path),
            ("validation report", report_path),
        )
        if args.projection is not None:
            assert projection_path is not None
            projection = load_projection(projection_path)
        document = read_json(sidecar_path)
        issues = validate_grammar_analysis_v2(
            document,
            projection=projection,
            expected_lrc_file=(
                args.expected_lrc_name
                or (projection.get("lrcFile") if projection is not None else None)
            ),
            learner_language=args.learner_language,
        )
        report = {
            "ok": not issues,
            "schemaVersion": 2,
            "errors": [issue.as_dict() for issue in issues],
            "projectionHash": (
                projection["lrcProjectionHash"] if projection is not None else None
            ),
            "sidecar": sidecar_path.name,
        }
        if report_path is not None:
            write_json_atomic(report_path, report, replace=True)
    except SidecarError as exc:
        print(f"ERROR [{exc.code}] {exc.path}: {exc.message}", file=sys.stderr)
        return 2

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
