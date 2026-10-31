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
            "stage-bound proofs, and an unchanged hash chain before writing <song>.json (single or batch)."
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
        help="analysis-candidate.json (single file only; default: <workdir>/analysis-candidate.json)",
    )
    parser.add_argument(
        "--candidate-hash",
        type=Path,
        help="analysis-candidate.sha256 (single file only; default: <workdir>/analysis-candidate.sha256)",
    )
    parser.add_argument(
        "--audit",
        type=Path,
        help="semantic-audit.json (single file only; default: <workdir>/semantic-audit.json)",
    )
    parser.add_argument(
        "--grammar-isolation",
        type=Path,
        help=(
            "Grammar child proof "
            "(single file only; default: <workdir>/isolation-grammar-analysis.json)"
        ),
    )
    parser.add_argument(
        "--audit-isolation",
        type=Path,
        help=(
            "Audit child proof "
            "(single file only; default: <workdir>/isolation-semantic-audit.json)"
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

        try:
            output, changed = finalize_sidecar_v2(
                lrc_path,
                projection_path,
                workdir,
                learner_language=args.learner_language,
                draft_path=args.draft if len(args.lrc) == 1 else None,
                candidate_path=args.candidate if len(args.lrc) == 1 else None,
                candidate_hash_path=args.candidate_hash if len(args.lrc) == 1 else None,
                audit_path=args.audit if len(args.lrc) == 1 else None,
                grammar_proof_path=args.grammar_isolation if len(args.lrc) == 1 else None,
                audit_proof_path=args.audit_isolation if len(args.lrc) == 1 else None,
                replace=args.replace,
            )
            action = "written" if changed else "unchanged"
            print(f"{action}: {output}")
            print(f"finalizeReport: {workdir / 'finalize-report.json'}")
        except SidecarError as exc:
            print(f"ERROR [{exc.code}] {exc.path}: {exc.message}", file=sys.stderr)
            print(f"finalizeReport: {workdir / 'finalize-report.json'}", file=sys.stderr)
            fail_count += 1
        except Exception as exc:
            print(f"ERROR: failed on {lrc_path.name}: {exc}", file=sys.stderr)
            fail_count += 1

    return 0 if fail_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
