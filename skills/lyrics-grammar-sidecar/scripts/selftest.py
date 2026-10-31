#!/usr/bin/env python3
"""Standard-library-only regression suite for the v2 grammar generator."""

from __future__ import annotations

import copy
import os
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from typing import Any

from sidecarlib import (
    AUDIT_VERSION,
    ISOLATION_METHODS,
    ISOLATION_STAGES,
    ISOLATION_VERSION,
    MIN_REPETITION_CODE_POINTS,
    REPETITION_CANDIDATE_VERSION,
    SCHEMA_VERSION,
    SEMANTIC_AUDIT_SCHEMA_ID,
    SidecarError,
    configure_utf8_stdio,
    grammar_line_fingerprint,
    grammar_line_id,
    load_projection,
    pretty_json,
    read_json,
    sha256_file,
    source_projection_from_lrc,
    validate_manifest,
    validate_projection,
    validate_schema_instance,
    verify_frozen_translation,
    verify_translation_handoff,
    write_accepted_translation,
    write_json_atomic,
)
from v2lib import (
    AUDIT_FINDING_CODES,
    build_isolation_proof_v2,
    discover_repetition_candidates,
    finalize_sidecar_v2,
    load_frozen_candidate,
    load_v2_schema,
    normalized_analysis_candidate,
    prepare_semantic_candidate,
    validate_distinct_children,
    validate_finalized_sidecar_v2,
    validate_grammar_analysis_v2,
    validate_isolation_proof_v2,
    validate_repetition_completion,
    validate_semantic_audit,
)

SKILL_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = SKILL_ROOT / "fixtures"
GOLDEN_VECTORS = read_json(FIXTURES / "golden-vectors.json")
REPETITION_DIR = FIXTURES / "repetition"
REPETITION_SONG_ID = "Fixture - Repetition Pattern"
REPETITION_BLOCK_IDS = ("b_repeat1", "b_repeat2", "b_repeat3")


def labels(namespace: str, key: str) -> list[dict[str, str]]:
    return [{"namespace": namespace, "key": key}]


def line_map(projection: dict) -> dict[str, dict]:
    return {line["lineId"]: line for line in projection["lines"]}


def draft_line(line: dict, language: str) -> dict:
    return {
        "lineId": line["lineId"],
        "sourceOrder": line["sourceOrder"],
        "timeMs": line["timeMs"],
        "text": line["text"],
        "fingerprint": line["fingerprint"],
        "role": "text",
        "language": language,
    }


def segment(line: dict, start: int | None = None, end: int | None = None) -> dict:
    actual_start = 0 if start is None else start
    actual_end = len(line["text"]) if end is None else end
    return {
        "lineId": line["lineId"],
        "start": actual_start,
        "end": actual_end,
        "text": line["text"][actual_start:actual_end],
    }


def source_object(projection: dict, source_languages: list[str]) -> dict:
    return {
        "lrcFile": projection["lrcFile"],
        "songId": projection["songId"],
        "sourceLanguages": source_languages,
        "lineCount": projection["lineCount"],
        "lrcProjectionHash": projection["lrcProjectionHash"],
    }


def block_object(
    block_id: str,
    block_lines: list[dict],
    part_ids: list[str],
    *,
    text_before: dict[int, str] | None = None,
) -> dict:
    parts = []
    for index, occurrence_id in enumerate(part_ids):
        part = {"meaningUnitOccurrenceId": occurrence_id}
        if text_before and index in text_before:
            part["textBefore"] = text_before[index]
        parts.append(part)
    return {
        "blockId": block_id,
        "lineIds": [line["lineId"] for line in block_lines],
        "startTimeMs": min(line["timeMs"] for line in block_lines),
        "endTimeMs": max(line["timeMs"] for line in block_lines),
        "meaning": {"parts": parts},
        "labels": [],
        "confidence": 0.92,
    }


def meaning_unit(
    unit_id: str,
    text: str,
    occurrences: list[dict],
) -> dict:
    return {
        "meaningUnitId": unit_id,
        "text": text,
        "labels": [],
        "confidence": 0.9,
        "occurrences": occurrences,
    }


def meaning_occurrence(
    occurrence_id: str,
    block_id: str,
    segments: list[dict],
    *,
    override_text: str | None = None,
    reason: str = "语境变化需要单独表达。",
) -> dict:
    occurrence: dict[str, Any] = {
        "occurrenceId": occurrence_id,
        "blockId": block_id,
        "sourceSegments": segments,
    }
    if override_text is not None:
        occurrence["semanticOverride"] = {
            "reason": reason,
            "text": override_text,
        }
    return occurrence


def phrase_object(
    phrase_id: str,
    surface: str,
    occurrences: list[dict],
    *,
    explanation: str = "共享短语的规范语法解释。",
    gloss: str = "共享短语释义",
    role: str = "shared_phrase",
) -> dict:
    return {
        "phraseId": phrase_id,
        "kind": "phrase",
        "surface": surface,
        "role": role,
        "labels": labels("syntax", "shared_occurrence"),
        "explanation": explanation,
        "gloss": gloss,
        "confidence": 0.9,
        "occurrences": occurrences,
    }


def phrase_occurrence(
    occurrence_id: str,
    block_id: str,
    segments: list[dict],
    *,
    override: dict | None = None,
) -> dict:
    occurrence: dict[str, Any] = {
        "occurrenceId": occurrence_id,
        "blockId": block_id,
        "sourceSegments": segments,
    }
    if override is not None:
        occurrence["semanticOverride"] = override
    return occurrence


def base_document(
    projection: dict,
    lines: list[dict],
    blocks: list[dict],
    meaning_units: list[dict],
    phrases: list[dict],
    *,
    learner_language: str,
    source_languages: list[str],
    repetition_groups: list[dict] | None = None,
    relations: list[dict] | None = None,
    notes: list[dict] | None = None,
    uncertainties: list[dict] | None = None,
) -> dict:
    return {
        "schema": "sakurauta.lyrics-grammar-analysis",
        "schemaVersion": SCHEMA_VERSION,
        "source": source_object(projection, source_languages),
        "lines": lines,
        "repetitionGroups": repetition_groups or [],
        "meaningUnits": meaning_units,
        "blocks": blocks,
        "phrases": phrases,
        "relations": relations or [],
        "grammarNotes": notes or [],
        "uncertainties": uncertainties or [],
        "extensions": {"learnerLanguage": learner_language},
    }


def simple_document(
    projection: dict,
    *,
    source_language: str,
    learner_language: str,
    meaning_text: str,
    explanation: str,
    gloss: str,
) -> dict:
    lines = [draft_line(line, source_language) for line in projection["lines"]]
    block_id = "b_fixture"
    unit_id = "mu_fixture"
    occurrence_id = "muo_fixture"
    phrase_id = "p_fixture"
    phrase_occ_id = "po_fixture"
    block_segments = [segment(line) for line in projection["lines"]]
    units = [
        meaning_unit(
            unit_id,
            meaning_text,
            [meaning_occurrence(occurrence_id, block_id, block_segments)],
        )
    ]
    blocks = [block_object(block_id, projection["lines"], [occurrence_id])]
    phrase = phrase_object(
        phrase_id,
        "".join(line["text"] for line in projection["lines"]),
        [phrase_occurrence(phrase_occ_id, block_id, block_segments)],
        explanation=explanation,
        gloss=gloss,
    )
    notes = [
        {
            "noteId": "g_fixture",
            "scope": {"blockIds": [block_id], "phraseId": phrase_id},
            "explanation": explanation,
            "labels": labels("syntax", "fixture"),
            "confidence": 0.8,
        }
    ]
    return base_document(
        projection,
        lines,
        blocks,
        units,
        [phrase],
        learner_language=learner_language,
        source_languages=[source_language],
        notes=notes,
    )


def two_block_repeat_document(
    projection: dict,
    block_line_groups: list[list[dict]],
    *,
    learner_language: str = "zh-CN",
) -> dict:
    lines = [draft_line(line, "ja") for line in projection["lines"]]
    by_id = line_map(projection)
    blocks = []
    units = []
    phrases: list[dict] = []
    block_texts = [
        unicodedata.normalize("NFC", "".join(line["text"] for line in group))
        for group in block_line_groups
    ]
    share_full_unit = len(block_texts) >= 2 and len(set(block_texts)) == 1
    if share_full_unit:
        shared_occurrences = []
        for block_index, group in enumerate(block_line_groups):
            shared_occurrences.append(
                meaning_occurrence(
                    f"muo_shared_{block_index + 1}",
                    f"b_{block_index + 1}",
                    [segment(by_id[line["lineId"]]) for line in group],
                )
            )
        units.append(
            meaning_unit(
                "mu_shared",
                "两个块共享同一段完整含义。",
                shared_occurrences,
            )
        )
    for block_index, group in enumerate(block_line_groups):
        block_id = f"b_{block_index + 1}"
        occurrence_id = (
            f"muo_shared_{block_index + 1}"
            if share_full_unit
            else f"muo_{block_index + 1}"
        )
        if not share_full_unit:
            unit_occurrence = meaning_occurrence(
                occurrence_id,
                block_id,
                [segment(by_id[line["lineId"]]) for line in group],
            )
            units.append(
                meaning_unit(
                    f"mu_{block_index + 1}",
                    f"第{block_index + 1}个学习块的完整含义。",
                    [unit_occurrence],
                )
            )
        blocks.append(block_object(block_id, group, [occurrence_id]))
    return base_document(
        projection,
        lines,
        blocks,
        units,
        phrases,
        learner_language=learner_language,
        source_languages=["ja"],
    )


def repetition_projection() -> dict:
    return source_projection_from_lrc(
        REPETITION_DIR / "source.lrc",
        REPETITION_SONG_ID,
    )


def stage_repetition_fixture(base: Path) -> dict[str, Any]:
    source = base / "source.lrc"
    source.write_bytes((REPETITION_DIR / "source.lrc").read_bytes())
    projection = source_projection_from_lrc(source, REPETITION_SONG_ID)
    projection_path = base / "source-projection.json"
    write_json_atomic(projection_path, projection)

    work = base / "source.grammar-work"
    work.mkdir()
    _translated, _manifest, issues = write_accepted_translation(
        projection,
        REPETITION_DIR / "translated.lrc",
        work,
    )
    if issues:
        raise AssertionError([issue.as_dict() for issue in issues])

    draft = read_json(REPETITION_DIR / "semantic-draft.json")
    draft_path = work / "semantic-draft.json"
    write_json_atomic(draft_path, draft)
    return {
        "source": source,
        "projection": projection,
        "projection_path": projection_path,
        "work": work,
        "draft": draft,
        "draft_path": draft_path,
    }


