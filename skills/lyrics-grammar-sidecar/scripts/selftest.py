#!/usr/bin/env python3
"""Standard-library-only regression suite for the grammar sidecar generator."""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from sidecarlib import (
    SidecarError,
    build_isolation_proof,
    build_translation_manifest,
    build_validation_report,
    finalize_sidecar,
    load_schema,
    normalized_sidecar,
    pretty_json,
    source_projection_from_lrc,
    validate_isolation_proof,
    validate_manifest,
    validate_projection,
    validate_schema_instance,
    validate_sidecar_document,
    validate_sidecar_semantics,
    verify_frozen_translation,
    verify_source_matches_projection,
    verify_translation_handoff,
    write_accepted_translation,
    write_json_atomic,
    grammar_line_fingerprint,
    grammar_line_id,
)


SKILL_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = SKILL_ROOT / "fixtures"
DEFAULT_SONG_ID = "Fixture - Japanese Promise"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


GOLDEN_VECTORS = load_json(FIXTURES / "golden-vectors.json")


def configure_utf8_stdio() -> None:
    import sys

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def configure_no_bytecode() -> None:
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True


def labels(namespace: str, key: str) -> list[dict[str, str]]:
    return [{"namespace": namespace, "key": key}]


def build_draft(
    projection: dict,
    *,
    language: str = "ja",
    song_id: str | None = None,
    source_languages: list[str] | None = None,
    blocks: list[dict] | None = None,
    phrases: list[dict] | None = None,
    notes: list[dict] | None = None,
) -> dict:
    lines = [
        {
            "lineId": line["lineId"],
            "sourceOrder": line["sourceOrder"],
            "timeMs": line["timeMs"],
            "text": line["text"],
            "fingerprint": line["fingerprint"],
            "role": "text",
            "language": language,
        }
        for line in projection["lines"]
    ]
    line_ids = [line["lineId"] for line in lines]
    if blocks is None:
        blocks = [
            {
                "blockId": "b_fixture",
                "lineIds": line_ids,
                "startTimeMs": lines[0]["timeMs"],
                "endTimeMs": lines[-1]["timeMs"],
                "meaning": "整块学习释义。",
                "labels": labels("discourse", "fixture"),
                "confidence": 0.9,
            }
        ]
    if phrases is None:
        phrases = [
            {
                "phraseId": "p_fixture",
                "blockIds": [blocks[0]["blockId"]],
                "kind": "phrase",
                "sourceSegments": [
                    {
                        "lineId": line["lineId"],
                        "start": 0,
                        "end": len(line["text"]),
                        "text": line["text"],
                    }
                    for line in lines
                ],
                "surface": "".join(line["text"] for line in lines),
                "role": "fixture_phrase",
                "labels": labels(f"lang:{language}", "fixture"),
                "explanation": "Testing a structured grammar explanation.",
                "gloss": "fixture meaning",
                "confidence": 0.8,
            }
        ]
    if notes is None:
        notes = [
            {
                "noteId": "g_fixture",
                "scope": {
                    "blockId": blocks[0]["blockId"],
                    "phraseId": phrases[0]["phraseId"],
                },
                "explanation": "A namespaced grammar note.",
                "labels": labels("syntax", "fixture"),
                "confidence": 0.8,
            }
        ]
    return {
        "schema": "sakurauta.lyrics-grammar-analysis",
        "schemaVersion": 1,
        "source": {
            "lrcFile": projection["lrcFile"],
            "songId": song_id or projection["songId"],
            "sourceLanguages": source_languages or [language],
            "lineCount": projection["lineCount"],
            "lrcProjectionHash": projection["lrcProjectionHash"],
        },
        "lines": lines,
        "blocks": blocks,
        "phrases": phrases,
        "relations": [],
        "grammarNotes": notes,
        "uncertainties": [],
    }


