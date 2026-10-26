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
    load_projection,
    sha256_file,
    verify_frozen_translation,
    write_accepted_translation,
)


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Verify source rows in a translated LRC and write a freeze manifest."
    )
    parser.add_argument("projection", type=Path, help="source-projection.json")
    parser.add_argument(
        "accepted_lrc",
        type=Path,
        help="Bilingual LRC produced and accepted through lyrics-translator",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        required=True,
        help="<song>.grammar-work directory that will hold translated.lrc",
    )
    parser.add_argument(
        "--verify-frozen",
        action="store_true",
        help="Verify an existing manifest instead of creating it",
    )
    args = parser.parse_args()

    try:
        projection = load_projection(args.projection)
        workdir = args.workdir.expanduser().resolve()
        translated = workdir / "translated.lrc"
        manifest_path = workdir / "translation-manifest.json"
        if args.verify_frozen:
            if not manifest_path.exists():
                raise SidecarError(
                    "file_not_found", f"Manifest not found: {manifest_path}"
                )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            frozen_input = translated if translated.is_file() else args.accepted_lrc
            report, issues = verify_frozen_translation(
                projection, frozen_input, manifest
            )
            actual_hash = sha256_file(frozen_input) if frozen_input.is_file() else None
        else:
            if translated.exists() or manifest_path.exists():
                raise SidecarError(
                    "translation_already_frozen",
                    "Refusing to replace an existing frozen translation; use a fresh workdir.",
                )
            translated, manifest, issues = write_accepted_translation(
                projection, args.accepted_lrc, workdir
            )
            report = dict(manifest)
            report["ok"] = not issues
            actual_hash = manifest.get("translatedLrcSha256")
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

    report["translatedLrcSha256"] = actual_hash
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