def build_repetition_ready_chain(base: Path) -> dict[str, Any]:
    staged = stage_repetition_fixture(base)
    candidate, prepare_report = prepare_semantic_candidate(
        staged["source"],
        staged["projection_path"],
        staged["work"],
        staged["draft_path"],
        learner_language="zh-CN",
    )
    candidate_path = staged["work"] / "analysis-candidate.json"
    candidate_hash_path = staged["work"] / "analysis-candidate.sha256"
    frozen_candidate, candidate_hash = load_frozen_candidate(
        candidate_path,
        candidate_hash_path,
    )
    if frozen_candidate != candidate:
        raise AssertionError("Prepared candidate did not round-trip canonically.")

    audit = {
        "schema": SEMANTIC_AUDIT_SCHEMA_ID,
        "auditVersion": AUDIT_VERSION,
        "candidateSha256": candidate_hash,
        "learnerLanguage": "zh-CN",
        "status": "pass",
        "reviewedBlockIds": [
            block["blockId"] for block in candidate["blocks"]
        ],
        "reviewedMeaningUnitIds": [
            unit["meaningUnitId"] for unit in candidate["meaningUnits"]
        ],
        "findings": [],
    }
    audit_issues = validate_semantic_audit(
        audit,
        candidate,
        candidate_hash,
        "zh-CN",
    )
    if audit_issues:
        raise AssertionError([issue.as_dict() for issue in audit_issues])
    audit_path = staged["work"] / "semantic-audit.json"
    write_json_atomic(audit_path, audit)

    grammar_proof = build_isolation_proof_v2(
        staged["projection"],
        "grammar-analysis",
        staged["draft_path"],
        "native-subagent",
        child_transcript_id="fixture-grammar-child",
        parent_transcript_id="fixture-parent",
    )
    audit_proof = build_isolation_proof_v2(
        staged["projection"],
        "semantic-audit",
        audit_path,
        "native-subagent",
        child_transcript_id="fixture-audit-child",
        parent_transcript_id="fixture-parent",
    )
    grammar_proof_path = staged["work"] / "isolation-grammar-analysis.json"
    audit_proof_path = staged["work"] / "isolation-semantic-audit.json"
    write_json_atomic(grammar_proof_path, grammar_proof)
    write_json_atomic(audit_proof_path, audit_proof)
    return {
        **staged,
        "candidate": candidate,
        "candidate_path": candidate_path,
        "candidate_hash_path": candidate_hash_path,
        "candidate_hash": candidate_hash,
        "prepare_report": prepare_report,
        "audit": audit,
        "audit_path": audit_path,
        "grammar_proof_path": grammar_proof_path,
        "audit_proof_path": audit_proof_path,
    }


def issue_codes(issues) -> list[str]:
    return [issue.code for issue in issues]


