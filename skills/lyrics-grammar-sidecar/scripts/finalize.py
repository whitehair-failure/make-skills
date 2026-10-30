#!/usr/bin/env python3
"""Verify the complete v2 audit chain and atomically write the sidecar."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sidecarlib import SidecarError, configure_utf8_stdio
from v2lib import finalize_sidecar_v2


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description=(
            "Require a frozen v2 candidate, passing independent semantic audit, "
            "stage-bound proofs, and an unchanged hash chain before writing <song>.json."
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
        help="analysis-candidate.json (default: <workdir>/analysis-candidate.json)",
    )
    parser.add_argument(
        "--candidate-hash",
        type=Path,
        help="analysis-candidate.sha256 (default: <workdir>/analysis-candidate.sha256)",
    )
    parser.add_argument(
        "--audit",
        type=Path,
        help="semantic-audit.json (default: <workdir>/semantic-audit.json)",
    )
    parser.add_argument(
        "--grammar-isolation",
        type=Path,
        help=(
            "Grammar child proof "
            "(default: <workdir>/isolation-grammar-analysis.json)"
        ),
    )
    parser.add_argument(
        "--audit-isolation",
        type=Path,
        help=(
            "Audit child proof "
            "(default: <workdir>/isolation-semantic-audit.json)"
        ),
    )
    parser.add_argument(
        "--learner-language",
        required=True,
        help="Language for learner-facing prose, e.g. zh-CN.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing differing production JSON.",
    )
    args = parser.parse_args()

    workdir = args.workdir.expanduser().resolve()
    try:
        output, changed = finalize_sidecar_v2(
            args.lrc,
            args.projection,
            workdir,
            learner_language=args.learner_language,
            draft_path=args.draft,
            candidate_path=args.candidate,
            candidate_hash_path=args.candidate_hash,
            audit_path=args.audit,
            grammar_proof_path=args.grammar_isolation,
            audit_proof_path=args.audit_isolation,
            replace=args.replace,
        )
    except SidecarError as exc:
        print(f"ERROR [{exc.code}] {exc.path}: {exc.message}", file=sys.stderr)
        print(f"finalizeReport: {workdir / 'finalize-report.json'}", file=sys.stderr)
        return 2
    action = "written" if changed else "unchanged"
    print(f"{action}: {output}")
    print(f"finalizeReport: {workdir / 'finalize-report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