class GrammarSidecarTests(unittest.TestCase):
    def assert_valid(self, document: dict, projection: dict) -> None:
        issues = validate_sidecar_document(
            document,
            projection=projection,
            expected_lrc_file=projection["lrcFile"],
            require_projection=True,
        )
        self.assertEqual([], [issue.as_dict() for issue in issues])

    def assert_issue(self, issues, code: str) -> None:
        self.assertTrue(
            any(issue.code == code for issue in issues),
            f"Expected {code}, got {[issue.as_dict() for issue in issues]}",
        )

    def test_four_language_minimal_fixtures(self) -> None:
        cases = {
            "ja": "ja",
            "ko": "ko",
            "zh": "zh",
            "en": "en",
        }
        for directory, language in cases.items():
            with self.subTest(language=language):
                source = FIXTURES / directory / "source.lrc"
                translated = FIXTURES / directory / "translated.lrc"
                sidecar = load_json(FIXTURES / directory / "sidecar.json")
                projection_file = load_json(
                    FIXTURES / directory / "source-projection.json"
                )
                projection = source_projection_from_lrc(
                    source,
                    {
                        "ja": "Fixture - Japanese Promise",
                        "ko": "Fixture - Korean Letter",
                        "zh": "Fixture - Chinese Night",
                        "en": "Fixture - English Horizon",
                    }[directory],
                )
                self.assertEqual(projection, projection_file)
                app_projection = {
                    key: projection_file[key]
                    for key in ("songId", "lineCount", "lrcProjectionHash", "lines")
                }
                self.assertEqual(
                    projection["lrcProjectionHash"],
                    app_projection["lrcProjectionHash"],
                )
                report, issues = verify_translation_handoff(projection, translated)
                self.assertEqual([], [issue.as_dict() for issue in issues])
                self.assertTrue(report["ok"])
                draft = build_draft(
                    projection,
                    language=language,
                    song_id=projection["songId"],
                )
                self.assert_valid(draft, projection)
                self.assert_valid(sidecar, projection)

    def test_duplicate_lines_receive_distinct_ids(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "edge" / "source.lrc", "Fixture - Unicode Edge"
        )
        ids = [line["lineId"] for line in projection["lines"]]
        self.assertEqual(len(ids), len(set(ids)))
        duplicate_texts = [
            line for line in projection["lines"] if line["text"] == "same"
        ]
        self.assertEqual(2, len(duplicate_texts))
        self.assertNotEqual(duplicate_texts[0]["lineId"], duplicate_texts[1]["lineId"])

    def test_cross_repository_golden_vectors(self) -> None:
        self.assertEqual(
            {
                "projection": "sakurauta-lrc-text-projection-v1",
                "lineId": "sakurauta-lyric-line-id-v1",
                "fingerprint": "sakurauta-lyric-text-fingerprint-v1",
            },
            GOLDEN_VECTORS["contractVersions"],
        )
        for vector in GOLDEN_VECTORS["fixtures"]:
            with self.subTest(vector=vector["name"]):
                with tempfile.TemporaryDirectory() as temp:
                    path = Path(temp) / "golden.lrc"
                    path.write_text(vector["lrc"], encoding="utf-8")
                    projection = source_projection_from_lrc(path, vector["songId"])
                expected_count = vector.get("lineCount", len(vector["lines"]))
                self.assertEqual(expected_count, projection["lineCount"])
                self.assertEqual(
                    vector["lrcProjectionHash"], projection["lrcProjectionHash"]
                )
                for expected, actual in zip(vector["lines"], projection["lines"]):
                    self.assertEqual(expected["lineId"], actual["lineId"])
                    self.assertEqual(expected["fingerprint"], actual["fingerprint"])
                    self.assertEqual(
                        expected["sameTimestampOrdinal"],
                        actual["sameTimestampOrdinal"],
                    )
                    self.assertEqual(expected["timeMs"], actual["timeMs"])
                    self.assertEqual(expected["text"], actual["text"])
                for segment in vector.get("segments", []):
                    line = projection["lines"][segment["lineIndex"]]
                    self.assertEqual(
                        segment["text"],
                        line["text"][segment["start"] : segment["end"]],
                    )

    def test_repeated_text_same_timestamp_uses_ordinals_without_identity_collision(self) -> None:
        song_id = "Fixture - Same Timestamp Ordinals"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "same-time.lrc"
            # The App groups by rounded milliseconds, so `.0004` remains in
            # the same group and the third row is translation.
            path.write_text(
                "[00:01.000]same\n"
                "[00:01.000]same\n"
                "[00:01.000]translation\n",
                encoding="utf-8",
            )
            projection = source_projection_from_lrc(path, song_id)
        self.assertEqual(1, projection["lineCount"])
        self.assertEqual(0, projection["lines"][0]["sameTimestampOrdinal"])
        self.assertEqual(
            grammar_line_id(song_id, 1000, 0, "same"),
            projection["lines"][0]["lineId"],
        )

    def test_schema_is_app_contract_shape(self) -> None:
        schema = load_schema()
        source = schema["properties"]["source"]
        self.assertEqual(
            ["lrcFile", "songId", "sourceLanguages", "lineCount", "lrcProjectionHash"],
            source["required"],
        )
        self.assertNotIn("pattern", source["properties"]["lrcFile"])
        self.assertNotIn("maxLength", source["properties"]["songId"])
        line = schema["$defs"]["line"]
        self.assertNotIn("sameTimestampOrdinal", line["properties"])
        self.assertEqual("text", line["properties"]["role"]["const"])
        phrase = schema["$defs"]["phrase"]
        self.assertGreaterEqual(phrase["properties"]["blockIds"]["minItems"], 1)

        draft = build_draft(
            source_projection_from_lrc(
                FIXTURES / "ja" / "source.lrc", DEFAULT_SONG_ID
            )
        )
        bad_basename = copy.deepcopy(draft)
        bad_basename["source"]["lrcFile"] = "F:/music/song.lrc"
        issues = validate_sidecar_document(
            bad_basename,
            projection=source_projection_from_lrc(
                FIXTURES / "ja" / "source.lrc", DEFAULT_SONG_ID
            ),
            expected_lrc_file="source.lrc",
        )
        self.assert_issue(issues, "absolute_path")

    def test_same_timestamp_first_source_line_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "same.lrc"
            path.write_text(
                "[00:01.00]same\n[00:01.00]same\n[00:02.00]after\n",
                encoding="utf-8",
            )
            projection = source_projection_from_lrc(path, DEFAULT_SONG_ID)
        self.assertEqual(["same", "after"], [line["text"] for line in projection["lines"]])
        self.assertEqual([1000, 2000], [line["timeMs"] for line in projection["lines"]])

    def test_app_lrc_parser_compatibility(self) -> None:
        body = (
            "[ti:metadata]\n"
            "[00:01]no millis is valid in the App\n"
            "[00:02.1]one decimal\n"
            "[00:03.1234]four decimals are not parsed\n"
            "[ 00:04.00]leading offset is not parsed\n"
            "[00:05.00][00:06.00]multiple timestamps are not parsed\n"
            "[00:07.00]词：Fixture\n"
            "[00:08.00]Lyrics by Fixture\n"
            "[00:09.00]   \n"
            "[00:10.00]kept\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "compat.lrc"
            path.write_text(body, encoding="utf-8")
            projection = source_projection_from_lrc(path, DEFAULT_SONG_ID)
        self.assertEqual(
            [
                "no millis is valid in the App",
                "one decimal",
                "[00:06.00]multiple timestamps are not parsed",
                "kept",
            ],
            [line["text"] for line in projection["lines"]],
        )
        self.assertEqual(
            [1000, 2100, 5000, 10000],
            [line["timeMs"] for line in projection["lines"]],
        )

    def test_song_id_participates_in_every_identity(self) -> None:
        source = FIXTURES / "ja" / "source.lrc"
        first = source_projection_from_lrc(source, "Artist - Song")
        second = source_projection_from_lrc(source, "Other Artist - Song")
        self.assertNotEqual(first["lines"][0]["lineId"], second["lines"][0]["lineId"])
        self.assertEqual(first["lines"][0]["fingerprint"], second["lines"][0]["fingerprint"])
        self.assertNotEqual(first["lrcProjectionHash"], second["lrcProjectionHash"])

    def test_cross_line_phrase_and_overlapping_blocks(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "ja" / "source.lrc", DEFAULT_SONG_ID
        )
        first_three = [line["lineId"] for line in projection["lines"][:3]]
        all_line_ids = [line["lineId"] for line in projection["lines"]]
        blocks = [
            {
                "blockId": "b_sentence",
                "lineIds": all_line_ids,
                "startTimeMs": 1000,
                "endTimeMs": 7000,
                "meaning": "整句是一个跨行名词短语加谓语。",
                "labels": labels("lang:ja", "sentence"),
                "confidence": 0.9,
            },
            {
                "blockId": "b_memory",
                "lineIds": [first_three[2]],
                "startTimeMs": 5000,
                "endTimeMs": 5000,
                "meaning": "谓语单独作为记忆焦点。",
                "labels": labels("discourse", "recollection"),
                "confidence": 0.8,
            },
        ]
        phrases = [
            {
                "phraseId": "p_cross_line",
                "blockIds": ["b_sentence"],
                "kind": "phrase",
                "sourceSegments": [
                    {
                        "lineId": first_three[0],
                        "start": 0,
                        "end": len(projection["lines"][0]["text"]),
                        "text": projection["lines"][0]["text"],
                    },
                    {
                        "lineId": first_three[1],
                        "start": 0,
                        "end": 5,
                        "text": projection["lines"][1]["text"][:5],
                    },
                ],
                "surface": projection["lines"][0]["text"] + projection["lines"][1]["text"][:5],
                "role": "object_noun_phrase",
                "labels": labels("lang:ja", "relative_clause"),
                "explanation": "A phrase that spans two source lines.",
                "confidence": 0.88,
            },
            {
                "phraseId": "p_focus",
                "blockIds": ["b_sentence", "b_memory"],
                "kind": "clause",
                "sourceSegments": [
                    {
                        "lineId": first_three[2],
                        "start": 0,
                        "end": len(projection["lines"][2]["text"]),
                        "text": projection["lines"][2]["text"],
                    }
                ],
                "surface": projection["lines"][2]["text"],
                "role": "predicate_clause",
                "labels": labels("lang:ja", "continuative"),
                "explanation": "The predicate remains true now.",
                "confidence": 0.86,
            },
        ]
        draft = build_draft(
            projection, blocks=blocks, phrases=phrases, notes=[]
        )
        self.assert_valid(draft, projection)
        self.assert_valid(load_json(FIXTURES / "ja" / "sidecar.json"), projection)

    def test_unicode_code_point_span(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "edge" / "source.lrc", "Fixture - Unicode Edge"
        )
        text = projection["lines"][0]["text"]
        self.assertEqual("😀e\u0301", text)
        self.assertEqual(3, len(text))
        draft = build_draft(projection, language="ja", source_languages=["ja", "en"])
        draft["lines"][0]["languageSpans"] = [
            {"start": 0, "end": 1, "language": "ja"},
            {"start": 1, "end": 2, "language": "en"},
            {"start": 2, "end": 3, "language": "ja"},
        ]
        phrase = draft["phrases"][0]
        phrase["sourceSegments"][0]["start"] = 0
        phrase["sourceSegments"][0]["end"] = 1
        phrase["sourceSegments"][0]["text"] = "😀"
        phrase["surface"] = "😀" + "".join(
            item["text"] for item in phrase["sourceSegments"][1:]
        )
        self.assert_valid(draft, projection)
        self.assert_valid(load_json(FIXTURES / "edge" / "sidecar.json"), projection)

    def test_translation_edit_does_not_change_projection_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "source.lrc"
            source.write_bytes((FIXTURES / "ja" / "source.lrc").read_bytes())
            projection = source_projection_from_lrc(source, DEFAULT_SONG_ID)
            translated = FIXTURES / "ja" / "translated.lrc"
            unchanged = base / "translated.lrc"
            unchanged.write_bytes(translated.read_bytes())
            changed = base / "translated-changed.lrc"
            changed.write_text(
                translated.read_text(encoding="utf-8").replace(
                    "你给我的", "我收到的", 1
                ),
                encoding="utf-8",
            )
            first, first_issues = verify_translation_handoff(projection, unchanged)
            second, second_issues = verify_translation_handoff(projection, changed)
            current_hash = source_projection_from_lrc(
                source, DEFAULT_SONG_ID
            )["lrcProjectionHash"]
            changed_text = changed.read_text(encoding="utf-8")
            changed.write_text(
                changed_text.replace("[00:01.00]君がくれた", "[00:01.00]君が笑った", 1),
                encoding="utf-8",
            )
            _, stale_issues = verify_translation_handoff(projection, changed)
        self.assertTrue(first["ok"])
        self.assertEqual([], [issue.as_dict() for issue in first_issues])
        self.assertTrue(second["ok"])
        self.assertEqual([], [issue.as_dict() for issue in second_issues])
        self.assertEqual(
            projection["lrcProjectionHash"],
            current_hash,
        )
        self.assert_issue(stale_issues, "stale_source_projection")

    def test_source_change_marks_projection_stale(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "stale" / "source-before.lrc", "Fixture - Stale"
        )
        report, issues = verify_source_matches_projection(
            projection, FIXTURES / "stale" / "source-after.lrc"
        )
        self.assertFalse(report["ok"])
        self.assert_issue(issues, "stale_source_projection")
        self.assertNotEqual(
            report["projectionHash"], report["currentProjectionHash"]
        )

    def test_invalid_references_and_uncovered_line_fail(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "ja" / "source.lrc", DEFAULT_SONG_ID
        )
        draft = build_draft(projection)
        draft["blocks"][0]["lineIds"] = draft["blocks"][0]["lineIds"][:1]
        draft["phrases"][0]["blockIds"] = ["b_missing"]
        draft["grammarNotes"][0]["scope"]["blockId"] = "b_missing"
        issues = validate_sidecar_document(draft, projection, projection["lrcFile"])
        self.assert_issue(issues, "uncovered_source_line")
        self.assert_issue(issues, "unresolved_block")

        invalid_fixture = load_json(FIXTURES / "invalid" / "invalid-references.json")
        issues = validate_sidecar_document(
            invalid_fixture, projection, projection["lrcFile"]
        )
        self.assert_issue(issues, "unresolved_line")
        self.assert_issue(issues, "unresolved_block")

    def test_html_looking_text_fails(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "ja" / "source.lrc", DEFAULT_SONG_ID
        )
        draft = build_draft(projection)
        draft["blocks"][0]["meaning"] = "<b>整块学习释义</b>"
        issues = validate_sidecar_document(draft, projection, projection["lrcFile"])
        self.assert_issue(issues, "html_content")
        draft = build_draft(projection)
        draft["blocks"][0]["meaning"] = "safe &amp; sound"
        issues = validate_sidecar_document(draft, projection, projection["lrcFile"])
        self.assert_issue(issues, "html_content")

        html_fixture = load_json(FIXTURES / "invalid" / "html-text.json")
        issues = validate_sidecar_document(
            html_fixture, projection, projection["lrcFile"]
        )
        self.assert_issue(issues, "html_content")

    def test_unknown_schema_keyword_fails_closed(self) -> None:
        schema = load_schema()
        schema_with_unknown = copy.deepcopy(schema)
        schema_with_unknown["properties"]["source"]["minProperties"] = 1
        schema_path = FIXTURES / "bad-schema.json"
        try:
            schema_path.write_text(
                json.dumps(schema_with_unknown), encoding="utf-8"
            )
            with self.assertRaises(SidecarError) as context:
                load_schema(schema_path)
            self.assertEqual("unsupported_schema_keyword", context.exception.code)
        finally:
            schema_path.unlink(missing_ok=True)

    def test_schema_rejects_bad_types_and_unknown_fields(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "ko" / "source.lrc", "Fixture - korean"
        )
        draft = build_draft(projection, language="ko")
        draft["source"]["lineCount"] = "1"
        issues = validate_sidecar_document(draft, projection, projection["lrcFile"])
        self.assert_issue(issues, "schema_type")

        draft = build_draft(projection, language="ko")
        draft["blocks"][0]["unknownField"] = True
        issues = validate_sidecar_document(draft, projection, projection["lrcFile"])
        self.assert_issue(issues, "schema_additional_property")

    def test_segment_span_and_surface_must_match_source(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "zh" / "source.lrc", "Fixture - chinese"
        )
        draft = build_draft(projection, language="zh")
        draft["phrases"][0]["sourceSegments"][0]["end"] = 99
        issues = validate_sidecar_document(draft, projection, projection["lrcFile"])
        self.assert_issue(issues, "invalid_codepoint_span")

        draft = build_draft(projection, language="zh")
        draft["phrases"][0]["surface"] = "wrong"
        issues = validate_sidecar_document(draft, projection, projection["lrcFile"])
        self.assert_issue(issues, "surface_mismatch")

    def test_per_line_translation_field_fails(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "en" / "source.lrc", "Fixture - english"
        )
        draft = build_draft(projection, language="en")
        draft["lines"][0]["lineTranslation"] = "translated"
        issues = validate_sidecar_document(draft, projection, projection["lrcFile"])
        self.assert_issue(issues, "per_line_translation")

        draft = build_draft(projection, language="en")
        draft["phrases"][0]["enParticle"] = "not namespaced"
        issues = validate_sidecar_document(draft, projection, projection["lrcFile"])
        self.assert_issue(issues, "schema_additional_property")

        draft = build_draft(projection, language="en")
        draft["phrases"][0]["syntax_role"] = "not namespaced"
        issues = validate_sidecar_document(draft, projection, projection["lrcFile"])
        self.assert_issue(issues, "unnamespaced_feature")

    def test_frozen_translation_manifest_and_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "source.lrc"
            source.write_bytes((FIXTURES / "en" / "source.lrc").read_bytes())
            projection = source_projection_from_lrc(
                source, "Fixture - English Horizon"
            )
            work = base / "song.grammar-work"
            work.mkdir()
            translated, manifest, issues = write_accepted_translation(
                projection, FIXTURES / "en" / "translated.lrc", work
            )
            self.assertEqual([], [issue.as_dict() for issue in issues])
            self.assertEqual([], [issue.as_dict() for issue in validate_manifest(manifest)])
            report, issues = verify_frozen_translation(projection, translated, manifest)
            self.assertTrue(report["ok"])
            self.assertEqual([], issues)

            translated.write_text(
                translated.read_text(encoding="utf-8").replace(
                    "我留着那扇门", "我为你留着门", 1
                ),
                encoding="utf-8",
            )
            report, issues = verify_frozen_translation(projection, translated, manifest)
            self.assertFalse(report["ok"])
            self.assert_issue(issues, "translation_changed_after_freeze")

    def test_finalizer_writes_deterministic_sibling_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "source.lrc"
            source.write_bytes((FIXTURES / "ja" / "source.lrc").read_bytes())
            projection = source_projection_from_lrc(source, DEFAULT_SONG_ID)
            projection_path = base / "source-projection.json"
            write_json_atomic(projection_path, projection)
            work = base / "source.grammar-work"
            work.mkdir()
            translated, manifest, issues = write_accepted_translation(
                projection, FIXTURES / "ja" / "translated.lrc", work
            )
            self.assertEqual([], [issue.as_dict() for issue in issues])
            manifest_path = work / "translation-manifest.json"
            draft = copy.deepcopy(load_json(FIXTURES / "ja" / "sidecar.json"))
            draft_path = work / "semantic-draft.json"
            write_json_atomic(draft_path, draft)
            isolation_path = work / "isolation-proof.json"
            proof = build_isolation_proof(
                projection,
                "codex-exec",
                child_transcript_id="child-finalize",
                parent_transcript_id="parent-finalize",
            )
            write_json_atomic(isolation_path, proof)

            output, changed = finalize_sidecar(
                source,
                projection_path,
                translated,
                manifest_path,
                draft_path,
                isolation_path=isolation_path,
                workdir=work,
                report_path=work / "validation-report.json",
            )
            self.assertTrue(changed)
            self.assertEqual(base / "source.json", output)
            self.assertTrue(output.is_file())
            report = load_json(work / "validation-report.json")
            self.assertTrue(report["ok"])
            before = output.read_bytes()
            _, changed_again = finalize_sidecar(
                source,
                projection_path,
                translated,
                manifest_path,
                draft_path,
                isolation_path=isolation_path,
                workdir=work,
                report_path=work / "validation-report.json",
            )
            self.assertFalse(changed_again)
            self.assertEqual(before, output.read_bytes())

    def test_finalizer_rejects_existing_different_output_without_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            source = base / "source.lrc"
            source.write_bytes((FIXTURES / "en" / "source.lrc").read_bytes())
            projection = source_projection_from_lrc(
                source, "Fixture - English Horizon"
            )
            projection_path = base / "source-projection.json"
            write_json_atomic(projection_path, projection)
            work = base / "source.grammar-work"
            work.mkdir()
            translated, _, issues = write_accepted_translation(
                projection, FIXTURES / "en" / "translated.lrc", work
            )
            self.assertEqual([], issues)
            draft = build_draft(projection, language="en")
            draft_path = work / "semantic-draft.json"
            write_json_atomic(draft_path, draft)
            isolation_path = work / "isolation-proof.json"
            write_json_atomic(
                isolation_path,
                build_isolation_proof(
                    projection,
                    "native-subagent",
                    child_transcript_id="child-existing-output",
                ),
            )
            existing = base / "source.json"
            existing.write_text('{"different":true}\n', encoding="utf-8")
            with self.assertRaises(SidecarError) as context:
                finalize_sidecar(
                    source,
                    projection_path,
                    translated,
                    work / "translation-manifest.json",
                    draft_path,
                    isolation_path=isolation_path,
                    workdir=work,
                )
            self.assertEqual("output_exists", context.exception.code)
            self.assertEqual('{"different":true}\n', existing.read_text(encoding="utf-8"))

    def test_translation_manifest_rejects_changed_source_text(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "ko" / "source.lrc", "Fixture - korean"
        )
        with tempfile.TemporaryDirectory() as temp:
            changed = Path(temp) / "changed.lrc"
            changed.write_text(
                (FIXTURES / "ko" / "translated.lrc")
                .read_text(encoding="utf-8")
                .replace("너에게 보내는", "그대에게 보내는", 1),
                encoding="utf-8",
            )
            manifest, issues = build_translation_manifest(projection, changed)
        self.assert_issue(issues, "stale_source_projection")
        self.assertFalse(manifest.get("ok", True))

    def test_untranslated_source_copy_is_not_a_handoff(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "ko" / "source.lrc", "Fixture - korean"
        )
        report, issues = verify_translation_handoff(
            projection, FIXTURES / "ko" / "source.lrc"
        )
        self.assertFalse(report["ok"])
        self.assertEqual(0, report["translationLines"])
        self.assert_issue(issues, "missing_translation")

    def test_isolation_proof_is_mandatory_and_non_forked(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "ja" / "source.lrc", DEFAULT_SONG_ID
        )
        proof = build_isolation_proof(
            projection,
            "native-subagent",
            child_transcript_id="child-1",
            parent_transcript_id="parent-1",
        )
        self.assertEqual([], validate_isolation_proof(proof, projection))
        inline = dict(proof)
        inline["context"] = "inline"
        self.assert_issue(validate_isolation_proof(inline, projection), "inline_generation")
        forked = dict(proof)
        forked["childTranscriptId"] = "parent-1"
        self.assert_issue(validate_isolation_proof(forked, projection), "inline_generation")
        self.assert_issue(
            validate_isolation_proof(None, projection), "missing_isolation_proof"
        )

    def test_projection_validation_and_canonical_normalization(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "edge" / "source.lrc", "Fixture - Unicode Edge"
        )
        self.assertEqual([], [issue.as_dict() for issue in validate_projection(projection)])
        draft = build_draft(projection, source_languages=["ja", "en"])
        changed = copy.deepcopy(draft)
        changed["lines"][0]["text"] = "tampered"
        issues = validate_sidecar_document(changed, projection, projection["lrcFile"])
        self.assert_issue(issues, "stale_source_projection")
        normalized = normalized_sidecar(draft, projection)
        self.assertEqual(projection["lines"][0]["text"], normalized["lines"][0]["text"])
        self.assertEqual(projection["lrcProjectionHash"], normalized["source"]["lrcProjectionHash"])

    def test_only_lrc_is_accepted_and_output_is_sibling_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "song.txt"
            source.write_text("[00:01.00]x\n", encoding="utf-8")
            with self.assertRaises(SidecarError) as context:
                source_projection_from_lrc(source, DEFAULT_SONG_ID)
            self.assertEqual("unsupported_source", context.exception.code)

            remote = Path("https://example.invalid/song.lrc")
            with self.assertRaises(SidecarError) as context:
                source_projection_from_lrc(remote, DEFAULT_SONG_ID)
            self.assertEqual("unsupported_source", context.exception.code)

            uppercase = Path(temp) / "song.LRC"
            uppercase.write_text("[00:01.00]x\n", encoding="utf-8")
            with self.assertRaises(SidecarError) as context:
                source_projection_from_lrc(uppercase, DEFAULT_SONG_ID)
            self.assertEqual("unsupported_source", context.exception.code)

            lrc = Path(temp) / "song.lrc"
            lrc.write_text("[00:01.00]x\n", encoding="utf-8")
            self.assertEqual(Path(temp) / "song.json", lrc.with_suffix(".json"))

    def test_translation_row_order_and_extra_rows_are_rejected(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "en" / "source.lrc", "Fixture - english"
        )
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            reordered = base / "reordered.lrc"
            reordered.write_text(
                "[00:09.00]I kept the door\n"
                "[00:11.00]open for your echo\n"
                "[00:09.00]我留着那扇门\n"
                "[00:11.00]等你的回声归来\n",
                encoding="utf-8",
            )
            _, issues = verify_translation_handoff(projection, reordered)
            self.assert_issue(issues, "stale_source_projection")

            stacked = base / "stacked.lrc"
            stacked.write_text(
                "[00:09.00]I kept the door\n"
                "[00:09.00]我留着那扇门\n"
                "[00:09.00]stacked third row\n"
                "[00:11.00]open for your echo\n"
                "[00:11.00]等你的回声归来\n",
                encoding="utf-8",
            )
            report, issues = verify_translation_handoff(projection, stacked)
            self.assertFalse(report["ok"])
            self.assert_issue(issues, "stale_source_projection")

    def test_schema_subset_resolves_refs_and_enforces_required(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "en" / "source.lrc", "Fixture - english"
        )
        draft = build_draft(projection, language="en")
        del draft["source"]["songId"]
        issues = validate_sidecar_document(draft, projection, projection["lrcFile"])
        self.assert_issue(issues, "schema_required")

        synthetic = {"$ref": "#/$defs/hash", "minLength": 1}
        schema = load_schema()
        from sidecarlib import _walk_schema

        probe = copy.deepcopy(schema)
        probe["properties"]["bad"] = synthetic
        self.assert_issue(_walk_schema(probe), "invalid_schema")

        bad_kind = copy.deepcopy(schema)
        bad_kind["properties"]["source"]["pattern"] = "["
        self.assert_issue(_walk_schema(bad_kind), "invalid_schema")

    def test_validation_does_not_mutate_document(self) -> None:
        projection = source_projection_from_lrc(
            FIXTURES / "ja" / "source.lrc", DEFAULT_SONG_ID
        )
        draft = build_draft(projection)
        original = pretty_json(draft)
        validate_sidecar_semantics(draft, projection, projection["lrcFile"])
        self.assertEqual(original, pretty_json(draft))


if __name__ == "__main__":
    configure_no_bytecode()
    configure_utf8_stdio()
    unittest.main(verbosity=2)
