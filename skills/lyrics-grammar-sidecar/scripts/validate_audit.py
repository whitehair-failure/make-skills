#!/usr/bin/env python3
"""Validate a semantic-audit report against its frozen candidate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sidecarlib import (
    Issue,
    SidecarError,
    configure_utf8_stdio,
    load_projection,
    read_json,
)
from v2lib import (
    load_frozen_candidate,
    validate_grammar_analysis_v2,
    validate_isolation_proof_v2,
    validate_semantic_audit,
)


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Check audit status, reviewed IDs, candidate binding, and optional proof."
    )
    parser.add_argument("candidate", type=Path, help="analysis-candidate.json")
    parser.add_argument(
        "--candidate-hash",
        type=Path,
        required=True,
        help="analysis-candidate.sha256",
    )
    parser.add_argument("report", type=Path, help="semantic-audit.json")
    parser.add_argument("--learner-language", required=True)
    parser.add_argument("--projection", type=Path)
    parser.add_argument("--isolation", type=Path, help="semantic-audit isolation proof")
    args = parser.parse_args()

    try:
        projection = (
            load_projection(args.projection)
            if args.projection is not None
            else None
        )
        candidate, candidate_hash = load_frozen_candidate(
            args.candidate, args.candidate_hash
        )
        report = read_json(args.report)
        issues = validate_grammar_analysis_v2(
            candidate,
            projection=projection,
            learner_language=args.learner_language,
        )
        issues.extend(
            validate_semantic_audit(
                report, candidate, candidate_hash, args.learner_language
            )
        )
        if args.projection is not None or args.isolation is not None:
            if args.projection is None or args.isolation is None:
                issues.append(
                    Issue(
                        "incomplete_audit_inputs",
                        "$",
                        "Pass both --projection and --isolation, or neither.",
                    )
                )
            else:
                assert projection is not None
                proof = read_json(args.isolation)
                issues.extend(
                    validate_isolation_proof_v2(
                        proof,
                        projection,
                        stage="semantic-audit",
                        artifact_path=args.report,
                    )
                )
    except SidecarError as exc:
        print(f"ERROR [{exc.code}] {exc.path}: {exc.message}", file=sys.stderr)
        return 2

    payload = {
        "ok": not issues,
        "errors": [issue.as_dict() for issue in issues],
        "candidateSha256": candidate_hash,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