class GrammarSidecarV2Tests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [sys.executable, *args],
            cwd=SKILL_ROOT.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )
        if completed.returncode != 0:
            self.fail(
                f"Command failed ({completed.returncode}): {args}\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )
        return completed

    def assert_valid(
        self,
        document: dict,
        projection: dict,
        learner_language: str | None = None,
    ) -> None:
        issues = validate_grammar_analysis_v2(
            document,
            projection=projection,
            expected_lrc_file=projection.get("lrcFile"),
            require_projection=True,
            learner_language=learner_language,
        )
        self.assertEqual([], [issue.as_dict() for issue in issues])

    def assert_issue(self, issues, code: str) -> None:
        self.assertTrue(
            any(issue.code == code for issue in issues),
            f"Expected {code}, got {[issue.as_dict() for issue in issues]}",
        )

    def test_four_language_minimal_fixtures_are_v2(self) -> None:
        cases = {
            "ja": ("ja", "zh-CN"),
            "ko": ("ko", "zh-CN"),
            "zh": ("zh", "zh-CN"),
            "en": ("en", "zh-CN"),
        }
        for directory, (source_language, learner_language) in cases.items():
            with self.subTest(directory=directory):
                source = FIXTURES / directory / "source.lrc"
                translated = FIXTURES / directory / "translated.lrc"
                projection = source_projection_from_lrc(
                    source,
                    {
                        "ja": "Fixture - Japanese Promise",
                        "ko": "Fixture - Korean Letter",
                        "zh": "Fixture - Chinese Night",
                        "en": "Fixture - English Horizon",
                    }[directory],
                )
                self.assertEqual(
                    projection,
                    read_json(FIXTURES / directory / "source-projection.json"),
                )
                sidecar = read_json(FIXTURES / directory / "sidecar.json")
                self.assertEqual(SCHEMA_VERSION, sidecar["schemaVersion"])
                self.assertEqual(
                    source_language,
                    sidecar["source"]["sourceLanguages"][0],
                )
                self.assert_valid(sidecar, projection, learner_language)
                report, issues = verify_translation_handoff(projection, translated)
                self.assertTrue(report["ok"])
                self.assertEqual([], issues)

    def test_edge_fixture_uses_code_point_spans(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "edge" / "source.lrc", "Fixture - Unicode Edge"
        )
        sidecar = read_json(FIXTURES / "edge" / "sidecar.json")
        self.assert_valid(sidecar, projection, "zh-CN")
        first_text = sidecar["lines"][0]["text"]
        self.assertEqual(3, len(first_text))
        self.assertEqual(
            [1, 2, 3],
            [span["end"] for span in sidecar["lines"][0]["languageSpans"]],
        )

    def test_v2_schema_contract(self) -> None:
        schema = load_v2_schema()
        self.assertEqual(2, schema["properties"]["schemaVersion"]["const"])
        self.assertTrue(
            {
                "repetitionGroups",
                "meaningUnits",
                "blocks",
                "phrases",
            }.issubset(set(schema["required"]))
        )
        self.assertEqual(
            2,
            schema["$defs"]["repetitionGroup"]["properties"]["occurrences"]["minItems"],
        )
        self.assertEqual(
            "blockMeaning",
            schema["$defs"]["block"]["properties"]["meaning"]["$ref"].split("/")[-1],
        )
        self.assertIn(
            "semanticOverride",
            schema["$defs"]["phraseOccurrence"]["properties"],
        )
        self.assertEqual(
            ["blockIds"],
            schema["$defs"]["scope"]["required"],
        )
        self.assertEqual(
            ["learnerLanguage"],
            schema["properties"]["extensions"]["required"],
        )
        audit_schema = load_v2_schema("semantic-audit.schema.json")
        self.assertEqual(
            AUDIT_FINDING_CODES,
            set(audit_schema["$defs"]["finding"]["properties"]["code"]["enum"]),
        )
        self.assertEqual(
            AUDIT_VERSION,
            audit_schema["properties"]["auditVersion"]["const"],
        )
        isolation_schema = load_v2_schema("isolation-proof.schema.json")
        self.assertEqual(
            ISOLATION_STAGES,
            set(isolation_schema["properties"]["stage"]["enum"]),
        )
        self.assertEqual(
            ISOLATION_METHODS,
            set(isolation_schema["properties"]["method"]["enum"]),
        )
        self.assertEqual(
            ISOLATION_VERSION,
            isolation_schema["properties"]["isolationVersion"]["const"],
        )
        candidate_schema = load_v2_schema("repetition-candidates.schema.json")
        self.assertEqual(
            REPETITION_CANDIDATE_VERSION,
            candidate_schema["properties"]["candidateVersion"]["const"],
        )
        self.assertEqual(
            MIN_REPETITION_CODE_POINTS,
            candidate_schema["properties"]["minimumCodePoints"]["const"],
        )

    def test_cross_repository_golden_vectors(self) -> None:
        self.assertEqual(
            {
                "projection": "sakurauta-lrc-text-projection-v1",
                "lineId": "sakurauta-lyric-line-id-v1",
                "fingerprint": "sakurauta-lyric-text-fingerprint-v1",
                "sidecar": 2,
                "isolation": 2,
                "semanticAudit": 1,
                "repetitionCandidates": 1,
                "minimumRepetitionCodePoints": 8,
            },
            GOLDEN_VECTORS["contractVersions"],
        )
        for vector in GOLDEN_VECTORS["fixtures"]:
            with self.subTest(vector=vector["name"]):
                with tempfile.TemporaryDirectory() as temp:
                    path = Path(temp) / "golden.lrc"
                    path.write_text(vector["lrc"], encoding="utf-8")
                    projection = source_projection_from_lrc(path, vector["songId"])
                self.assertEqual(
                    vector["lrcProjectionHash"], projection["lrcProjectionHash"]
                )
                self.assertEqual(len(vector["lines"]), len(projection["lines"]))
                self.assertEqual(
                    vector["lines"],
                    [
                        {key: actual[key] for key in expected}
                        for expected, actual in zip(vector["lines"], projection["lines"])
                    ],
                )
                for segment in vector.get("segments", []):
                    line = projection["lines"][segment["lineIndex"]]
                    self.assertEqual(
                        segment["text"],
                        line["text"][segment["start"] : segment["end"]],
                    )
        self.assertEqual(SCHEMA_VERSION, 2)
        self.assertEqual(ISOLATION_VERSION, 2)
        self.assertEqual(AUDIT_VERSION, 1)
        self.assertEqual(REPETITION_CANDIDATE_VERSION, 1)
        self.assertEqual(MIN_REPETITION_CODE_POINTS, 8)
        self.assertEqual(
            "NFC",
            GOLDEN_VECTORS["v2"]["repetition"]["normalization"],
        )
        self.assertTrue(
            GOLDEN_VECTORS["v2"]["repetition"]["ignoreLineBoundaries"]
        )
        self.assertTrue(
            GOLDEN_VECTORS["v2"]["repetition"]["preserveInternalWhitespace"]
        )
        self.assertFalse(GOLDEN_VECTORS["v2"]["repetition"]["fuzzy"])
        cross_line = GOLDEN_VECTORS["v2"]["repetition"]["crossLineNfc"]
        self.assertEqual(
            cross_line["normalizedText"],
            unicodedata.normalize("NFC", "".join(cross_line["rawParts"])),
        )
        self.assertEqual(
            3,
            GOLDEN_VECTORS["v2"]["repetitionFixture"]["sharedPrefixOccurrences"],
        )
        self.assertEqual(
            "repetition/source.lrc",
            GOLDEN_VECTORS["v2"]["repetitionFixture"]["sourceLrc"],
        )
        self.assertEqual(
            "repetition/sidecar.json",
            GOLDEN_VECTORS["v2"]["repetitionFixture"]["fixture"],
        )
        self.assertEqual(
            "repetition/semantic-draft.json",
            GOLDEN_VECTORS["v2"]["repetitionFixture"]["semanticDraft"],
        )
        self.assertEqual(
            "repetition/translated.lrc",
            GOLDEN_VECTORS["v2"]["repetitionFixture"]["translatedLrc"],
        )

    def test_repetition_fixture_candidate_schema_and_completion(self) -> None:
        projection = repetition_projection()
        document = read_json(REPETITION_DIR / "sidecar.json")
        candidates, issues = validate_repetition_completion(
            document,
            projection,
            learner_language="zh-CN",
        )
        self.assertEqual([], issues)
        schema = load_v2_schema("repetition-candidates.schema.json")
        self.assertEqual([], validate_schema_instance(candidates, schema))
        self.assertEqual(2, len(candidates["candidates"]))
        self.assertTrue(
            any(len(item["occurrences"]) == 3 for item in candidates["candidates"])
        )
        self.assertTrue(
            any(len(item["occurrences"]) == 2 for item in candidates["candidates"])
        )

    def test_committed_invalid_fixtures_are_v2_and_rejected(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "ja" / "source.lrc", "Fixture - Japanese Promise"
        )
        invalid_references = read_json(
            FIXTURES / "invalid" / "invalid-references.json"
        )
        self.assertEqual(2, invalid_references["schemaVersion"])
        self.assert_issue(
            validate_grammar_analysis_v2(
                invalid_references,
                projection,
                projection["lrcFile"],
                require_projection=True,
                learner_language="zh-CN",
            ),
            "unresolved_block",
        )
        html_text = read_json(FIXTURES / "invalid" / "html-text.json")
        self.assertEqual(2, html_text["schemaVersion"])
        self.assert_issue(
            validate_grammar_analysis_v2(
                html_text,
                projection,
                projection["lrcFile"],
                require_projection=True,
                learner_language="zh-CN",
            ),
            "html_content",
        )

    def test_projection_identity_and_lrc_parser_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "same.lrc"
            path.write_text(
                "[00:01.000]same\n"
                "[00:01.000]same\n"
                "[00:01.000]translation\n"
                "[00:02.00]after\n",
                encoding="utf-8",
            )
            projection = source_projection_from_lrc(path, "Fixture - Same Time")
        self.assertEqual(["same", "after"], [line["text"] for line in projection["lines"]])
        self.assertEqual([1000, 2000], [line["timeMs"] for line in projection["lines"]])
        self.assertEqual(
            grammar_line_id("Fixture - Same Time", 1000, 0, "same"),
            projection["lines"][0]["lineId"],
        )
        self.assertEqual(
            grammar_line_fingerprint("same"),
            projection["lines"][0]["fingerprint"],
        )

    def test_json_reader_rejects_ambiguous_or_nonstandard_json(self) -> None:
        cases = {
            "duplicate": b'{"a":1,"a":2}',
            "nan": b'{"a":NaN}',
            "overflow": b'{"a":1e999}',
            "surrogate": b'{"a":"\\ud800"}',
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "input.json"
            for name, payload in cases.items():
                with self.subTest(name=name):
                    path.write_bytes(payload)
                    with self.assertRaises(SidecarError) as context:
                        read_json(path)
                    self.assertEqual("invalid_json", context.exception.code)

            path.write_bytes(b'{"a":"\xff"}')
            with self.assertRaises(SidecarError) as context:
                read_json(path)
            self.assertEqual("invalid_utf8", context.exception.code)

    def test_exact_repetition_across_line_break_variation(self) -> None:
        source_text = "夜空を越えて君に会いに行く"
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            lrc = base / "repeat.lrc"
            lrc.write_text(
                "[00:01.00]夜空を越えて\n"
                "[00:02.00]君に会いに行く\n"
                "[00:04.00]夜空を越えて君に\n"
                "[00:05.00]会いに行く\n",
                encoding="utf-8",
            )
            projection = source_projection_from_lrc(lrc, "Fixture - Repetition")
            document = two_block_repeat_document(
                projection,
                [
                    projection["lines"][0:2],
                    projection["lines"][2:4],
                ],
            )
            candidates = discover_repetition_candidates(
                document, projection, learner_language="zh-CN"
            )
            self.assertEqual(
                candidates,
                discover_repetition_candidates(
                    document, projection, learner_language="zh-CN"
                ),
            )
        matches = [
            candidate
            for candidate in candidates["candidates"]
            if candidate["normalizedText"] == source_text
        ]
        self.assertEqual(1, len(candidates["candidates"]))
        self.assertEqual(1, len(matches))
        self.assertEqual(2, len(matches[0]["occurrences"]))
        first = matches[0]["occurrences"][0]["sourceSegments"]
        second = matches[0]["occurrences"][1]["sourceSegments"]
        self.assertEqual(2, len(first))
        self.assertEqual(2, len(second))
        self.assertEqual("夜空を越えて", first[0]["text"])
        self.assertEqual("君に会いに行く", first[1]["text"])
        self.assertEqual("夜空を越えて君に", second[0]["text"])
        self.assertEqual("会いに行く", second[1]["text"])

    def test_internal_whitespace_similarity_and_minimum_are_not_fuzzy(self) -> None:
        cases = [
            ("同じ 歌をうたう", "同じ歌をうたう"),
            ("かがやくうた", "かがやきうた"),
        ]
        for left, right in cases:
            with self.subTest(left=left, right=right):
                with tempfile.TemporaryDirectory() as temp:
                    lrc = Path(temp) / "near.lrc"
                    lrc.write_text(
                        f"[00:01.00]{left}\n[00:02.00]{right}\n",
                        encoding="utf-8",
                    )
                    projection = source_projection_from_lrc(lrc, "Fixture - Near")
                    document = two_block_repeat_document(
                        projection,
                        [projection["lines"][0:1], projection["lines"][1:2]],
                    )
                    candidates = discover_repetition_candidates(
                        document, projection, learner_language="zh-CN"
                    )
                self.assertEqual([], candidates["candidates"])

        with tempfile.TemporaryDirectory() as temp:
            lrc = Path(temp) / "short.lrc"
            lrc.write_text(
                "[00:01.00]短い歌\n[00:02.00]短い歌\n",
                encoding="utf-8",
            )
            projection = source_projection_from_lrc(lrc, "Fixture - Short")
            document = two_block_repeat_document(
                projection,
                [projection["lines"][0:1], projection["lines"][1:2]],
            )
            candidates = discover_repetition_candidates(
                document, projection, learner_language="zh-CN"
            )
        self.assertEqual([], candidates["candidates"])

    def test_repetition_discovery_keeps_a_later_uncontained_maximal_match(self) -> None:
        first_text = "ABCDEFGHIJxxxxABCDEFGHyy"
        second_text = "ABCDEFGHIJyyyyABCDEFGHzz"
        shared_short = "ABCDEFGH"
        with tempfile.TemporaryDirectory() as temp:
            lrc = Path(temp) / "maximal.lrc"
            lrc.write_text(
                f"[00:01.00]{first_text}\n[00:02.00]{second_text}\n",
                encoding="utf-8",
            )
            projection = source_projection_from_lrc(lrc, "Fixture - Maximal")
            document = two_block_repeat_document(
                projection,
                [projection["lines"][0:1], projection["lines"][1:2]],
            )
            candidates = discover_repetition_candidates(
                document, projection, learner_language="zh-CN"
            )
        shared = [
            candidate
            for candidate in candidates["candidates"]
            if candidate["normalizedText"] == shared_short
        ]
        self.assertEqual(1, len(shared))
        self.assertEqual(
            [14, 14],
            [
                occurrence["sourceSegments"][0]["start"]
                for occurrence in shared[0]["occurrences"]
            ],
        )

    def test_repetition_matching_uses_nfc_but_preserves_source_spans(self) -> None:
        decomposed = "cafe\u0301 emo\u0301tion"
        composed = "caf\u00e9 em\u00f3tion"
        with tempfile.TemporaryDirectory() as temp:
            lrc = Path(temp) / "nfc.lrc"
            lrc.write_text(
                f"[00:01.00]{decomposed}\n[00:02.00]{composed}\n",
                encoding="utf-8",
            )
            projection = source_projection_from_lrc(lrc, "Fixture - NFC")
            document = two_block_repeat_document(
                projection,
                [projection["lines"][0:1], projection["lines"][1:2]],
            )
            candidates = discover_repetition_candidates(
                document, projection, learner_language="zh-CN"
            )
        self.assertEqual(1, len(candidates["candidates"]))
        candidate = candidates["candidates"][0]
        self.assertEqual(unicodedata.normalize("NFC", decomposed), candidate["normalizedText"])
        first_segment = candidate["occurrences"][0]["sourceSegments"][0]
        second_segment = candidate["occurrences"][1]["sourceSegments"][0]
        self.assertEqual(decomposed, first_segment["text"])
        self.assertEqual(composed, second_segment["text"])
        self.assertEqual(len(decomposed), first_segment["end"])
        self.assertEqual(len(composed), second_segment["end"])

    def test_nfc_normalization_is_applied_after_line_boundaries_are_removed(self) -> None:
        expected_text = unicodedata.normalize("NFC", "cafe\u0301 emotion")
        with tempfile.TemporaryDirectory() as temp:
            lrc = Path(temp) / "cross-line-nfc.lrc"
            lrc.write_text(
                "[00:01.00]cafe\n"
                "[00:02.00]\u0301 emotion\n"
                "[00:03.00]caf\u00e9 emotion\n",
                encoding="utf-8",
            )
            projection = source_projection_from_lrc(lrc, "Fixture - Cross-line NFC")
            document = two_block_repeat_document(
                projection,
                [projection["lines"][0:2], projection["lines"][2:3]],
            )
            candidates = discover_repetition_candidates(
                document, projection, learner_language="zh-CN"
            )
            full = [
                candidate
                for candidate in candidates["candidates"]
                if candidate["normalizedText"] == expected_text
            ]
            self.assertEqual(1, len(full))
            self.assertEqual(2, len(full[0]["occurrences"]))
            document["repetitionGroups"] = [
                {
                    "repetitionId": full[0]["repetitionId"],
                    "occurrences": full[0]["occurrences"],
                }
            ]
            self.assert_valid(document, projection, "zh-CN")
        self.assertEqual(
            ["cafe", "\u0301 emotion"],
            [segment["text"] for segment in full[0]["occurrences"][0]["sourceSegments"]],
        )
        self.assertEqual(
            ["caf\u00e9 emotion"],
            [segment["text"] for segment in full[0]["occurrences"][1]["sourceSegments"]],
        )

    def test_metadata_blocks_are_excluded_from_repetition(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            lrc = Path(temp) / "metadata.lrc"
            lrc.write_text(
                "[00:01.00]同じ長いメタデータ\n"
                "[00:02.00]同じ長いメタデータ\n",
                encoding="utf-8",
            )
            projection = source_projection_from_lrc(lrc, "Fixture - Metadata")
            document = two_block_repeat_document(
                projection,
                [projection["lines"][0:1], projection["lines"][1:2]],
            )
            document["blocks"][0]["labels"] = labels("discourse", "metadata")
            candidates = discover_repetition_candidates(
                document, projection, learner_language="zh-CN"
            )
        self.assertEqual([], candidates["candidates"])

    def test_repetition_completion_requires_exact_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            lrc = Path(temp) / "complete.lrc"
            lrc.write_text(
                "[00:01.00]同じ長いフレーズです\n"
                "[00:02.00]同じ長いフレーズです\n",
                encoding="utf-8",
            )
            projection = source_projection_from_lrc(lrc, "Fixture - Complete")
            document = two_block_repeat_document(
                projection,
                [projection["lines"][0:1], projection["lines"][1:2]],
            )
            candidates, issues = validate_repetition_completion(
                document, projection, learner_language="zh-CN"
            )
            self.assertTrue(candidates["candidates"])
            self.assert_issue(issues, "missing_repetition_candidate")
            document["repetitionGroups"] = [
                {
                    "repetitionId": candidate["repetitionId"],
                    "occurrences": candidate["occurrences"],
                }
                for candidate in candidates["candidates"]
            ]
            self.assertEqual(
                [],
                validate_repetition_completion(
                    document, projection, learner_language="zh-CN"
                )[1],
            )
            document["repetitionGroups"][0]["occurrences"][1][
                "occurrenceId"
            ] = "repo_tampered"
            self.assert_issue(
                validate_repetition_completion(
                    document, projection, learner_language="zh-CN"
                )[1],
                "repetition_candidate_mismatch",
            )

    def test_missing_and_mismatched_shared_occurrences_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            lrc = Path(temp) / "shared.lrc"
            lrc.write_text(
                "[00:01.00]夜明けの歌を君と歌う夜明けの歌を君と歌う\n"
                "[00:02.00]夜明けの歌を君と歌う夜明けの歌を君と歌う\n",
                encoding="utf-8",
            )
            projection = source_projection_from_lrc(lrc, "Fixture - Shared")
            document = two_block_repeat_document(
                projection,
                [projection["lines"][0:1], projection["lines"][1:2]],
            )
            phrase_text = "夜明けの歌を君と歌う"
            first_line = projection["lines"][0]
            second_line = projection["lines"][1]
            document["phrases"] = [
                phrase_object(
                    "p_shared",
                    phrase_text,
                    [
                        phrase_occurrence(
                            "po_one",
                            "b_1",
                            [segment(first_line, 0, len(phrase_text))],
                        ),
                        phrase_occurrence(
                            "po_two",
                            "b_2",
                            [
                                segment(
                                    second_line,
                                    len(phrase_text),
                                    len(phrase_text) * 2,
                                )
                            ],
                        ),
                    ],
                )
            ]
            candidates, _ = validate_repetition_completion(
                document, projection, learner_language="zh-CN"
            )
            document["repetitionGroups"] = [
                {
                    "repetitionId": candidate["repetitionId"],
                    "occurrences": candidate["occurrences"],
                }
                for candidate in candidates["candidates"]
                if candidate["normalizedText"] == first_line["text"]
            ]
            issues = validate_grammar_analysis_v2(
                document,
                projection,
                projection["lrcFile"],
                require_projection=True,
                learner_language="zh-CN",
            )
            self.assert_issue(issues, "shared-occurrence-mismatch")

            missing = copy.deepcopy(document)
            missing["phrases"][0]["occurrences"].pop()
            self.assert_issue(
                validate_grammar_analysis_v2(
                    missing,
                    projection,
                    projection["lrcFile"],
                    require_projection=True,
                    learner_language="zh-CN",
                ),
                "shared-occurrence-incomplete",
            )

    def test_meaning_parts_must_cover_every_block_line(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "ja" / "source.lrc", "Fixture - Japanese Promise"
        )
        document = simple_document(
            projection,
            source_language="ja",
            learner_language="zh-CN",
            meaning_text="你给我的约定，我至今仍记得。",
            explanation="跨行短语构成完整回忆。",
            gloss="你给我的小小约定",
        )
        bad = copy.deepcopy(document)
        bad["meaningUnits"][0]["occurrences"][0]["sourceSegments"].pop()
        bad["blocks"][0]["lineIds"].append(bad["blocks"][0]["lineIds"].pop(0))
        issues = validate_grammar_analysis_v2(
            bad,
            projection,
            projection["lrcFile"],
            require_projection=True,
            learner_language="zh-CN",
        )
        self.assert_issue(issues, "meaning_coverage_gap")

        no_snapshot = copy.deepcopy(document)
        no_snapshot["blocks"][0]["meaning"]["text"] = "duplicated snapshot"
        issues = validate_grammar_analysis_v2(
            no_snapshot,
            projection,
            projection["lrcFile"],
            require_projection=True,
            learner_language="zh-CN",
        )
        self.assert_issue(issues, "schema_additional_property")

    def test_missing_shared_meaning_unit_occurrence_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            lrc = Path(temp) / "unit-shared.lrc"
            lrc.write_text(
                "[00:01.00]共有される長い意味単位\n"
                "[00:02.00]共有される長い意味単位\n",
                encoding="utf-8",
            )
            projection = source_projection_from_lrc(lrc, "Fixture - Shared Unit")
            document = two_block_repeat_document(
                projection,
                [projection["lines"][0:1], projection["lines"][1:2]],
            )
            candidates, _ = validate_repetition_completion(
                document, projection, learner_language="zh-CN"
            )
            document["repetitionGroups"] = [
                {
                    "repetitionId": candidate["repetitionId"],
                    "occurrences": candidate["occurrences"],
                }
                for candidate in candidates["candidates"]
            ]
            self.assert_valid(document, projection, "zh-CN")

            missing = copy.deepcopy(document)
            missing["meaningUnits"][0]["occurrences"].pop()
            local_occurrence = meaning_occurrence(
                "muo_local_two",
                "b_2",
                [segment(projection["lines"][1])],
            )
            missing["meaningUnits"].append(
                meaning_unit(
                    "mu_local_two",
                    "第二个块在本语境中的独立含义。",
                    [local_occurrence],
                )
            )
            missing["blocks"][1]["meaning"]["parts"] = [
                {"meaningUnitOccurrenceId": "muo_local_two"}
            ]
            self.assert_issue(
                validate_grammar_analysis_v2(
                    missing,
                    projection,
                    projection["lrcFile"],
                    require_projection=True,
                    learner_language="zh-CN",
                ),
                "shared-occurrence-incomplete",
            )

    def test_override_requires_reason_and_is_explicit(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "ja" / "source.lrc", "Fixture - Japanese Promise"
        )
        document = simple_document(
            projection,
            source_language="ja",
            learner_language="zh-CN",
            meaning_text="你给我的约定，我至今仍记得。",
            explanation="跨行短语构成完整回忆。",
            gloss="你给我的小小约定",
        )
        document["phrases"][0]["occurrences"][0]["semanticOverride"] = {
            "reason": "该处语气更接近追忆。",
            "role": "recollection_theme",
            "gloss": "那段你给我的约定",
        }
        self.assert_valid(document, projection, "zh-CN")
        invalid = copy.deepcopy(document)
        invalid["phrases"][0]["occurrences"][0]["semanticOverride"].pop("reason")
        self.assert_issue(
            validate_grammar_analysis_v2(
                invalid, projection, projection["lrcFile"]
            ),
            "schema_required",
        )
        blank_reason = copy.deepcopy(document)
        blank_reason["phrases"][0]["occurrences"][0]["semanticOverride"][
            "reason"
        ] = "   "
        self.assert_issue(
            validate_grammar_analysis_v2(
                blank_reason, projection, projection["lrcFile"]
            ),
            "empty_override_reason",
        )
        blank_meaning = copy.deepcopy(document)
        blank_meaning["meaningUnits"][0]["occurrences"][0]["semanticOverride"] = {
            "reason": "   ",
            "text": "   ",
        }
        self.assert_issue(
            validate_grammar_analysis_v2(
                blank_meaning, projection, projection["lrcFile"]
            ),
            "empty_override_reason",
        )

    def test_multi_block_phrase_scope_and_occurrence_narrowing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            lrc = Path(temp) / "scope.lrc"
            lrc.write_text(
                "[00:01.00]同じ長いフレーズです\n"
                "[00:02.00]同じ長いフレーズです\n",
                encoding="utf-8",
            )
            projection = source_projection_from_lrc(lrc, "Fixture - Scope")
            document = two_block_repeat_document(
                projection,
                [projection["lines"][0:1], projection["lines"][1:2]],
            )
            occurrences = [
                phrase_occurrence(
                    f"po_{index}",
                    f"b_{index}",
                    [segment(projection["lines"][index - 1])],
                )
                for index in (1, 2)
            ]
            document["phrases"] = [
                phrase_object(
                    "p_scope",
                    projection["lines"][0]["text"],
                    occurrences,
                )
            ]
            document["grammarNotes"] = [
                {
                    "noteId": "g_scope",
                    "scope": {
                        "blockIds": ["b_1", "b_2"],
                        "phraseId": "p_scope",
                        "occurrenceIds": ["po_1", "po_2"],
                    },
                    "explanation": "两个块共享同一条语法说明。",
                    "labels": labels("syntax", "shared"),
                    "confidence": 0.9,
                }
            ]
            candidates, _ = validate_repetition_completion(
                document, projection, learner_language="zh-CN"
            )
            document["repetitionGroups"] = [
                {
                    "repetitionId": candidate["repetitionId"],
                    "occurrences": candidate["occurrences"],
                }
                for candidate in candidates["candidates"]
            ]
            self.assert_valid(document, projection, "zh-CN")
            document["grammarNotes"][0]["scope"]["occurrenceIds"] = ["po_missing"]
            self.assert_issue(
                validate_grammar_analysis_v2(
                    document, projection, projection["lrcFile"]
                ),
                "unresolved_phrase_occurrence",
            )
            document["grammarNotes"][0]["scope"]["occurrenceIds"] = ["po_1"]
            self.assert_issue(
                validate_grammar_analysis_v2(
                    document, projection, projection["lrcFile"]
                ),
                "scope_occurrence_block_gap",
            )

    def test_v1_documents_and_proofs_are_rejected(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "ja" / "source.lrc", "Fixture - Japanese Promise"
        )
        v1 = {
            "schema": "sakurauta.lyrics-grammar-analysis",
            "schemaVersion": 1,
            "source": source_object(projection, ["ja"]),
            "lines": [],
            "blocks": [],
            "phrases": [],
            "relations": [],
            "grammarNotes": [],
            "uncertainties": [],
        }
        issues = validate_grammar_analysis_v2(v1, projection)
        self.assertEqual(["unsupported_schema_version"], issue_codes(issues))

        with tempfile.TemporaryDirectory() as temp:
            artifact = Path(temp) / "artifact.json"
            artifact.write_text("{}\n", encoding="utf-8")
            v1_proof = {
                "schema": "sakurauta.lyrics-grammar-isolation-proof",
                "isolationVersion": 1,
                "method": "native-subagent",
                "context": "non-forked-transcript",
                "transcriptIsolated": True,
                "childTranscriptId": "child-grammar",
                "parentTranscriptId": "parent",
                "sourceProjectionHash": projection["lrcProjectionHash"],
            }
            proof_issues = validate_isolation_proof_v2(
                v1_proof,
                projection,
                stage="grammar-analysis",
                artifact_path=artifact,
            )
        self.assertEqual(
            ["unsupported_isolation_version"],
            issue_codes(proof_issues),
        )

    def test_finalizer_requires_replace_to_regenerate_v1_production(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            chain = build_repetition_ready_chain(base)
            source = chain["source"]
            projection_path = chain["projection_path"]
            work = chain["work"]
            legacy = {
                "schema": "sakurauta.lyrics-grammar-analysis",
                "schemaVersion": 1,
            }
            output = base / "source.json"
            write_json_atomic(output, legacy)
            with self.assertRaises(SidecarError) as context:
                finalize_sidecar_v2(
                    source,
                    projection_path,
                    work,
                    learner_language="zh-CN",
                )
            self.assertEqual("unsupported_schema_version", context.exception.code)
            self.assertIn("regenerate Schema v2", context.exception.message)
            regenerated, changed = finalize_sidecar_v2(
                source,
                projection_path,
                work,
                learner_language="zh-CN",
                replace=True,
            )
            self.assertTrue(changed)
            self.assertEqual(2, read_json(regenerated)["schemaVersion"])

    def test_candidate_normalization_uses_projected_source_fields(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "ja" / "source.lrc", "Fixture - Japanese Promise"
        )
        document = simple_document(
            projection,
            source_language="ja",
            learner_language="zh-CN",
            meaning_text="你给我的约定，我至今仍记得。",
            explanation="跨行短语构成完整回忆。",
            gloss="你给你的小小约定",
        )
        normalized = normalized_analysis_candidate(document, projection)
        self.assertEqual(projection["lines"][0]["text"], normalized["lines"][0]["text"])
        self.assert_valid(normalized, projection, "zh-CN")
        tampered = copy.deepcopy(document)
        tampered["lines"][0]["text"] = "changed"
        self.assert_issue(
            validate_grammar_analysis_v2(
                tampered, projection, projection["lrcFile"]
            ),
            "stale_source_projection",
        )

    def test_source_identity_checks_work_without_a_projection(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "ja" / "source.lrc", "Fixture - Japanese Promise"
        )
        document = simple_document(
            projection,
            source_language="ja",
            learner_language="zh-CN",
            meaning_text="你给我的约定，我至今仍记得。",
            explanation="跨行短语构成完整回忆。",
            gloss="你给你的小小约定",
        )
        wrong_count = copy.deepcopy(document)
        wrong_count["source"]["lineCount"] += 1
        self.assert_issue(
            validate_grammar_analysis_v2(wrong_count, learner_language="zh-CN"),
            "line_count_mismatch",
        )

        wrong_fingerprint = copy.deepcopy(document)
        wrong_fingerprint["lines"][0]["fingerprint"] = "sha256:" + "0" * 64
        self.assert_issue(
            validate_grammar_analysis_v2(
                wrong_fingerprint, learner_language="zh-CN"
            ),
            "invalid_line_fingerprint",
        )

        wrong_line_id = copy.deepcopy(document)
        wrong_line_id["lines"][0]["lineId"] = "l_notcanonical"
        self.assert_issue(
            validate_grammar_analysis_v2(wrong_line_id, learner_language="zh-CN"),
            "invalid_line_id",
        )

        reordered = copy.deepcopy(document)
        reordered["lines"].reverse()
        self.assert_issue(
            validate_grammar_analysis_v2(reordered, learner_language="zh-CN"),
            "non_canonical_line_order",
        )

        drive_relative = copy.deepcopy(document)
        drive_relative["source"]["lrcFile"] = "C:source.lrc"
        self.assert_issue(
            validate_grammar_analysis_v2(drive_relative, learner_language="zh-CN"),
            "invalid_lrc_basename",
        )

        duplicate_id = copy.deepcopy(document)
        duplicate_id["grammarNotes"].append(
            copy.deepcopy(duplicate_id["grammarNotes"][0])
        )
        self.assert_issue(
            validate_grammar_analysis_v2(duplicate_id, learner_language="zh-CN"),
            "duplicate_id",
        )

    def test_html_and_per_line_translation_fields_fail(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "ja" / "source.lrc", "Fixture - Japanese Promise"
        )
        document = simple_document(
            projection,
            source_language="ja",
            learner_language="zh-CN",
            meaning_text="你给我的约定，我至今仍记得。",
            explanation="跨行短语构成完整回忆。",
            gloss="你给你的小小约定",
        )
        injected = copy.deepcopy(document)
        injected["phrases"][0]["explanation"] = "<strong>unsafe</strong>"
        self.assert_issue(
            validate_grammar_analysis_v2(
                injected, projection, projection["lrcFile"]
            ),
            "html_content",
        )
        for unsafe_text in ("<!-- hidden -->", "&#X41;"):
            unsafe = copy.deepcopy(document)
            unsafe["phrases"][0]["explanation"] = unsafe_text
            self.assert_issue(
                validate_grammar_analysis_v2(
                    unsafe, projection, projection["lrcFile"]
                ),
                "html_content",
            )
        absolute_path = copy.deepcopy(document)
        absolute_path["phrases"][0]["explanation"] = "C:\\private\\lyrics.txt"
        self.assert_issue(
            validate_grammar_analysis_v2(
                absolute_path, projection, projection["lrcFile"]
            ),
            "absolute_path",
        )
        translated = copy.deepcopy(document)
        translated["phrases"][0]["translationText"] = "line translation"
        self.assert_issue(
            validate_grammar_analysis_v2(
                translated, projection, projection["lrcFile"]
            ),
            "schema_additional_property",
        )

    def test_learner_language_is_required_v2(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "ja" / "source.lrc", "Fixture - Japanese Promise"
        )
        document = simple_document(
            projection,
            source_language="ja",
            learner_language="zh-CN",
            meaning_text="你给我的约定，我至今仍记得。",
            explanation="跨行短语构成完整回忆。",
            gloss="你给你的小小约定",
        )
        missing = copy.deepcopy(document)
        missing.pop("extensions")
        self.assert_issue(
            validate_grammar_analysis_v2(
                missing, projection, projection["lrcFile"]
            ),
            "schema_required",
        )
        mismatch = copy.deepcopy(document)
        mismatch["extensions"]["learnerLanguage"] = "en"
        self.assert_issue(
            validate_grammar_analysis_v2(
                mismatch,
                projection,
                projection["lrcFile"],
                learner_language="zh-CN",
            ),
            "learner_language_mismatch",
        )
        blank_text = copy.deepcopy(document)
        blank_text["meaningUnits"][0]["text"] = "   "
        self.assert_issue(
            validate_grammar_analysis_v2(
                blank_text,
                projection,
                projection["lrcFile"],
                learner_language="zh-CN",
            ),
            "blank_learner_text",
        )
        blank_optional = copy.deepcopy(document)
        blank_optional["phrases"][0]["gloss"] = "   "
        self.assert_issue(
            validate_grammar_analysis_v2(
                blank_optional,
                projection,
                projection["lrcFile"],
            ),
            "blank_learner_text",
        )

    def test_semantic_audit_pass_fail_and_review_coverage(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "ja" / "source.lrc", "Fixture - Japanese Promise"
        )
        candidate = simple_document(
            projection,
            source_language="ja",
            learner_language="zh-CN",
            meaning_text="你给我的约定，我至今仍记得。",
            explanation="跨行短语构成完整回忆。",
            gloss="你给你的小小约定",
        )
        candidate_hash = "sha256:" + "a" * 64
        audit = {
            "schema": SEMANTIC_AUDIT_SCHEMA_ID,
            "auditVersion": AUDIT_VERSION,
            "candidateSha256": candidate_hash,
            "learnerLanguage": "zh-CN",
            "status": "pass",
            "reviewedBlockIds": ["b_fixture"],
            "reviewedMeaningUnitIds": ["mu_fixture"],
            "findings": [],
        }
        self.assertEqual(
            [],
            validate_semantic_audit(audit, candidate, candidate_hash, "zh-CN"),
        )
        failed = copy.deepcopy(audit)
        failed["status"] = "fail"
        failed["findings"] = [
            {
                "code": "unsupported-addition",
                "severity": "error",
                "message": "译文没有表达“最后的高潮”。",
            }
        ]
        self.assert_issue(
            validate_semantic_audit(failed, candidate, candidate_hash, "zh-CN"),
            "semantic_audit_failed",
        )
        missing_review = copy.deepcopy(audit)
        missing_review["reviewedMeaningUnitIds"] = []
        self.assert_issue(
            validate_semantic_audit(missing_review, candidate, candidate_hash, "zh-CN"),
            "audit_review_coverage",
        )
        hash_drift = copy.deepcopy(audit)
        hash_drift["candidateSha256"] = "sha256:" + "b" * 64
        self.assert_issue(
            validate_semantic_audit(hash_drift, candidate, candidate_hash, "zh-CN"),
            "audit_candidate_hash_mismatch",
        )

    def test_isolation_requires_distinct_stage_bound_children(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "ja" / "source.lrc", "Fixture - Japanese Promise"
        )
        with tempfile.TemporaryDirectory() as temp:
            draft_path = Path(temp) / "semantic-draft.json"
            audit_path = Path(temp) / "semantic-audit.json"
            write_json_atomic(draft_path, {"draft": True})
            write_json_atomic(audit_path, {"audit": True})
            grammar = build_isolation_proof_v2(
                projection,
                "grammar-analysis",
                draft_path,
                "native-subagent",
                child_transcript_id="child-grammar",
                parent_transcript_id="parent",
            )
            audit = build_isolation_proof_v2(
                projection,
                "semantic-audit",
                audit_path,
                "native-subagent",
                child_transcript_id="child-audit",
                parent_transcript_id="parent",
            )
            self.assertEqual(
                [],
                validate_isolation_proof_v2(
                    grammar,
                    projection,
                    stage="grammar-analysis",
                    artifact_path=draft_path,
                ),
            )
            self.assertEqual(
                [],
                validate_isolation_proof_v2(
                    audit,
                    projection,
                    stage="semantic-audit",
                    artifact_path=audit_path,
                ),
            )
            audit["childTranscriptId"] = "child-grammar"
            self.assert_issue(
                validate_distinct_children(grammar, audit),
                "same_child_self_audit",
            )
            blank_parent = copy.deepcopy(grammar)
            blank_parent["parentTranscriptId"] = "   "
            self.assert_issue(
                validate_isolation_proof_v2(
                    blank_parent,
                    projection,
                    stage="grammar-analysis",
                    artifact_path=draft_path,
                ),
                "invalid_parent_transcript",
            )
            with self.assertRaises(SidecarError) as context:
                build_isolation_proof_v2(
                    projection,
                    "semantic-audit",
                    audit_path,
                    "native-subagent",
                    child_transcript_id="child-audit",
                    parent_transcript_id="   ",
                )
            self.assertEqual("invalid_parent_transcript", context.exception.code)

    def test_full_staged_pipeline_atomic_writes_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            chain = build_repetition_ready_chain(base)
            source = chain["source"]
            projection = chain["projection"]
            projection_path = chain["projection_path"]
            work = chain["work"]
            draft = chain["draft"]
            draft_path = chain["draft_path"]
            candidate = chain["candidate"]
            candidate_hash = chain["candidate_hash"]
            self.assertEqual(
                candidate,
                read_json(REPETITION_DIR / "sidecar.json"),
            )
            self.assertEqual(
                [block["blockId"] for block in candidate["blocks"]],
                list(REPETITION_BLOCK_IDS),
            )
            audit = chain["audit"]
            audit_path = chain["audit_path"]
            grammar_proof_path = chain["grammar_proof_path"]
            audit_proof_path = chain["audit_proof_path"]

            output, changed = finalize_sidecar_v2(
                source,
                projection_path,
                work,
                learner_language="zh-CN",
            )
            self.assertTrue(changed)
            self.assertEqual(base / "source.json", output)
            self.assertEqual(candidate_hash, sha256_file(output))
            before = output.read_bytes()
            _, changed_again = finalize_sidecar_v2(
                source,
                projection_path,
                work,
                learner_language="zh-CN",
            )
            self.assertFalse(changed_again)
            self.assertEqual(before, output.read_bytes())
            self.assertEqual(
                [],
                validate_finalized_sidecar_v2(
                    source,
                    output,
                    projection_path,
                    work,
                    "zh-CN",
                ),
            )

            tampered_audit = copy.deepcopy(audit)
            tampered_audit["findings"] = [
                {
                    "code": "other",
                    "severity": "warning",
                    "message": "审计报告在 proof 后被修改。",
                }
            ]
            write_json_atomic(audit_path, tampered_audit, replace=True)
            with self.assertRaises(SidecarError) as context:
                finalize_sidecar_v2(
                    source,
                    projection_path,
                    work,
                    learner_language="zh-CN",
                    replace=True,
                )
            self.assertEqual("artifact_hash_mismatch", context.exception.code)
            write_json_atomic(audit_path, audit, replace=True)

            tampered_draft = copy.deepcopy(draft)
            tampered_draft["meaningUnits"][0]["text"] = "草稿在冻结后被修改。"
            write_json_atomic(draft_path, tampered_draft, replace=True)
            with self.assertRaises(SidecarError) as context:
                finalize_sidecar_v2(
                    source,
                    projection_path,
                    work,
                    learner_language="zh-CN",
                    replace=True,
                )
            self.assertEqual("candidate_content_drift", context.exception.code)
            write_json_atomic(draft_path, draft, replace=True)

            draft_path.write_bytes(
                pretty_json(draft).encode("utf-8").replace(b"{\n", b"{\n ", 1)
            )
            with self.assertRaises(SidecarError) as context:
                finalize_sidecar_v2(
                    source,
                    projection_path,
                    work,
                    learner_language="zh-CN",
                    replace=True,
                )
            self.assertEqual("artifact_hash_mismatch", context.exception.code)
            write_json_atomic(draft_path, draft, replace=True)

            failing_audit = copy.deepcopy(audit)
            failing_audit["status"] = "fail"
            failing_audit["findings"] = [
                {
                    "code": "translation-mismatch",
                    "severity": "error",
                    "message": "新增了译文没有表达的语义。",
                }
            ]
            write_json_atomic(audit_path, failing_audit, replace=True)
            audit_proof = build_isolation_proof_v2(
                projection,
                "semantic-audit",
                audit_path,
                "native-subagent",
                child_transcript_id="child-audit",
                parent_transcript_id="parent",
            )
            write_json_atomic(audit_proof_path, audit_proof, replace=True)
            with self.assertRaises(SidecarError) as context:
                finalize_sidecar_v2(
                    source,
                    projection_path,
                    work,
                    learner_language="zh-CN",
                    replace=True,
                )
            self.assertEqual("semantic_audit_failed", context.exception.code)

            write_json_atomic(audit_path, audit, replace=True)
            audit_proof = build_isolation_proof_v2(
                projection,
                "semantic-audit",
                audit_path,
                "native-subagent",
                child_transcript_id="fixture-grammar-child",
                parent_transcript_id="parent",
            )
            write_json_atomic(audit_proof_path, audit_proof, replace=True)
            with self.assertRaises(SidecarError) as context:
                finalize_sidecar_v2(
                    source,
                    projection_path,
                    work,
                    learner_language="zh-CN",
                    replace=True,
                )
            self.assertEqual("same_child_self_audit", context.exception.code)

    def test_hash_drift_and_existing_output_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "source.lrc"
            source.write_bytes((FIXTURES / "ja" / "source.lrc").read_bytes())
            projection = source_projection_from_lrc(
                source, "Fixture - Japanese Promise"
            )
            projection_path = base / "source-projection.json"
            write_json_atomic(projection_path, projection)
            work = base / "source.grammar-work"
            work.mkdir()
            write_accepted_translation(
                projection, FIXTURES / "ja" / "translated.lrc", work
            )
            draft = simple_document(
                projection,
                source_language="ja",
                learner_language="zh-CN",
                meaning_text="你给我的约定，我至今仍记得。",
                explanation="跨行短语构成完整回忆。",
                gloss="你给你的小小约定",
            )
            draft_path = work / "semantic-draft.json"
            write_json_atomic(draft_path, draft)
            candidate, report = prepare_semantic_candidate(
                source,
                projection_path,
                work,
                draft_path,
                learner_language="zh-CN",
            )
            audit = {
                "schema": SEMANTIC_AUDIT_SCHEMA_ID,
                "auditVersion": AUDIT_VERSION,
                "candidateSha256": report["candidateHash"],
                "learnerLanguage": "zh-CN",
                "status": "pass",
                "reviewedBlockIds": ["b_fixture"],
                "reviewedMeaningUnitIds": ["mu_fixture"],
                "findings": [],
            }
            audit_path = work / "semantic-audit.json"
            write_json_atomic(audit_path, audit)
            for stage, child, path in (
                ("grammar-analysis", "child-grammar", draft_path),
                ("semantic-audit", "child-audit", audit_path),
            ):
                proof = build_isolation_proof_v2(
                    projection,
                    stage,
                    path,
                    "native-subagent",
                    child_transcript_id=child,
                    parent_transcript_id="parent",
                )
                write_json_atomic(
                    work
                    / (
                        "isolation-grammar-analysis.json"
                        if stage == "grammar-analysis"
                        else "isolation-semantic-audit.json"
                    ),
                    proof,
                )

            existing = base / "source.json"
            existing.write_text('{"different":true}\n', encoding="utf-8")
            with self.assertRaises(SidecarError) as context:
                finalize_sidecar_v2(
                    source,
                    projection_path,
                    work,
                    learner_language="zh-CN",
                )
            self.assertEqual("output_exists", context.exception.code)
            self.assertEqual('{"different":true}\n', existing.read_text(encoding="utf-8"))

            candidate_path = work / "analysis-candidate.json"
            candidate["meaningUnits"][0]["text"] = "候选被篡改。"
            write_json_atomic(candidate_path, candidate, replace=True)
            with self.assertRaises(SidecarError) as context:
                finalize_sidecar_v2(
                    source,
                    projection_path,
                    work,
                    learner_language="zh-CN",
                    replace=True,
                )
            self.assertEqual("candidate_hash_drift", context.exception.code)

    def test_frozen_candidate_must_use_canonical_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            chain = build_repetition_ready_chain(Path(temp))
            candidate = chain["candidate_path"]
            candidate_hash = chain["candidate_hash_path"]
            original = candidate.read_bytes()
            reformatted = original.replace(b"{\n", b"{\n ", 1)
            self.assertNotEqual(original, reformatted)
            candidate.write_bytes(reformatted)
            with self.assertRaises(SidecarError) as context:
                load_frozen_candidate(candidate, candidate_hash)
        self.assertEqual("candidate_not_canonical", context.exception.code)

    def test_prepare_rejects_artifact_path_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            draft = work / "semantic-draft.json"
            write_json_atomic(draft, {"draft": True})
            with self.assertRaises(SidecarError) as context:
                prepare_semantic_candidate(
                    work / "source.lrc",
                    work / "source-projection.json",
                    work,
                    draft,
                    learner_language="zh-CN",
                    candidate_path=draft,
                )
        self.assertEqual("artifact_path_collision", context.exception.code)

    def test_translation_freeze_tamper_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "source.lrc"
            source.write_bytes((FIXTURES / "en" / "source.lrc").read_bytes())
            projection = source_projection_from_lrc(
                source, "Fixture - English Horizon"
            )
            work = base / "source.grammar-work"
            work.mkdir()
            translated, manifest, issues = write_accepted_translation(
                projection, FIXTURES / "en" / "translated.lrc", work
            )
            self.assertEqual([], issues)
            self.assertEqual([], validate_manifest(manifest))
            translated.write_text(
                translated.read_text(encoding="utf-8").replace(
                    "I kept the door", "I changed the door", 1
                ),
                encoding="utf-8",
            )
            _, issues = verify_frozen_translation(projection, translated, manifest)
        self.assert_issue(issues, "translation_changed_after_freeze")

    def test_prepare_fails_on_stale_source_with_canonical_error_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "source.lrc"
            source.write_bytes((FIXTURES / "ja" / "source.lrc").read_bytes())
            projection = source_projection_from_lrc(
                source, "Fixture - Japanese Promise"
            )
            projection_path = base / "source-projection.json"
            write_json_atomic(projection_path, projection)
            work = base / "source.grammar-work"
            work.mkdir()
            write_accepted_translation(
                projection, FIXTURES / "ja" / "translated.lrc", work
            )
            draft = simple_document(
                projection,
                source_language="ja",
                learner_language="zh-CN",
                meaning_text="你给我的约定，我至今仍记得。",
                explanation="跨行短语构成完整回忆。",
                gloss="你给你的小小约定",
            )
            write_json_atomic(work / "semantic-draft.json", draft)
            source.write_text(
                source.read_text(encoding="utf-8").replace(
                    "君がくれた", "君が笑った", 1
                ),
                encoding="utf-8",
            )
            with self.assertRaises(SidecarError) as context:
                prepare_semantic_candidate(
                    source,
                    projection_path,
                    work,
                    work / "semantic-draft.json",
                    learner_language="zh-CN",
                )
        self.assertEqual("stale_source_projection", context.exception.code)
        self.assertEqual("$.source.lrcProjectionHash", context.exception.path)
        self.assertIn("source LRC", context.exception.message)

    def test_projection_rejects_remote_and_non_lrc(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "song.txt"
            source.write_text("[00:01.00]x\n", encoding="utf-8")
            with self.assertRaises(SidecarError) as context:
                source_projection_from_lrc(source, "Fixture")
            self.assertEqual("unsupported_source", context.exception.code)
        with self.assertRaises(SidecarError) as context:
            source_projection_from_lrc(
                Path("https://example.invalid/song.lrc"), "Fixture"
            )
        self.assertEqual("unsupported_source", context.exception.code)

    def test_validation_does_not_mutate_document(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "ja" / "source.lrc", "Fixture - Japanese Promise"
        )
        document = simple_document(
            projection,
            source_language="ja",
            learner_language="zh-CN",
            meaning_text="你给我的约定，我至今仍记得。",
            explanation="跨行短语构成完整回忆。",
            gloss="你给你的小小约定",
        )
        before = pretty_json(document)
        validate_grammar_analysis_v2(
            document,
            projection,
            projection["lrcFile"],
            require_projection=True,
            learner_language="zh-CN",
        )
        self.assertEqual(before, pretty_json(document))

    def test_repetition_fixture_three_occurrences_changed_tail_and_override(
        self,
    ) -> None:
        self.assertEqual(
            {
                ".gitattributes",
                "semantic-draft.json",
                "sidecar.json",
                "source.lrc",
                "translated.lrc",
            },
            {path.name for path in REPETITION_DIR.iterdir()},
        )

        projection = repetition_projection()
        document = read_json(REPETITION_DIR / "sidecar.json")
        draft = read_json(REPETITION_DIR / "semantic-draft.json")
        self.assertEqual(document, draft)
        self.assertEqual(source_object(projection, ["ja"]), document["source"])
        self.assert_valid(document, projection, "zh-CN")
        candidates, issues = validate_repetition_completion(
            document, projection, learner_language="zh-CN"
        )
        self.assertEqual([], issues)
        prefix_text = "".join(line["text"] for line in projection["lines"][0:2])
        prefix_groups = [
            candidate
            for candidate in candidates["candidates"]
            if len(candidate["occurrences"]) == 3
            and candidate["normalizedText"] == unicodedata.normalize("NFC", prefix_text)
        ]
        self.assertTrue(prefix_groups)
        expected_tail_group = unicodedata.normalize(
            "NFC", prefix_text + projection["lines"][2]["text"]
        )
        tail_groups = [
            candidate
            for candidate in candidates["candidates"]
            if len(candidate["occurrences"]) == 2
            and candidate["normalizedText"] == expected_tail_group
        ]
        self.assertTrue(tail_groups)
        self.assertTrue(
            tail_groups[0]["normalizedText"].startswith(
                prefix_groups[0]["normalizedText"]
            )
        )
        shared_phrase = next(
            phrase
            for phrase in document["phrases"]
            if phrase["phraseId"] == "p_dawn_song"
        )
        self.assertEqual(3, len(shared_phrase["occurrences"]))
        self.assertEqual(
            set(REPETITION_BLOCK_IDS),
            {occurrence["blockId"] for occurrence in shared_phrase["occurrences"]},
        )
        self.assertTrue(
            any(
                occurrence.get("semanticOverride", {}).get("reason")
                for occurrence in shared_phrase["occurrences"]
            )
        )
        self.assertTrue(
            any(
                note["scope"]["blockIds"] == list(REPETITION_BLOCK_IDS)
                for note in document["grammarNotes"]
            )
        )
        relation_ids = {relation["relationId"] for relation in document["relations"]}
        self.assertIn("r_shared_development", relation_ids)
        phrase_occurrence_counts = {
            phrase["phraseId"]: len(phrase["occurrences"])
            for phrase in document["phrases"]
        }
        for relation in document["relations"]:
            self.assertEqual(3, phrase_occurrence_counts[relation["fromPhraseId"]])
            self.assertEqual(3, phrase_occurrence_counts[relation["toPhraseId"]])
        block1_parts = {
            part["meaningUnitOccurrenceId"]
            for part in document["blocks"][0]["meaning"]["parts"]
        }
        block2_parts = {
            part["meaningUnitOccurrenceId"]
            for part in document["blocks"][1]["meaning"]["parts"]
        }
        block3_parts = {
            part["meaningUnitOccurrenceId"]
            for part in document["blocks"][2]["meaning"]["parts"]
        }
        self.assertIn("muo_tail_base_1", block1_parts)
        self.assertIn("muo_tail_base_2", block2_parts)
        self.assertIn("muo_tail_extra_2", block2_parts)
        self.assertNotIn("muo_tail_extra_2", block1_parts)
        self.assertIn("muo_tail_alt_3", block3_parts)
        self.assertNotIn("muo_tail_alt_3", block2_parts)
        self.assertTrue(
            projection["lines"][5]["text"].startswith(projection["lines"][2]["text"])
        )
        tail_parts = document["blocks"][1]["meaning"]["parts"]
        self.assertGreaterEqual(len(tail_parts), 2)

    def test_load_projection_exposes_invalid_projection(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "ja" / "source.lrc", "Fixture - Japanese Promise"
        )
        self.assertEqual([], validate_projection(projection))
        tampered = copy.deepcopy(projection)
        tampered["lines"][0]["text"] = "changed"
        self.assertTrue(validate_projection(tampered))

    def test_cli_stage_scripts_run_end_to_end(self) -> None:
        scripts = SKILL_ROOT / "scripts"
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "cli-source.lrc"
            source.write_bytes((FIXTURES / "ja" / "source.lrc").read_bytes())
            accepted = base / "accepted.lrc"
            accepted.write_bytes((FIXTURES / "ja" / "translated.lrc").read_bytes())
            work = base / "cli-source.grammar-work"
            projection_path = work / "source-projection.json"

            self.run_cli(
                str(scripts / "project_source.py"),
                str(source),
                "--song-id",
                "Fixture - Japanese Promise",
            )
            self.run_cli(
                str(scripts / "verify_translation.py"),
                str(projection_path),
                str(accepted),
                "--workdir",
                str(work),
            )
            projection = load_projection(projection_path)
            draft = simple_document(
                projection,
                source_language="ja",
                learner_language="zh-CN",
                meaning_text="你给我的约定，我至今仍记得。",
                explanation="跨行短语构成完整回忆。",
                gloss="你给你的小小约定",
            )
            draft_path = work / "semantic-draft.json"
            write_json_atomic(draft_path, draft)

            self.run_cli(
                str(scripts / "discover_repetitions.py"),
                str(draft_path),
                "--projection",
                str(projection_path),
                "--workdir",
                str(work),
                "--learner-language",
                "zh-CN",
                "--replace",
            )
            candidates = read_json(work / "repetition-candidates.json")
            draft["repetitionGroups"] = [
                {
                    "repetitionId": candidate["repetitionId"],
                    "occurrences": candidate["occurrences"],
                }
                for candidate in candidates["candidates"]
            ]
            write_json_atomic(draft_path, draft, replace=True)

            self.run_cli(
                str(scripts / "record_isolation.py"),
                str(projection_path),
                "--stage",
                "grammar-analysis",
                "--artifact",
                str(draft_path),
                "--method",
                "native-subagent",
                "--child-transcript-id",
                "cli-child-grammar",
                "--parent-transcript-id",
                "cli-parent",
                "--output",
                str(work / "isolation-grammar-analysis.json"),
                "--replace",
            )
            self.run_cli(
                str(scripts / "prepare.py"),
                str(source),
                "--projection",
                str(projection_path),
                "--workdir",
                str(work),
                "--learner-language",
                "zh-CN",
            )

            candidate_path = work / "analysis-candidate.json"
            candidate_hash_path = work / "analysis-candidate.sha256"
            candidate, candidate_hash = load_frozen_candidate(
                candidate_path, candidate_hash_path
            )
            audit = {
                "schema": SEMANTIC_AUDIT_SCHEMA_ID,
                "auditVersion": AUDIT_VERSION,
                "candidateSha256": candidate_hash,
                "learnerLanguage": "zh-CN",
                "status": "pass",
                "reviewedBlockIds": [
                    block["blockId"] for block in candidate["blocks"]
                ],
                "reviewedMeaningUnitIds": [
                    unit["meaningUnitId"] for unit in candidate["meaningUnits"]
                ],
                "findings": [],
            }
            audit_path = work / "semantic-audit.json"
            write_json_atomic(audit_path, audit)
            self.run_cli(
                str(scripts / "validate_audit.py"),
                str(candidate_path),
                "--candidate-hash",
                str(candidate_hash_path),
                "--learner-language",
                "zh-CN",
                str(audit_path),
            )
            self.run_cli(
                str(scripts / "record_isolation.py"),
                str(projection_path),
                "--stage",
                "semantic-audit",
                "--artifact",
                str(audit_path),
                "--method",
                "native-subagent",
                "--child-transcript-id",
                "cli-child-audit",
                "--parent-transcript-id",
                "cli-parent",
                "--output",
                str(work / "isolation-semantic-audit.json"),
                "--replace",
            )
            self.run_cli(
                str(scripts / "finalize.py"),
                str(source),
                "--projection",
                str(projection_path),
                "--workdir",
                str(work),
                "--learner-language",
                "zh-CN",
            )
            output = base / "cli-source.json"
            self.assertTrue(output.is_file())
            self.run_cli(
                str(scripts / "validate_all.py"),
                str(source),
                str(output),
                "--projection",
                str(projection_path),
                "--workdir",
                str(work),
                "--learner-language",
                "zh-CN",
            )


    def test_discover_repetitions_in_place_and_resolve_spans(self) -> None:
        scripts = SKILL_ROOT / "scripts"
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "cli-source.lrc"
            source.write_bytes((FIXTURES / "ja" / "source.lrc").read_bytes())
            work = base / "cli-source.grammar-work"
            projection_path = work / "source-projection.json"

            self.run_cli(
                str(scripts / "project_source.py"),
                str(source),
                "--song-id",
                "Fixture - Japanese Promise",
            )
            projection = load_projection(projection_path)
            draft = simple_document(
                projection,
                source_language="ja",
                learner_language="zh-CN",
                meaning_text="你给我的约定，我至今仍记得。",
                explanation="跨行短语构成完整回忆。",
                gloss="你给你的小小约定",
            )
            phrase_seg = draft["phrases"][0]["occurrences"][0]["sourceSegments"][0]
            phrase_seg["start"] = 999
            phrase_seg["end"] = 999

            draft_path = work / "semantic-draft.json"
            write_json_atomic(draft_path, draft)

            proc_query = self.run_cli(
                str(scripts / "resolve_spans.py"),
                "--projection",
                str(projection_path),
                "--line",
                "0",
                "--text",
                phrase_seg["text"],
            )
            self.assertIn('"lineId"', proc_query.stdout)

            self.run_cli(
                str(scripts / "resolve_spans.py"),
                str(draft_path),
                "--projection",
                str(projection_path),
                "--in-place",
            )
            fixed_draft = read_json(draft_path)
            fixed_seg = fixed_draft["phrases"][0]["occurrences"][0]["sourceSegments"][0]
            self.assertNotEqual(999, fixed_seg["start"])
            self.assertEqual(
                fixed_seg["text"],
                projection["lines"][0]["text"][fixed_seg["start"]:fixed_seg["end"]],
            )

            self.run_cli(
                str(scripts / "discover_repetitions.py"),
                str(draft_path),
                "--projection",
                str(projection_path),
                "--workdir",
                str(work),
                "--learner-language",
                "zh-CN",
                "--replace",
                "--in-place",
            )
            updated_draft = read_json(draft_path)
            candidates = read_json(work / "repetition-candidates.json")
            self.assertEqual(
                len(candidates["candidates"]),
                len(updated_draft["repetitionGroups"]),
            )


    def test_batch_cli_pipeline_end_to_end(self) -> None:
        scripts = SKILL_ROOT / "scripts"
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            src_a = base / "song_a.lrc"
            src_a.write_bytes((FIXTURES / "ja" / "translated.lrc").read_bytes())
            src_b = base / "song_b.lrc"
            src_b.write_bytes((FIXTURES / "ko" / "translated.lrc").read_bytes())

            # 1. Batch project_source
            self.run_cli(
                str(scripts / "project_source.py"),
                str(src_a),
                str(src_b),
            )
            work_a = base / "song_a.grammar-work"
            work_b = base / "song_b.grammar-work"
            proj_a = load_projection(work_a / "source-projection.json")
            proj_b = load_projection(work_b / "source-projection.json")
            self.assertEqual("song_a", proj_a["songId"])
            self.assertEqual("song_b", proj_b["songId"])

            # 2. Batch verify_translation
            self.run_cli(
                str(scripts / "verify_translation.py"),
                "--batch",
                str(src_a),
                str(src_b),
            )
            self.assertTrue((work_a / "translated.lrc").is_file())
            self.assertTrue((work_b / "translated.lrc").is_file())

            # Create drafts for both songs
            draft_a = simple_document(
                proj_a,
                source_language="ja",
                learner_language="zh-CN",
                meaning_text="你给我的约定，我至今仍记得。",
                explanation="跨行短语构成完整回忆。",
                gloss="你给你的小小约定",
            )
            write_json_atomic(work_a / "semantic-draft.json", draft_a)
            self.run_cli(
                str(scripts / "discover_repetitions.py"),
                str(work_a / "semantic-draft.json"),
                "--projection",
                str(work_a / "source-projection.json"),
                "--workdir",
                str(work_a),
                "--learner-language",
                "zh-CN",
                "--replace",
                "--in-place",
            )

            draft_b = simple_document(
                proj_b,
                source_language="ko",
                learner_language="zh-CN",
                meaning_text="风吹过的街角，依然留在原地。",
                explanation="诗意记忆的象征表达。",
                gloss="风吹过的街角",
            )
            write_json_atomic(work_b / "semantic-draft.json", draft_b)
            self.run_cli(
                str(scripts / "discover_repetitions.py"),
                str(work_b / "semantic-draft.json"),
                "--projection",
                str(work_b / "source-projection.json"),
                "--workdir",
                str(work_b),
                "--learner-language",
                "zh-CN",
                "--replace",
                "--in-place",
            )

            # 3. Batch prepare
            self.run_cli(
                str(scripts / "prepare.py"),
                str(src_a),
                str(src_b),
                "--learner-language",
                "zh-CN",
            )
            self.assertTrue((work_a / "analysis-candidate.json").is_file())
            self.assertTrue((work_b / "analysis-candidate.json").is_file())

            # Fake passing audits and isolation proofs for both songs
            for song_src, work_dir in (
                (src_a, work_a),
                (src_b, work_b),
            ):
                cand_path = work_dir / "analysis-candidate.json"
                cand_hash_path = work_dir / "analysis-candidate.sha256"
                cand, cand_hash = load_frozen_candidate(cand_path, cand_hash_path)
                audit = {
                    "schema": SEMANTIC_AUDIT_SCHEMA_ID,
                    "auditVersion": AUDIT_VERSION,
                    "candidateSha256": cand_hash,
                    "learnerLanguage": "zh-CN",
                    "status": "pass",
                    "reviewedBlockIds": [b["blockId"] for b in cand["blocks"]],
                    "reviewedMeaningUnitIds": [m["meaningUnitId"] for m in cand["meaningUnits"]],
                    "findings": [],
                }
                write_json_atomic(work_dir / "semantic-audit.json", audit)
                self.run_cli(
                    str(scripts / "record_isolation.py"),
                    str(work_dir / "source-projection.json"),
                    "--stage",
                    "grammar-analysis",
                    "--artifact",
                    str(work_dir / "semantic-draft.json"),
                    "--method",
                    "native-subagent",
                    "--child-transcript-id",
                    f"grammar-{song_src.stem}",
                    "--parent-transcript-id",
                    "batch-parent",
                    "--output",
                    str(work_dir / "isolation-grammar-analysis.json"),
                    "--replace",
                )
                self.run_cli(
                    str(scripts / "record_isolation.py"),
                    str(work_dir / "source-projection.json"),
                    "--stage",
                    "semantic-audit",
                    "--artifact",
                    str(work_dir / "semantic-audit.json"),
                    "--method",
                    "native-subagent",
                    "--child-transcript-id",
                    f"audit-{song_src.stem}",
                    "--parent-transcript-id",
                    "batch-parent",
                    "--output",
                    str(work_dir / "isolation-semantic-audit.json"),
                    "--replace",
                )

            # 4. Batch finalize
            self.run_cli(
                str(scripts / "finalize.py"),
                str(src_a),
                str(src_b),
                "--learner-language",
                "zh-CN",
            )
            self.assertTrue((base / "song_a.json").is_file())
            self.assertTrue((base / "song_b.json").is_file())

            # 5. Batch validate_all
            proc = self.run_cli(
                str(scripts / "validate_all.py"),
                "--batch",
                str(src_a),
                str(src_b),
                "--learner-language",
                "zh-CN",
            )
            self.assertIn("[PASS] song_a.lrc -> song_a.json", proc.stdout)
            self.assertIn("[PASS] song_b.lrc -> song_b.json", proc.stdout)


if __name__ == "__main__":
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    configure_utf8_stdio()
    unittest.main(verbosity=2)
