#!/usr/bin/env python3
"""Environment-independent tests for the SakuraUta workflow orchestration."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_ROOT.parents[1]
GENERATOR = REPO_ROOT / "skills" / "lyrics-grammar-sidecar"
GENERATOR_SCRIPTS = GENERATOR / "scripts"
sys.path.insert(0, str(GENERATOR_SCRIPTS))

from sidecarlib import build_isolation_proof, write_accepted_translation, write_json_atomic  # noqa: E402


def run(script: str, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=cwd or REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def add_valid_draft_and_proof(work: Path, projection: dict) -> None:
    draft = json.loads((GENERATOR / "fixtures" / "ja" / "sidecar.json").read_text(encoding="utf-8"))
    draft["source"]["lrcFile"] = "Song.lrc"
    write_json_atomic(work / "semantic-draft.json", draft, replace=True)
    write_json_atomic(
        work / "isolation-proof.json",
        build_isolation_proof(
            projection,
            "native-subagent",
            child_transcript_id="selftest-child",
            parent_transcript_id="selftest-parent",
        ),
        replace=True,
    )


class WorkflowOrchestrationTests(unittest.TestCase):
    def make_fixture(self, root: Path) -> tuple[Path, Path]:
        source = root / "Song.lrc"
        source.write_bytes((GENERATOR / "fixtures" / "ja" / "source.lrc").read_bytes())
        work = root / "Song.grammar-work"
        work.mkdir()
        return source, work

    def test_preflight_rejects_non_lrc(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "Song.txt"
            path.write_text("[00:01.00]x\n", encoding="utf-8")
            result = run(
                str(SKILL_ROOT / "scripts" / "preflight.py"),
                str(path),
                "--target-language",
                "zh",
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported_source", result.stderr)

    def test_preflight_replace_status_works_in_fresh_workdir(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source, work = self.make_fixture(Path(temp))
            result = run(
                str(SKILL_ROOT / "scripts" / "preflight.py"),
                str(source),
                "--target-language",
                "zh",
                "--song-id",
                "Fixture - Japanese Promise",
                "--workdir",
                str(work),
                "--replace-status",
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_preflight_and_phase_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source, work = self.make_fixture(Path(temp))
            result = run(
                str(SKILL_ROOT / "scripts" / "preflight.py"),
                str(source),
                "--target-language",
                "zh",
                "--song-id",
                "Fixture - Japanese Promise",
                "--workdir",
                str(work),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            projection = json.loads((work / "source-projection.json").read_text(encoding="utf-8"))
            translated, manifest, issues = write_accepted_translation(
                projection, GENERATOR / "fixtures" / "ja" / "translated.lrc", work
            )
            self.assertEqual([], [issue.as_dict() for issue in issues])
            result = run(
                str(SKILL_ROOT / "scripts" / "check_phase.py"),
                str(work),
                "--phase",
                "frozen",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            translated.write_text(
                translated.read_text(encoding="utf-8").replace("你给我的", "我收到的", 1),
                encoding="utf-8",
            )
            result = run(
                str(SKILL_ROOT / "scripts" / "check_phase.py"),
                str(work),
                "--phase",
                "frozen",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("translation_changed_after_freeze", result.stderr)

    def test_child_skill_boundaries_are_declared(self) -> None:
        translation = (REPO_ROOT / "skills" / "lyrics-translation-handoff" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        grammar = (REPO_ROOT / "skills" / "lyrics-grammar-analysis" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        main = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("lyrics-translator/SKILL.md", translation)
        self.assertIn("semantic-draft.json", grammar)
        self.assertIn("no inline fallback", main)
        self.assertIn("sakurauta-lyrics-workflow", main)
        self.assertIn("independent", main.lower())

    def test_finalizer_requires_draft_and_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source, work = self.make_fixture(Path(temp))
            result = run(
                str(SKILL_ROOT / "scripts" / "preflight.py"),
                str(source),
                "--target-language",
                "zh",
                "--song-id",
                "Fixture - Japanese Promise",
                "--workdir",
                str(work),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            result = run(
                str(SKILL_ROOT / "scripts" / "check_phase.py"),
                str(work),
                "--phase",
                "drafted",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing", result.stderr)

    def test_drafted_phase_validates_draft_and_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source, work = self.make_fixture(Path(temp))
            result = run(
                str(SKILL_ROOT / "scripts" / "preflight.py"),
                str(source),
                "--target-language",
                "zh",
                "--song-id",
                "Fixture - Japanese Promise",
                "--workdir",
                str(work),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            projection = json.loads((work / "source-projection.json").read_text(encoding="utf-8"))
            _, _, issues = write_accepted_translation(
                projection, GENERATOR / "fixtures" / "ja" / "translated.lrc", work
            )
            self.assertEqual([], [issue.as_dict() for issue in issues])
            add_valid_draft_and_proof(work, projection)

            result = run(
                str(SKILL_ROOT / "scripts" / "check_phase.py"),
                str(work),
                "--phase",
                "drafted",
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            proof_path = work / "isolation-proof.json"
            proof = json.loads(proof_path.read_text(encoding="utf-8"))
            proof["childTranscriptId"] = proof["parentTranscriptId"]
            write_json_atomic(proof_path, proof, replace=True)
            result = run(
                str(SKILL_ROOT / "scripts" / "check_phase.py"),
                str(work),
                "--phase",
                "drafted",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("inline_generation", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
