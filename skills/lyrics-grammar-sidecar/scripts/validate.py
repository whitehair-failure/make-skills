#!/usr/bin/env python3
"""Offline schema, coverage, Unicode, and cross-reference validation."""

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
    validate_sidecar_document,
    write_json_atomic,
)


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Validate a lyrics grammar sidecar without SakuraUta or network access."
    )
    parser.add_argument("sidecar", type=Path, help="Candidate sidecar JSON")
    parser.add_argument(
        "--projection",
        type=Path,
        help="Frozen source-projection.json for exact source and hash checks",
    )
    parser.add_argument(
        "--expected-lrc-name",
        help="Require source.lrcFile to equal this sibling basename",
    )
    parser.add_argument("--report", type=Path, help="Write a validation-report.json")
    args = parser.parse_args()

    projection = None
    try:
        if args.projection is not None:
            projection = load_projection(args.projection)
        try:
            document = json.loads(args.sidecar.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SidecarError(
                "file_not_found", f"Sidecar not found: {args.sidecar}"
            ) from exc
        except UnicodeDecodeError as exc:
            raise SidecarError("invalid_utf8", "Sidecar is not valid UTF-8.") from exc
        except json.JSONDecodeError as exc:
            raise SidecarError(
                "invalid_json",
                f"Invalid JSON: line {exc.lineno}, column {exc.colno}",
            ) from exc

        issues = validate_sidecar_document(
            document,
            projection=projection,
            expected_lrc_file=(
                args.expected_lrc_name
                or (projection.get("lrcFile") if projection is not None else None)
            ),
        )
        report = validation_report(
            issues,
            projectionHash=(
                projection["lrcProjectionHash"] if projection is not None else None
            ),
            sidecar=str(args.sidecar.name),
        )
        if args.report is not None:
            write_json_atomic(args.report, report, replace=True)
    except SidecarError as exc:
        print(f"ERROR [{exc.code}] {exc.path}: {exc.message}", file=sys.stderr)
        return 2

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
