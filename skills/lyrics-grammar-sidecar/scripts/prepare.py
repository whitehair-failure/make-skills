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
            "translation, and source projection; write the candidate and hash (single or batch)."
        )
    )
    parser.add_argument("lrc", type=Path, nargs="+", help="Original local .lrc file(s)")
    parser.add_argument(
        "--projection", type=Path, help="source-projection.json (single file only)"
    )
    parser.add_argument(
        "--workdir", type=Path, help="<song>.grammar-work directory (single file only)"
    )
    parser.add_argument(
        "--draft",
        type=Path,
        help="semantic-draft.json (single file only; default: <workdir>/semantic-draft.json)",
    )
    parser.add_argument(
        "--candidate",
        type=Path,
        help="Candidate output (single file only; default: <workdir>/analysis-candidate.json)",
    )
    parser.add_argument(
        "--candidate-hash",
        type=Path,
        help="Hash output (single file only; default: <workdir>/analysis-candidate.sha256)",
    )
    parser.add_argument(
        "--learner-language",
        required=True,
        help="Target learner language, e.g. zh-CN.",
    )
    args = parser.parse_args()

    fail_count = 0
    for lrc_path in args.lrc:
        lrc_path = lrc_path.expanduser().resolve()
        workdir = (
            args.workdir.expanduser().resolve()
            if (len(args.lrc) == 1 and args.workdir is not None)
            else (lrc_path.parent / f"{lrc_path.stem}.grammar-work").resolve()
        )
        projection_path = (
            args.projection.expanduser().resolve()
            if (len(args.lrc) == 1 and args.projection is not None)
            else (workdir / "source-projection.json")
        )
        draft_path = (
            args.draft
            if (len(args.lrc) == 1 and args.draft is not None)
            else (workdir / "semantic-draft.json")
        )
        candidate_path = (
            args.candidate
            if (len(args.lrc) == 1 and args.candidate is not None)
            else (workdir / "analysis-candidate.json")
        )
        candidate_hash_path = (
            args.candidate_hash
            if (len(args.lrc) == 1 and args.candidate_hash is not None)
            else (workdir / "analysis-candidate.sha256")
        )

        try:
            candidate, report = prepare_semantic_candidate(
                lrc_path,
                projection_path,
                workdir,
                draft_path,
                learner_language=args.learner_language,
                candidate_path=candidate_path,
                candidate_hash_path=candidate_hash_path,
            )
            print(f"written: {candidate_path}")
            print(f"candidateHash: {report['candidateHash']}")
            print(f"repetitionGroups: {len(candidate['repetitionGroups'])}")
            print(f"validationReport: {workdir / 'validation-report.json'}")
            if len(args.lrc) == 1:
                print(json.dumps(report, ensure_ascii=False, indent=2))
        except SidecarError as exc:
            print(f"ERROR [{exc.code}] {exc.path}: {exc.message}", file=sys.stderr)
            print(f"validationReport: {workdir / 'validation-report.json'}", file=sys.stderr)
            fail_count += 1
        except Exception as exc:
            print(f"ERROR: failed on {lrc_path.name}: {exc}", file=sys.stderr)
            fail_count += 1

    return 0 if fail_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
