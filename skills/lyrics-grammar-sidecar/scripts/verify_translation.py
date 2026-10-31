#!/usr/bin/env python3
"""Verify translation handoff and freeze an accepted bilingual LRC."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sidecarlib import (
    SidecarError,
    configure_utf8_stdio,
    ensure_inside_workdir,
    load_projection,
    read_json,
    sha256_file,
    verify_frozen_translation,
    write_accepted_translation,
)


def verify_single_translation(
    projection_path: Path,
    accepted_lrc_path: Path,
    workdir_path: Path,
    verify_frozen: bool = False,
) -> tuple[dict, list, str]:
    projection = load_projection(projection_path)
    workdir = workdir_path.expanduser().resolve()
    translated = workdir / "translated.lrc"
    manifest_path = ensure_inside_workdir(
        workdir / "translation-manifest.json",
        workdir,
        "translation manifest",
    )
    if verify_frozen:
        if not manifest_path.exists():
            raise SidecarError(
                "file_not_found", f"Manifest not found: {manifest_path}"
            )
        if not translated.is_file():
            raise SidecarError(
                "file_not_found", f"Frozen translation not found: {translated}"
            )
        manifest = read_json(manifest_path)
        report, issues = verify_frozen_translation(projection, translated, manifest)
        actual_hash = sha256_file(translated)
    else:
        if translated.exists() or manifest_path.exists():
            raise SidecarError(
                "translation_already_frozen",
                "Refusing to replace an existing frozen translation; use a fresh workdir.",
            )
        translated, manifest, issues = write_accepted_translation(
            projection, accepted_lrc_path, workdir
        )
        report = dict(manifest)
        report["ok"] = not issues
        actual_hash = manifest["translatedLrcSha256"]

    report["translatedLrcSha256"] = actual_hash
    return report, issues, actual_hash


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Verify source rows in a translated LRC and write a freeze manifest (single or batch)."
    )
    parser.add_argument(
        "files",
        type=Path,
        nargs="+",
        help="Single mode: <projection> <accepted_lrc>; Batch mode: <lrc1> <lrc2> ...",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        help="<song>.grammar-work directory (single mode only)",
    )
    parser.add_argument(
        "--verify-frozen",
        action="store_true",
        help="Verify an existing manifest instead of creating it",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Verify and freeze translations in batch mode for multiple LRC files",
    )
    args = parser.parse_args()

    if args.batch:
        fail_count = 0
        results = []
        for lrc_path in args.files:
            lrc_path = lrc_path.expanduser().resolve()
            workdir = lrc_path.parent / f"{lrc_path.stem}.grammar-work"
            proj_path = workdir / "source-projection.json"
            try:
                report, issues, actual_hash = verify_single_translation(
                    proj_path,
                    lrc_path,
                    workdir,
                    verify_frozen=args.verify_frozen,
                )
                if issues:
                    print(f"FAILED: {lrc_path.name}: {[i.code for i in issues]}", file=sys.stderr)
                    fail_count += 1
                else:
                    print(f"FROZEN: {lrc_path.name} -> {workdir / 'translated.lrc'} ({actual_hash[:16]}...)")
                results.append(report)
            except SidecarError as exc:
                print(f"ERROR [{exc.code}] {exc.path}: {exc.message}", file=sys.stderr)
                fail_count += 1
            except Exception as exc:
                print(f"ERROR: {lrc_path.name}: {exc}", file=sys.stderr)
                fail_count += 1
        return 0 if fail_count == 0 else 1

    if len(args.files) != 2:
        print("ERROR: single mode requires exactly 2 positional arguments: <projection> <accepted_lrc>", file=sys.stderr)
        return 2
    if args.workdir is None:
        print("ERROR: --workdir is required in single mode", file=sys.stderr)
        return 2

    projection_path = args.files[0]
    accepted_lrc_path = args.files[1]
    try:
        report, issues, actual_hash = verify_single_translation(
            projection_path,
            accepted_lrc_path,
            args.workdir,
            verify_frozen=args.verify_frozen,
        )
    except SidecarError as exc:
        print(f"ERROR [{exc.code}] {exc.path}: {exc.message}", file=sys.stderr)
        return 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR [invalid_manifest] $: {exc}", file=sys.stderr)
        return 2

    if issues:
        print(
            json.dumps(
                {"ok": False, "errors": [issue.as_dict() for issue in issues]},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
