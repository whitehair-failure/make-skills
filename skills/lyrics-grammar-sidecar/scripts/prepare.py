#!/usr/bin/env python3
"""Freeze a semantic draft into a normalized, auditable v2 candidate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sidecarlib import SidecarError, configure_utf8_stdio
from v2lib import prepare_semantic_candidate


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description=(
            "Validate a v2 semantic draft, exact repetition completion, frozen "
            "translation, and source projection; write the candidate and hash."
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
        "--candidate",
        type=Path,
        help="Candidate output (default: <workdir>/analysis-candidate.json)",
    )
    parser.add_argument(
        "--candidate-hash",
        type=Path,
        help="Hash output (default: <workdir>/analysis-candidate.sha256)",
    )
    parser.add_argument(
        "--learner-language",
        required=True,
        help="Target learner language, e.g. zh-CN.",
    )
    args = parser.parse_args()

    workdir = args.workdir.expanduser().resolve()
    candidate_output = (
        args.candidate or (workdir / "analysis-candidate.json")
    ).expanduser().resolve()
    try:
        candidate, report = prepare_semantic_candidate(
            args.lrc,
            args.projection,
            workdir,
            args.draft or (workdir / "semantic-draft.json"),
            learner_language=args.learner_language,
            candidate_path=args.candidate,
            candidate_hash_path=args.candidate_hash,
        )
    except SidecarError as exc:
        print(f"ERROR [{exc.code}] {exc.path}: {exc.message}", file=sys.stderr)
        print(f"validationReport: {workdir / 'validation-report.json'}", file=sys.stderr)
        return 2

    print(f"written: {candidate_output}")
    print(f"candidateHash: {report['candidateHash']}")
    print(f"repetitionGroups: {len(candidate['repetitionGroups'])}")
    print(f"validationReport: {workdir / 'validation-report.json'}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
