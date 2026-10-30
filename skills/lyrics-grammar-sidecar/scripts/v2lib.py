#!/usr/bin/env python3
"""Schema v2 validation, repetition discovery, audit gates, and finalization."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sidecarlib import (
    ABSOLUTE_PATH_RE,
    AUDIT_VERSION,
    CJK_LEARNER_LANGUAGES,
    FILE_URI_RE,
    FORBIDDEN_TRANSLATION_KEY_RE,
    HASH_PATTERN,
    HTML_COMMENT_RE,
    HTML_DECL_RE,
    HTML_ENTITY_RE,
    HTML_TAG_RE,
    ID_PATTERN,
    ISOLATION_CONTEXT,
    ISOLATION_METHODS,
    ISOLATION_SCHEMA_ID,
    ISOLATION_STAGES,
    ISOLATION_VERSION,
    LEARNER_LANGUAGE_EXTENSION,
    MIN_REPETITION_CODE_POINTS,
    REPETITION_CANDIDATE_SCHEMA_ID,
    REPETITION_CANDIDATE_VERSION,
    SCHEMA_VERSION,
    SEMANTIC_AUDIT_SCHEMA_ID,
    SIDECAR_SCHEMA_ID,
    WINDOWS_ABSOLUTE_PATH_RE,
    Issue,
    SidecarError,
    _json_equal,
    _walk_schema,
    _write_bytes_atomic,
    assert_distinct_paths,
    canonical_json,
    ensure_inside_workdir,
    ensure_local_lrc,
    grammar_line_fingerprint,
    grammar_line_id,
    json_value_issues,
    load_projection,
    lrc_basename_issues,
    parse_json_bytes,
    pretty_json,
    read_json,
    sha256_bytes,
    sha256_file,
    verify_frozen_translation,
    verify_source_matches_projection,
    write_json_atomic,
)

AUDIT_FINDING_CODES = {
    "unsupported-addition",
    "translation-mismatch",
    "shared-drift",
    "coverage-gap",
    "missing-source-evidence",
    "other",
}


@dataclass(frozen=True)
class NormalizedLine:
    line_id: str
    source_order: int
    raw_text: str


@dataclass(frozen=True)
class BlockView:
    block_id: str
    line_ids: tuple[str, ...]
    normalized_text: str
    raw_text: str
    raw_to_normalized: tuple[int | None, ...]
    normalized_to_raw: dict[int, tuple[int, ...]]
    line_raw_ranges: dict[str, tuple[int, int]]
    line_indexes: dict[str, int]
    lines: tuple[NormalizedLine, ...]


@dataclass(frozen=True)
class OccurrenceRange:
    owner_id: str
    occurrence_id: str
    block_id: str
    start: int
    end: int
    normalized_text: str
    line_ids: tuple[str, ...]


def schema_path(name: str = "lyrics-grammar-analysis.schema.json") -> Path:
    return Path(__file__).resolve().parents[1] / "schema" / "v2" / name


def load_v2_schema(name: str = "lyrics-grammar-analysis.schema.json") -> dict[str, Any]:
    schema = read_json(schema_path(name))
    issues = _walk_schema(schema)
    if issues:
        raise SidecarError(issues[0].code, issues[0].message, issues[0].path)
    return schema


def _schema_issues(
    value: Any,
    schema_name: str,
    path: str = "$",
) -> list[Issue]:
    # Imported lazily to keep this module's public dependency surface small.
    from sidecarlib import validate_schema_instance

    value_issues = json_value_issues(value, path=path)
    if value_issues:
        return value_issues
    schema = load_v2_schema(schema_name)
    return validate_schema_instance(value, schema, path=path)


def _error(code: str, path: str, message: str) -> SidecarError:
    return SidecarError(code, message, path)


def _has_duplicates(values: Sequence[Any]) -> bool:
    return len({canonical_json(value) for value in values}) != len(values)


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def learner_language_of(document: Any) -> str | None:
    if not isinstance(document, dict):
        return None
    extensions = document.get("extensions")
    if not isinstance(extensions, dict):
        return None
    value = extensions.get(LEARNER_LANGUAGE_EXTENSION)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().lower()


def _is_ascii_prose(value: str) -> bool:
    letters = [char for char in value if char.isalpha()]
    return bool(letters) and all(ord(char) < 128 for char in letters)


def _has_cjk(value: str) -> bool:
    return any(
        "\u3400" <= char <= "\u9fff"
        or "\u3040" <= char <= "\u30ff"
        or "\uac00" <= char <= "\ud7af"
        for char in value
    )


def _iter_learner_text(document: dict[str, Any]) -> Iterable[tuple[str, str, str]]:
    def iter_label_displays(value: Any, path: str = "$") -> Iterable[tuple[str, str, str]]:
        if isinstance(value, dict):
            display = value.get("display")
            if (
                {"namespace", "key"}.issubset(value)
                and isinstance(display, str)
            ):
                yield path + ".display", display, "label_display"
            for key, child in value.items():
                yield from iter_label_displays(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from iter_label_displays(child, f"{path}[{index}]")

    yield from iter_label_displays(document)

    blocks = document.get("blocks")
    if isinstance(blocks, list):
        for block_index, block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            block_notes = block.get("notes")
            if isinstance(block_notes, str):
                yield f"$.blocks[{block_index}].notes", block_notes, "notes"
            meaning = block.get("meaning")
            parts = meaning.get("parts") if isinstance(meaning, dict) else None
            if isinstance(parts, list):
                for part_index, part in enumerate(parts):
                    if not isinstance(part, dict):
                        continue
                    text = part.get("textBefore")
                    if isinstance(text, str):
                        yield (
                            f"$.blocks[{block_index}].meaning.parts[{part_index}].textBefore",
                            text,
                            "connective",
                        )

    meaning_units = document.get("meaningUnits")
    if isinstance(meaning_units, list):
        for unit_index, unit in enumerate(meaning_units):
            if not isinstance(unit, dict):
                continue
            text = unit.get("text")
            if isinstance(text, str):
                yield f"$.meaningUnits[{unit_index}].text", text, "meaning"
            occurrences = unit.get("occurrences")
            if isinstance(occurrences, list):
                for occ_index, occurrence in enumerate(occurrences):
                    if not isinstance(occurrence, dict):
                        continue
                    override = occurrence.get("semanticOverride")
                    if not isinstance(override, dict):
                        continue
                    for key in ("reason", "text"):
                        value = override.get(key)
                        if isinstance(value, str):
                            yield (
                                (
                                    f"$.meaningUnits[{unit_index}].occurrences[{occ_index}]."
                                    f"semanticOverride.{key}"
                                ),
                                value,
                                "override",
                            )

    phrases = document.get("phrases")
    if isinstance(phrases, list):
        for phrase_index, phrase in enumerate(phrases):
            if not isinstance(phrase, dict):
                continue
            for key in ("gloss", "explanation", "notes"):
                value = phrase.get(key)
                if isinstance(value, str):
                    yield f"$.phrases[{phrase_index}].{key}", value, key
            alternatives = phrase.get("alternatives")
            if isinstance(alternatives, list):
                for alternative_index, value in enumerate(alternatives):
                    if isinstance(value, str):
                        yield (
                            f"$.phrases[{phrase_index}].alternatives[{alternative_index}]",
                            value,
                            "alternatives",
                        )
            occurrences = phrase.get("occurrences")
            if isinstance(occurrences, list):
                for occ_index, occurrence in enumerate(occurrences):
                    if not isinstance(occurrence, dict):
                        continue
                    override = occurrence.get("semanticOverride")
                    if not isinstance(override, dict):
                        continue
                    for key in (
                        "reason",
                        "explanation",
                        "gloss",
                        "notes",
                    ):
                        value = override.get(key)
                        if isinstance(value, str):
                            yield (
                                (
                                    f"$.phrases[{phrase_index}].occurrences[{occ_index}]."
                                    f"semanticOverride.{key}"
                                ),
                                value,
                                "override",
                            )
                    override_alternatives = override.get("alternatives")
                    if isinstance(override_alternatives, list):
                        for alternative_index, value in enumerate(override_alternatives):
                            if isinstance(value, str):
                                yield (
                                    (
                                        f"$.phrases[{phrase_index}].occurrences[{occ_index}]."
                                        f"semanticOverride.alternatives[{alternative_index}]"
                                    ),
                                    value,
                                    "alternatives",
                                )

    for collection, explanation_key in (
        ("relations", "explanation"),
        ("grammarNotes", "explanation"),
        ("uncertainties", "message"),
    ):
        values = document.get(collection)
        if not isinstance(values, list):
            continue
        for index, item in enumerate(values):
            if not isinstance(item, dict):
                continue
            value = item.get(explanation_key)
            if isinstance(value, str):
                yield f"$.{collection}[{index}].{explanation_key}", value, explanation_key
            alternatives = item.get("alternatives")
            if isinstance(alternatives, list):
                for alternative_index, alternative in enumerate(alternatives):
                    if isinstance(alternative, str):
                        yield (
                            f"$.{collection}[{index}].alternatives[{alternative_index}]",
                            alternative,
                            "alternatives",
                        )


def validate_learner_language_v2(
    document: Any,
    expected_language: str | None,
) -> list[Issue]:
    expected = (
        expected_language.strip().lower()
        if isinstance(expected_language, str)
        else None
    )
    if expected_language is not None and not expected:
        return [Issue("missing_learner_language", "$", "Expected a learner language.")]
    actual = learner_language_of(document)
    issues: list[Issue] = []
    if actual is None:
        issues.append(
            Issue(
                "missing_learner_language",
                "$.extensions.learnerLanguage",
                "Schema v2 requires extensions.learnerLanguage.",
            )
        )
    elif expected is not None and actual != expected:
        issues.append(
            Issue(
                "learner_language_mismatch",
                "$.extensions.learnerLanguage",
                f"Expected learner language {expected}, got {actual}.",
            )
        )
    if not isinstance(document, dict):
        return issues

    target_language = expected or actual
    for path, text, kind in _iter_learner_text(document):
        if not text.strip():
            issues.append(
                Issue(
                    "blank_learner_text",
                    path,
                    "Learner-facing prose must not be blank.",
                )
            )
            continue
        if (
            target_language is None
            or target_language.split("-", 1)[0] not in CJK_LEARNER_LANGUAGES
        ):
            continue
        minimum_length = 1 if kind in {"gloss", "override"} else 12
        if (
            len(text) >= minimum_length
            and _is_ascii_prose(text)
            and not _has_cjk(text)
        ):
            issues.append(
                Issue(
                    "learner_text_not_in_target_language",
                    path,
                    (
                        f"Learner-facing prose must use {target_language}, "
                        "not English-only text."
                    ),
                )
            )
    return issues


def _normalized_line(line: dict[str, Any]) -> NormalizedLine:
    return NormalizedLine(
        line_id=line["lineId"],
        source_order=line["sourceOrder"],
        raw_text=line["text"],
    )


def _normalization_maps(
    raw_text: str,
) -> tuple[str, tuple[int | None, ...], dict[int, tuple[int, ...]]]:
    normalized = _nfc(raw_text)
    raw_to_normalized: list[int | None] = [None] * (len(raw_text) + 1)
    normalized_to_raw: dict[int, list[int]] = {}
    for raw_end in range(len(raw_text) + 1):
        normalized_prefix = _nfc(raw_text[:raw_end])
        if not normalized.startswith(normalized_prefix):
            continue
        normalized_end = len(normalized_prefix)
        raw_to_normalized[raw_end] = normalized_end
        normalized_to_raw.setdefault(normalized_end, []).append(raw_end)
    return (
        normalized,
        tuple(raw_to_normalized),
        {key: tuple(values) for key, values in normalized_to_raw.items()},
    )


def _build_block_view(
    block: dict[str, Any],
    lines_by_id: dict[str, dict[str, Any]],
    normalized_by_id: dict[str, NormalizedLine],
) -> BlockView | None:
    line_ids = tuple(
        line_id
        for line_id in block.get("lineIds", [])
        if isinstance(line_id, str) and line_id in lines_by_id
    )
    if len(line_ids) != len(block.get("lineIds", [])):
        return None
    normalized_lines = tuple(normalized_by_id[line_id] for line_id in line_ids)
    raw_parts: list[str] = []
    line_raw_ranges: dict[str, tuple[int, int]] = {}
    line_indexes: dict[str, int] = {}
    cursor = 0
    for index, line in enumerate(normalized_lines):
        start = cursor
        cursor += len(line.raw_text)
        line_raw_ranges[line.line_id] = (start, cursor)
        line_indexes[line.line_id] = index
        raw_parts.append(line.raw_text)
    raw_text = "".join(raw_parts)
    normalized_text, raw_to_normalized, normalized_to_raw = _normalization_maps(
        raw_text
    )
    return BlockView(
        block_id=block["blockId"],
        line_ids=line_ids,
        normalized_text=normalized_text,
        raw_text=raw_text,
        raw_to_normalized=raw_to_normalized,
        normalized_to_raw=normalized_to_raw,
        line_raw_ranges=line_raw_ranges,
        line_indexes=line_indexes,
        lines=normalized_lines,
    )


def _resolve_segment_range(
    segments: Any,
    block_id: str,
    view: BlockView,
    lines_by_id: dict[str, dict[str, Any]],
    path: str,
    issues: list[Issue],
    code_prefix: str = "shared-occurrence",
) -> OccurrenceRange | None:
    if not isinstance(segments, list) or not segments:
        issues.append(Issue(f"{code_prefix}-incomplete", path, "Source segments are required."))
        return None

    resolved: list[tuple[str, int, int, str, int]] = []
    for index, segment in enumerate(segments):
        segment_path = f"{path}[{index}]"
        if not isinstance(segment, dict):
            issues.append(Issue("invalid_source_segment", segment_path, "Segment must be an object."))
            return None
        line_id = segment.get("lineId")
        start = segment.get("start")
        end = segment.get("end")
        text = segment.get("text")
        if not isinstance(line_id, str) or line_id not in lines_by_id:
            issues.append(Issue("unresolved_line", segment_path + ".lineId", "Unknown source line."))
            return None
        if line_id not in view.line_raw_ranges:
            issues.append(
                Issue(
                    "occurrence_block_mismatch",
                    segment_path + ".lineId",
                    "Occurrence segment is outside its block.",
                )
            )
            return None
        line = lines_by_id[line_id]
        line_text = line.get("text")
        if (
            not isinstance(line_text, str)
            or not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < 0
            or end <= start
            or end > len(line_text)
        ):
            issues.append(
                Issue(
                    "invalid_codepoint_span",
                    segment_path,
                    "Segment is outside the source line's Unicode code-point range.",
                )
            )
            return None
        if not isinstance(text, str) or line_text[start:end] != text:
            issues.append(
                Issue(
                    "segment_text_mismatch",
                    segment_path + ".text",
                    "Segment text does not equal the exact source substring.",
                )
            )
            return None
        source_order = line.get("sourceOrder")
        if not isinstance(source_order, int) or isinstance(source_order, bool):
            issues.append(
                Issue("invalid_source_order", segment_path + ".lineId", "Line has no source order.")
            )
            return None
        resolved.append((line_id, start, end, text, source_order))

    if not resolved:
        return None

    expected_next_order = resolved[0][4]
    previous_line_id = resolved[0][0]
    previous_raw_end = resolved[0][2]
    for index, (line_id, start, end, _text, source_order) in enumerate(resolved[1:], start=1):
        expected_next_order += 1
        if source_order != expected_next_order:
            issues.append(
                Issue(
                    "source_segments_not_contiguous",
                    f"{path}[{index}]",
                    "Occurrence segments must use immediately consecutive source lines.",
                )
            )
            return None
        previous_line_text = lines_by_id[previous_line_id].get("text")
        if (
            not isinstance(previous_line_text, str)
            or previous_raw_end != len(previous_line_text)
            or start != 0
        ):
            issues.append(
                Issue(
                    "source_segments_not_contiguous",
                    f"{path}[{index}]",
                    "A line-crossing occurrence must consume the previous line and start at zero.",
                )
            )
            return None
        previous_line_id = line_id
        previous_raw_end = end

    first_line_raw_start, _ = view.line_raw_ranges[resolved[0][0]]
    last_line_raw_start, _ = view.line_raw_ranges[resolved[-1][0]]
    raw_start = first_line_raw_start + resolved[0][1]
    raw_end = last_line_raw_start + resolved[-1][2]
    if raw_end <= raw_start:
        return None
    normalized_start = view.raw_to_normalized[raw_start]
    normalized_end = view.raw_to_normalized[raw_end]
    if (
        normalized_start is None
        or normalized_end is None
        or normalized_end <= normalized_start
    ):
        issues.append(
            Issue(
                "invalid_normalization_boundary",
                path,
                "Occurrence boundaries cannot be mapped to NFC source code points.",
            )
        )
        return None
    source_text = "".join(item[3] for item in resolved)
    expected_normalized = view.normalized_text[normalized_start:normalized_end]
    if expected_normalized != _nfc(source_text):
        issues.append(
            Issue(
                "segment_text_mismatch",
                path,
                "Occurrence text does not match its normalized source span.",
            )
        )
        return None
    return OccurrenceRange(
        owner_id="",
        occurrence_id="",
        block_id=block_id,
        start=normalized_start,
        end=normalized_end,
        normalized_text=expected_normalized,
        line_ids=tuple(item[0] for item in resolved),
    )


def _is_metadata_block(block: dict[str, Any]) -> bool:
    labels = block.get("labels")
    if not isinstance(labels, list):
        return False
    return any(
        isinstance(label, dict)
        and label.get("namespace") == "discourse"
        and label.get("key") == "metadata"
        for label in labels
    )


def _check_labels(labels: Any, path: str, issues: list[Issue]) -> None:
    if not isinstance(labels, list):
        return
    for index, label in enumerate(labels):
        if not isinstance(label, dict):
            continue
        if not isinstance(label.get("namespace"), str) or not label.get("namespace"):
            issues.append(
                Issue("missing_namespace", f"{path}[{index}].namespace", "Missing label namespace.")
            )
        if not isinstance(label.get("key"), str) or not label.get("key"):
            issues.append(
                Issue("missing_label_key", f"{path}[{index}].key", "Missing label key.")
            )


def _validate_override_rules(document: dict[str, Any], issues: list[Issue]) -> None:
    meaning_units = document.get("meaningUnits")
    if isinstance(meaning_units, list):
        for unit_index, unit in enumerate(meaning_units):
            if not isinstance(unit, dict):
                continue
            occurrences = unit.get("occurrences")
            if not isinstance(occurrences, list):
                continue
            for occurrence_index, occurrence in enumerate(occurrences):
                if not isinstance(occurrence, dict):
                    continue
                override = occurrence.get("semanticOverride")
                if not isinstance(override, dict):
                    continue
                for key in ("reason", "text"):
                    value = override.get(key)
                    if isinstance(value, str) and not value.strip():
                        issues.append(
                            Issue(
                                f"empty_override_{key}",
                                (
                                    f"$.meaningUnits[{unit_index}].occurrences"
                                    f"[{occurrence_index}].semanticOverride.{key}"
                                ),
                                f"Occurrence override {key} must not be blank.",
                            )
                        )

    phrases = document.get("phrases")
    if isinstance(phrases, list):
        for phrase_index, phrase in enumerate(phrases):
            if not isinstance(phrase, dict):
                continue
            occurrences = phrase.get("occurrences")
            if not isinstance(occurrences, list):
                continue
            for occurrence_index, occurrence in enumerate(occurrences):
                if not isinstance(occurrence, dict):
                    continue
                override = occurrence.get("semanticOverride")
                if not isinstance(override, dict):
                    continue
                reason = override.get("reason")
                if isinstance(reason, str) and not reason.strip():
                    issues.append(
                        Issue(
                            "empty_override_reason",
                            (
                                f"$.phrases[{phrase_index}].occurrences"
                                f"[{occurrence_index}].semanticOverride.reason"
                            ),
                            "Occurrence override reason must not be blank.",
                        )
                    )


def _check_html_and_paths(document: dict[str, Any], issues: list[Issue]) -> None:
    def iter_strings(value: Any, path: str = "$") -> Iterable[tuple[str, str]]:
        if isinstance(value, str):
            yield path, value
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from iter_strings(item, f"{path}[{index}]")
        elif isinstance(value, dict):
            for key, item in value.items():
                yield from iter_strings(item, f"{path}.{key}")

    def iter_keys(value: Any, path: str = "$") -> Iterable[tuple[str, str]]:
        if isinstance(value, dict):
            for key, item in value.items():
                yield path, key
                yield from iter_keys(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from iter_keys(item, f"{path}[{index}]")

    for path, text in iter_strings(document):
        lower_path = path.lower()
        if (
            "<!doctype" in text.lower()
            or "<![cdata[" in text.lower()
            or "<?xml" in text.lower()
            or HTML_COMMENT_RE.search(text)
            or HTML_DECL_RE.search(text)
            or HTML_TAG_RE.search(text)
            or HTML_ENTITY_RE.search(text)
        ):
            issues.append(Issue("html_content", path, "Inline HTML is not allowed."))
        if path.endswith(".lrcFile") and ("/" in text or "\\" in text):
            issues.append(Issue("absolute_path", path, "lrcFile must be a basename."))
        pathish = "path" in lower_path or "file" in lower_path
        if (
            WINDOWS_ABSOLUTE_PATH_RE.search(text)
            or FILE_URI_RE.search(text)
            or text.startswith(("\\\\", "//"))
            or (pathish and ABSOLUTE_PATH_RE.match(text))
        ):
            issues.append(Issue("absolute_path", path, "Absolute paths are not allowed."))
    for path, key in iter_keys(document):
        if FORBIDDEN_TRANSLATION_KEY_RE.search(key) and key != "kind":
            issues.append(
                Issue(
                    "per_line_translation",
                    f"{path}.{key}",
                    "Translation-bearing fields are not allowed in the analysis sidecar.",
                )
            )


def _check_source_filename(source: Any, issues: list[Issue]) -> None:
    if not isinstance(source, dict):
        return
    issues.extend(lrc_basename_issues(source.get("lrcFile"), "$.source.lrcFile"))


def _collect_global_ids(document: dict[str, Any], issues: list[Issue]) -> None:
    seen: dict[str, str] = {}
    collections = (
        ("lines", "lineId", False),
        ("repetitionGroups", "repetitionId", False),
        ("repetitionGroups", "occurrences", True),
        ("meaningUnits", "meaningUnitId", False),
        ("meaningUnits", "occurrences", True),
        ("blocks", "blockId", False),
        ("phrases", "phraseId", False),
        ("phrases", "occurrences", True),
        ("relations", "relationId", False),
        ("grammarNotes", "noteId", False),
        ("uncertainties", "uncertaintyId", False),
    )
    for collection, id_key, nested in collections:
        values = document.get(collection)
        if not isinstance(values, list):
            continue
        for index, entity in enumerate(values):
            if not isinstance(entity, dict):
                continue
            items = entity.get(id_key) if nested else [entity]
            if not isinstance(items, list):
                continue
            for child_index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                entity_id = item.get("occurrenceId") if nested else item.get(id_key)
                if not isinstance(entity_id, str):
                    continue
                path = (
                    f"$.{collection}[{index}].{id_key}[{child_index}].occurrenceId"
                    if nested
                    else f"$.{collection}[{index}].{id_key}"
                )
                if ID_PATTERN.fullmatch(entity_id) is None:
                    issues.append(Issue("invalid_id", path, "Entity ID is not in canonical form."))
                if entity_id in seen:
                    issues.append(
                        Issue(
                            "duplicate_id",
                            path,
                            f"Entity ID already used by {seen[entity_id]}.",
                        )
                    )
                else:
                    seen[entity_id] = collection


def _validate_source_and_lines(
    document: dict[str, Any],
    projection: dict[str, Any] | None,
    expected_lrc_file: str | None,
    issues: list[Issue],
) -> tuple[dict[str, dict[str, Any]], dict[str, NormalizedLine]]:
    source = document.get("source")
    _check_source_filename(source, issues)
    if not isinstance(source, dict):
        return {}, {}

    source_languages = source.get("sourceLanguages")
    language_set: set[str] = set()
    if isinstance(source_languages, list):
        if len({item for item in source_languages if isinstance(item, str)}) != len(
            source_languages
        ):
            issues.append(
                Issue(
                    "duplicate_language",
                    "$.source.sourceLanguages",
                    "sourceLanguages must not contain duplicates.",
                )
            )
        language_set = {item for item in source_languages if isinstance(item, str)}

    lrc_file = source.get("lrcFile")
    if isinstance(lrc_file, str):
        if "/" in lrc_file or "\\" in lrc_file:
            issues.append(
                Issue("absolute_path", "$.source.lrcFile", "lrcFile must be a basename.")
            )
        if expected_lrc_file is not None and lrc_file != expected_lrc_file:
            issues.append(
                Issue(
                    "lrc_file_mismatch",
                    "$.source.lrcFile",
                    f"Expected {expected_lrc_file}, got {lrc_file}.",
                )
            )

    song_id = source.get("songId")
    if isinstance(song_id, str) and not song_id.strip():
        issues.append(
            Issue("invalid_song_id", "$.source.songId", "songId must not be blank.")
        )

    lines_list = document.get("lines")
    if not isinstance(lines_list, list):
        return {}, {}
    if source.get("lineCount") != len(lines_list):
        issues.append(
            Issue(
                "line_count_mismatch",
                "$.source.lineCount",
                "source.lineCount must equal the number of source lines.",
            )
        )
    lines_by_id: dict[str, dict[str, Any]] = {}
    normalized_by_id: dict[str, NormalizedLine] = {}
    source_orders: set[int] = set()
    line_times: set[int] = set()
    array_orders: list[int] = []
    for index, line in enumerate(lines_list):
        if not isinstance(line, dict):
            continue
        path = f"$.lines[{index}]"
        line_id = line.get("lineId")
        if isinstance(line_id, str):
            if line_id in lines_by_id:
                issues.append(
                    Issue("duplicate_id", path + ".lineId", f"Duplicate lineId: {line_id}")
                )
            else:
                lines_by_id[line_id] = line
        source_order = line.get("sourceOrder")
        if isinstance(source_order, int) and not isinstance(source_order, bool):
            array_orders.append(source_order)
            if source_order in source_orders:
                issues.append(
                    Issue("duplicate_source_order", path + ".sourceOrder", "Duplicate sourceOrder.")
                )
            source_orders.add(source_order)
        time_ms = line.get("timeMs")
        if isinstance(time_ms, int) and not isinstance(time_ms, bool):
            if time_ms in line_times:
                issues.append(
                    Issue(
                        "duplicate_timestamp",
                        path + ".timeMs",
                        "Source lines must contain unique projected timestamps.",
                    )
                )
            line_times.add(time_ms)
        text = line.get("text")
        language = line.get("language")
        if isinstance(language, str) and language_set and language not in language_set:
            issues.append(
                Issue(
                    "unknown_line_language",
                    path + ".language",
                    "Line language is absent from sourceLanguages.",
                )
            )
        if not isinstance(text, str) or not text:
            continue
        if line.get("fingerprint") != grammar_line_fingerprint(text):
            issues.append(
                Issue(
                    "invalid_line_fingerprint",
                    path + ".fingerprint",
                    "Line fingerprint is not canonical for its source text.",
                )
            )
        if (
            isinstance(song_id, str)
            and song_id.strip()
            and isinstance(line_id, str)
            and isinstance(time_ms, int)
            and not isinstance(time_ms, bool)
            and line_id != grammar_line_id(song_id, time_ms, 0, text)
        ):
            issues.append(
                Issue(
                    "invalid_line_id",
                    path + ".lineId",
                    "Line ID is not canonical for its song, timestamp, and text.",
                )
            )
        if isinstance(line_id, str):
            normalized_by_id[line_id] = _normalized_line(line)
        spans = line.get("languageSpans")
        if isinstance(spans, list):
            previous_end = 0
            for span_index, span in enumerate(spans):
                if not isinstance(span, dict):
                    continue
                start = span.get("start")
                end = span.get("end")
                span_path = f"{path}.languageSpans[{span_index}]"
                if (
                    not isinstance(start, int)
                    or isinstance(start, bool)
                    or not isinstance(end, int)
                    or isinstance(end, bool)
                    or start < 0
                    or end <= start
                    or end > len(text)
                ):
                    issues.append(
                        Issue(
                            "invalid_codepoint_span",
                            span_path,
                            "Language span is outside the line's Unicode code-point range.",
                        )
                    )
                    continue
                if start < previous_end:
                    issues.append(
                        Issue(
                            "overlapping_language_span",
                            span_path,
                            "Language spans on a line must not overlap.",
                        )
                    )
                previous_end = max(previous_end, end)
                span_language = span.get("language")
                if language_set and span_language not in language_set:
                    issues.append(
                        Issue(
                            "unknown_span_language",
                            span_path + ".language",
                            "Span language is absent from sourceLanguages.",
                        )
                    )

    if array_orders != list(range(len(lines_list))):
        issues.append(
            Issue(
                "non_canonical_line_order",
                "$.lines",
                "Lines must be stored in canonical sourceOrder sequence.",
            )
        )

    if projection is not None:
        if source.get("songId") != projection.get("songId"):
            issues.append(
                Issue(
                    "source_song_mismatch",
                    "$.source.songId",
                    "Sidecar songId does not match the source projection.",
                )
            )
        if source.get("lrcProjectionHash") != projection.get("lrcProjectionHash"):
            issues.append(
                Issue(
                    "stale_projection_hash",
                    "$.source.lrcProjectionHash",
                    "Sidecar projection hash does not match the accepted projection.",
                )
            )
        if source.get("lineCount") != projection.get("lineCount"):
            issues.append(
                Issue(
                    "line_count_mismatch",
                    "$.source.lineCount",
                    "Sidecar lineCount does not match the source projection.",
                )
            )
        if projection.get("lrcFile") is not None and source.get("lrcFile") != projection.get(
            "lrcFile"
        ):
            issues.append(
                Issue(
                    "lrc_file_mismatch",
                    "$.source.lrcFile",
                    "Sidecar lrcFile does not match the source projection.",
                )
            )
        projected_lines = projection.get("lines", [])
        projected_by_id = {
            item["lineId"]: item for item in projected_lines if isinstance(item, dict)
        }
        if len(lines_list) != len(projected_lines):
            issues.append(
                Issue(
                    "line_coverage",
                    "$.lines",
                    f"Expected {len(projected_lines)} lines, got {len(lines_list)}.",
                )
            )
        for index, projected in enumerate(projected_lines):
            line_id = projected["lineId"]
            actual = lines_by_id.get(line_id)
            if actual is None:
                issues.append(
                    Issue(
                        "line_coverage",
                        f"$.lines[{index}]",
                        f"Missing projected source line: {line_id}",
                    )
                )
                continue
            for field in ("lineId", "sourceOrder", "timeMs", "text", "fingerprint"):
                if actual.get(field) != projected[field]:
                    issues.append(
                        Issue(
                            "stale_source_projection",
                            f"$.lines[{index}].{field}",
                            f"Line {field} does not match the source projection.",
                        )
                    )
            if actual.get("role") != "text":
                issues.append(
                    Issue(
                        "invalid_line_role",
                        f"$.lines[{index}].role",
                        "Only source text lines are allowed.",
                    )
                )
        for line_id, actual in lines_by_id.items():
            if line_id not in projected_by_id:
                issues.append(
                    Issue(
                        "unknown_source_line",
                        f"$.lines[{actual.get('sourceOrder', '?')}]",
                        f"Sidecar line is absent from the source projection: {line_id}",
                    )
                )
    else:
        ordered: list[int] = []
        for line in lines_by_id.values():
            source_order = line.get("sourceOrder")
            if isinstance(source_order, int) and not isinstance(source_order, bool):
                ordered.append(source_order)
        ordered.sort()
        if ordered != list(range(len(lines_list))):
            issues.append(
                Issue(
                    "non_canonical_source_order",
                    "$.lines",
                    "Line sourceOrder values must be unique and contiguous from zero.",
                )
            )
    return lines_by_id, normalized_by_id


def _validate_blocks(
    document: dict[str, Any],
    lines_by_id: dict[str, dict[str, Any]],
    normalized_by_id: dict[str, NormalizedLine],
    issues: list[Issue],
) -> tuple[
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, BlockView],
]:
    blocks_list = [
        block
        for block in document.get("blocks", [])
        if isinstance(block, dict)
    ] if isinstance(document.get("blocks"), list) else []
    blocks_by_id: dict[str, dict[str, Any]] = {}
    views: dict[str, BlockView] = {}
    covered_lines: set[str] = set()

    for index, block in enumerate(blocks_list):
        path = f"$.blocks[{index}]"
        block_id = block.get("blockId")
        if isinstance(block_id, str):
            if block_id in blocks_by_id:
                issues.append(
                    Issue("duplicate_id", path + ".blockId", f"Duplicate blockId: {block_id}")
                )
            else:
                blocks_by_id[block_id] = block
        _check_labels(block.get("labels"), path + ".labels", issues)

        line_ids = block.get("lineIds")
        resolved_lines: list[dict[str, Any]] = []
        block_line_ids: list[str] = []
        if not isinstance(line_ids, list) or not line_ids:
            issues.append(Issue("empty_block", path + ".lineIds", "Block must reference a line."))
            continue
        if _has_duplicates(line_ids):
            issues.append(
                Issue(
                    "duplicate_block_line",
                    path + ".lineIds",
                    "Block lineIds must be unique.",
                )
            )
        for line_index, line_id in enumerate(line_ids):
            if not isinstance(line_id, str):
                continue
            line = lines_by_id.get(line_id)
            if line is None:
                issues.append(
                    Issue(
                        "unresolved_line",
                        f"{path}.lineIds[{line_index}]",
                        f"Unknown source line: {line_id}",
                    )
                )
                continue
            block_line_ids.append(line_id)
            resolved_lines.append(line)
            covered_lines.add(line_id)

        source_orders: list[int] = []
        for line in resolved_lines:
            source_order = line.get("sourceOrder")
            if isinstance(source_order, int) and not isinstance(source_order, bool):
                source_orders.append(source_order)
        if source_orders != sorted(source_orders):
            issues.append(
                Issue(
                    "non_monotonic_block",
                    path + ".lineIds",
                    "Block lineIds do not follow sourceOrder.",
                )
            )
        line_times: list[int] = []
        for line in resolved_lines:
            line_time = line.get("timeMs")
            if isinstance(line_time, int) and not isinstance(line_time, bool):
                line_times.append(line_time)
        if line_times != sorted(line_times):
            issues.append(
                Issue(
                    "non_monotonic_block",
                    path + ".lineIds",
                    "Block line timestamps decrease as source order advances.",
                )
            )
        start_time = block.get("startTimeMs")
        end_time = block.get("endTimeMs")
        if (
            isinstance(start_time, int)
            and not isinstance(start_time, bool)
            and isinstance(end_time, int)
            and not isinstance(end_time, bool)
        ):
            if end_time < start_time:
                issues.append(
                    Issue("invalid_time_range", path, "Block endTimeMs precedes startTimeMs.")
                )
            for line in resolved_lines:
                line_time = line.get("timeMs")
                if (
                    isinstance(line_time, int)
                    and not isinstance(line_time, bool)
                    and not (start_time <= line_time <= end_time)
                ):
                    issues.append(
                        Issue(
                            "block_time_coverage",
                            path,
                            f"Block time range excludes line {line.get('lineId')}.",
                        )
                    )
        if isinstance(block_id, str) and len(block_line_ids) == len(line_ids):
            view = _build_block_view(block, lines_by_id, normalized_by_id)
            if view is None:
                issues.append(
                    Issue(
                        "invalid_block_lines",
                        path + ".lineIds",
                        "Block line references cannot be projected.",
                    )
                )
            else:
                views[block_id] = view

    for line_id in lines_by_id:
        if line_id not in covered_lines:
            issues.append(
                Issue(
                    "uncovered_source_line",
                    "$.blocks",
                    f"Source line is not covered by any block: {line_id}",
                )
            )
    return blocks_list, blocks_by_id, views


def _occurrence_range(
    occurrence: dict[str, Any],
    view: BlockView,
    lines_by_id: dict[str, dict[str, Any]],
    path: str,
    issues: list[Issue],
) -> OccurrenceRange | None:
    block_id = occurrence.get("blockId")
    if not isinstance(block_id, str) or block_id != view.block_id:
        issues.append(
            Issue(
                "occurrence_block_mismatch",
                path + ".blockId",
                "Occurrence blockId does not match its block.",
            )
        )
        return None
    resolved = _resolve_segment_range(
        occurrence.get("sourceSegments"),
        block_id,
        view,
        lines_by_id,
        path + ".sourceSegments",
        issues,
    )
    if resolved is None:
        return None
    return OccurrenceRange(
        owner_id="",
        occurrence_id=str(occurrence.get("occurrenceId", "")),
        block_id=block_id,
        start=resolved.start,
        end=resolved.end,
        normalized_text=resolved.normalized_text,
        line_ids=resolved.line_ids,
    )


def _validate_repetition_groups(
    document: dict[str, Any],
    blocks_by_id: dict[str, dict[str, Any]],
    views: dict[str, BlockView],
    lines_by_id: dict[str, dict[str, Any]],
    issues: list[Issue],
) -> dict[str, dict[str, OccurrenceRange]]:
    groups = document.get("repetitionGroups")
    if not isinstance(groups, list):
        return {}
    result: dict[str, dict[str, OccurrenceRange]] = {}
    for group_index, group in enumerate(groups):
        if not isinstance(group, dict):
            continue
        path = f"$.repetitionGroups[{group_index}]"
        repetition_id = group.get("repetitionId")
        group_ranges: dict[str, OccurrenceRange] = {}
        group_text: str | None = None
        occurrences = group.get("occurrences")
        if not isinstance(occurrences, list) or len(occurrences) < 2:
            issues.append(
                Issue(
                    "repetition_occurrence_count",
                    path + ".occurrences",
                    "A repetition group requires at least two occurrences.",
                )
            )
            continue
        previous_anchor_order = -1
        for occ_index, occurrence in enumerate(occurrences):
            occ_path = f"{path}.occurrences[{occ_index}]"
            if not isinstance(occurrence, dict):
                continue
            block_id = occurrence.get("blockId")
            block = blocks_by_id.get(block_id) if isinstance(block_id, str) else None
            view = views.get(block_id) if isinstance(block_id, str) else None
            if block is None or view is None:
                issues.append(
                    Issue(
                        "unresolved_block",
                        occ_path + ".blockId",
                        f"Unknown repetition block: {block_id}",
                    )
                )
                continue
            assert isinstance(block_id, str)
            if _is_metadata_block(block):
                issues.append(
                    Issue(
                        "metadata_repetition_block",
                        occ_path + ".blockId",
                        "Metadata blocks cannot participate in repetition groups.",
                    )
                )
            if block_id in group_ranges:
                issues.append(
                    Issue(
                        "repetition_duplicate_block",
                        occ_path + ".blockId",
                        "A repetition group may have at most one occurrence per block.",
                    )
                )
                continue
            resolved = _occurrence_range(
                occurrence, view, lines_by_id, occ_path, issues
            )
            if resolved is None:
                continue
            if len(resolved.normalized_text) < MIN_REPETITION_CODE_POINTS:
                issues.append(
                    Issue(
                        "repetition_too_short",
                        occ_path,
                        f"Repetition groups require at least {MIN_REPETITION_CODE_POINTS} code points.",
                    )
                )
            if group_text is None:
                group_text = resolved.normalized_text
            elif resolved.normalized_text != group_text:
                issues.append(
                    Issue(
                        "repetition_text_mismatch",
                        occ_path,
                        "All repetition occurrences must have identical NFC text.",
                    )
                )
            first_line_id = occurrence.get("sourceSegments", [{}])[0].get("lineId")
            first_line = lines_by_id.get(first_line_id) if isinstance(first_line_id, str) else None
            anchor_order = first_line.get("sourceOrder") if first_line else -1
            if isinstance(anchor_order, int):
                if anchor_order < previous_anchor_order:
                    issues.append(
                        Issue(
                            "repetition_occurrence_order",
                            occ_path,
                            "Repetition occurrences must be ordered by anchor source order.",
                        )
                    )
                previous_anchor_order = anchor_order
            group_ranges[block_id] = OccurrenceRange(
                owner_id=str(repetition_id or ""),
                occurrence_id=resolved.occurrence_id,
                block_id=block_id,
                start=resolved.start,
                end=resolved.end,
                normalized_text=resolved.normalized_text,
                line_ids=resolved.line_ids,
            )
        if isinstance(repetition_id, str):
            result[repetition_id] = group_ranges
    return result


def _validate_meaning_units(
    document: dict[str, Any],
    blocks_by_id: dict[str, dict[str, Any]],
    views: dict[str, BlockView],
    lines_by_id: dict[str, dict[str, Any]],
    issues: list[Issue],
) -> tuple[dict[str, dict[str, Any]], dict[str, OccurrenceRange]]:
    values = document.get("meaningUnits")
    if not isinstance(values, list):
        return {}, {}
    units: dict[str, dict[str, Any]] = {}
    occurrence_ranges: dict[str, OccurrenceRange] = {}
    for unit_index, unit in enumerate(values):
        if not isinstance(unit, dict):
            continue
        path = f"$.meaningUnits[{unit_index}]"
        unit_id = unit.get("meaningUnitId")
        if isinstance(unit_id, str):
            units[unit_id] = unit
        _check_labels(unit.get("labels"), path + ".labels", issues)
        occurrences = unit.get("occurrences")
        if not isinstance(occurrences, list) or not occurrences:
            issues.append(
                Issue(
                    "empty_meaning_occurrences",
                    path + ".occurrences",
                    "A meaning unit requires at least one occurrence.",
                )
            )
            continue
        for occ_index, occurrence in enumerate(occurrences):
            occ_path = f"{path}.occurrences[{occ_index}]"
            if not isinstance(occurrence, dict):
                continue
            block_id = occurrence.get("blockId")
            view = views.get(block_id) if isinstance(block_id, str) else None
            if block_id not in blocks_by_id or view is None:
                issues.append(
                    Issue(
                        "unresolved_block",
                        occ_path + ".blockId",
                        f"Unknown meaning-unit block: {block_id}",
                    )
                )
                continue
            resolved = _occurrence_range(
                occurrence, view, lines_by_id, occ_path, issues
            )
            if resolved is None:
                continue
            occurrence_id = occurrence.get("occurrenceId")
            if isinstance(occurrence_id, str):
                occurrence_ranges[occurrence_id] = OccurrenceRange(
                    owner_id=str(unit_id or ""),
                    occurrence_id=occurrence_id,
                    block_id=resolved.block_id,
                    start=resolved.start,
                    end=resolved.end,
                    normalized_text=resolved.normalized_text,
                    line_ids=resolved.line_ids,
                )
    return units, occurrence_ranges


def _validate_phrases(
    document: dict[str, Any],
    blocks_by_id: dict[str, dict[str, Any]],
    views: dict[str, BlockView],
    lines_by_id: dict[str, dict[str, Any]],
    issues: list[Issue],
) -> tuple[dict[str, dict[str, Any]], dict[str, OccurrenceRange]]:
    values = document.get("phrases")
    if not isinstance(values, list):
        return {}, {}
    phrases: dict[str, dict[str, Any]] = {}
    occurrence_ranges: dict[str, OccurrenceRange] = {}
    for phrase_index, phrase in enumerate(values):
        if not isinstance(phrase, dict):
            continue
        path = f"$.phrases[{phrase_index}]"
        phrase_id = phrase.get("phraseId")
        if isinstance(phrase_id, str):
            phrases[phrase_id] = phrase
        _check_labels(phrase.get("labels"), path + ".labels", issues)
        occurrences = phrase.get("occurrences")
        if not isinstance(occurrences, list) or not occurrences:
            issues.append(
                Issue(
                    "empty_phrase_occurrences",
                    path + ".occurrences",
                    "A phrase requires at least one occurrence.",
                )
            )
            continue
        surface = phrase.get("surface")
        for occ_index, occurrence in enumerate(occurrences):
            occ_path = f"{path}.occurrences[{occ_index}]"
            if not isinstance(occurrence, dict):
                continue
            block_id = occurrence.get("blockId")
            view = views.get(block_id) if isinstance(block_id, str) else None
            if block_id not in blocks_by_id or view is None:
                issues.append(
                    Issue(
                        "unresolved_block",
                        occ_path + ".blockId",
                        f"Unknown phrase block: {block_id}",
                    )
                )
                continue
            resolved = _occurrence_range(
                occurrence, view, lines_by_id, occ_path, issues
            )
            if resolved is None:
                continue
            source_text = "".join(
                segment.get("text", "")
                for segment in occurrence.get("sourceSegments", [])
                if isinstance(segment, dict)
            )
            if (
                not isinstance(surface, str)
                or _nfc(surface) != _nfc(source_text)
                or resolved.normalized_text != _nfc(source_text)
            ):
                issues.append(
                    Issue(
                        "surface_mismatch",
                        occ_path + ".sourceSegments",
                        "Phrase surface must be NFC-equal to every occurrence source span.",
                    )
                )
            override = occurrence.get("semanticOverride")
            if isinstance(override, dict):
                _check_labels(override.get("labels"), occ_path + ".semanticOverride.labels", issues)
            occurrence_id = occurrence.get("occurrenceId")
            if isinstance(occurrence_id, str):
                occurrence_ranges[occurrence_id] = OccurrenceRange(
                    owner_id=str(phrase_id or ""),
                    occurrence_id=occurrence_id,
                    block_id=resolved.block_id,
                    start=resolved.start,
                    end=resolved.end,
                    normalized_text=resolved.normalized_text,
                    line_ids=resolved.line_ids,
                )
    return phrases, occurrence_ranges


def _validate_block_meanings(
    blocks_list: Sequence[dict[str, Any]],
    views: dict[str, BlockView],
    meaning_units: dict[str, dict[str, Any]],
    meaning_ranges: dict[str, OccurrenceRange],
    issues: list[Issue],
) -> None:
    occurrence_to_unit: dict[str, str] = {}
    for unit_id, unit in meaning_units.items():
        occurrences = unit.get("occurrences")
        if not isinstance(occurrences, list):
            continue
        for occurrence in occurrences:
            if isinstance(occurrence, dict) and isinstance(occurrence.get("occurrenceId"), str):
                occurrence_to_unit[occurrence["occurrenceId"]] = unit_id

    for block_index, block in enumerate(blocks_list):
        block_id = block.get("blockId")
        if not isinstance(block_id, str):
            continue
        view = views.get(block_id)
        if view is None:
            continue
        path = f"$.blocks[{block_index}].meaning.parts"
        meaning = block.get("meaning")
        parts = meaning.get("parts") if isinstance(meaning, dict) else None
        if not isinstance(parts, list) or not parts:
            issues.append(
                Issue("meaning_coverage_gap", path, "Block meaning requires at least one part.")
            )
            continue
        resolved: list[tuple[int, int, str]] = []
        for part_index, part in enumerate(parts):
            part_path = f"{path}[{part_index}]"
            if not isinstance(part, dict):
                continue
            occurrence_id = part.get("meaningUnitOccurrenceId")
            if not isinstance(occurrence_id, str):
                issues.append(
                    Issue(
                        "unresolved_meaning_unit_occurrence",
                        part_path + ".meaningUnitOccurrenceId",
                        "Missing meaning-unit occurrence reference.",
                    )
                )
                continue
            occurrence_range = meaning_ranges.get(occurrence_id)
            if occurrence_range is None:
                issues.append(
                    Issue(
                        "unresolved_meaning_unit_occurrence",
                        part_path + ".meaningUnitOccurrenceId",
                        f"Unknown meaning-unit occurrence: {occurrence_id}",
                    )
                )
                continue
            if occurrence_range.block_id != block_id:
                issues.append(
                    Issue(
                        "meaning_occurrence_block_mismatch",
                        part_path + ".meaningUnitOccurrenceId",
                        "Meaning-unit occurrence belongs to a different block.",
                    )
                )
                continue
            if occurrence_id not in occurrence_to_unit:
                issues.append(
                    Issue(
                        "unresolved_meaning_unit_occurrence",
                        part_path + ".meaningUnitOccurrenceId",
                        "Occurrence has no canonical meaning unit.",
                    )
                )
                continue
            resolved.append(
                (occurrence_range.start, occurrence_range.end, occurrence_id)
            )

        previous_end = -1
        for index, (start, end, _occurrence_id) in enumerate(resolved):
            if index and start < previous_end:
                issues.append(
                    Issue(
                        "meaning_part_overlap",
                        f"{path}[{index}]",
                        "Block meaning parts must be source ordered and non-overlapping.",
                    )
                )
            previous_end = max(previous_end, end)

        covered_lines: set[str] = set()
        for _start, _end, occurrence_id in resolved:
            covered_lines.update(meaning_ranges[occurrence_id].line_ids)
        missing_lines = [line_id for line_id in view.line_ids if line_id not in covered_lines]
        if missing_lines:
            issues.append(
                Issue(
                    "meaning_coverage_gap",
                    path,
                    "Block meaning does not cover: " + ", ".join(missing_lines),
                )
            )


def _validate_relations(
    document: dict[str, Any],
    phrases: dict[str, dict[str, Any]],
    issues: list[Issue],
) -> None:
    values = document.get("relations")
    if not isinstance(values, list):
        return
    for index, relation in enumerate(values):
        if not isinstance(relation, dict):
            continue
        path = f"$.relations[{index}]"
        _check_labels(relation.get("labels"), path + ".labels", issues)
        from_id = relation.get("fromPhraseId")
        to_id = relation.get("toPhraseId")
        if isinstance(from_id, str) and from_id not in phrases:
            issues.append(
                Issue("unresolved_phrase", path + ".fromPhraseId", f"Unknown phrase: {from_id}")
            )
        if isinstance(to_id, str) and to_id not in phrases:
            issues.append(
                Issue("unresolved_phrase", path + ".toPhraseId", f"Unknown phrase: {to_id}")
            )
        if isinstance(from_id, str) and from_id == to_id:
            issues.append(
                Issue("self_relation", path, "A relation cannot point to the same phrase.")
            )


def _validate_scope(
    scope: Any,
    path: str,
    blocks_by_id: dict[str, dict[str, Any]],
    phrases: dict[str, dict[str, Any]],
    issues: list[Issue],
) -> None:
    if not isinstance(scope, dict):
        return
    block_ids = scope.get("blockIds")
    if not isinstance(block_ids, list) or not block_ids:
        issues.append(
            Issue("missing_scope_blocks", path + ".blockIds", "Scope blockIds are required.")
        )
        return
    if _has_duplicates(block_ids):
        issues.append(
            Issue("duplicate_scope_block", path + ".blockIds", "Scope blockIds must be unique.")
        )
    resolved_blocks = {block_id for block_id in block_ids if isinstance(block_id, str)}
    for index, block_id in enumerate(block_ids):
        if isinstance(block_id, str) and block_id not in blocks_by_id:
            issues.append(
                Issue(
                    "unresolved_block",
                    f"{path}.blockIds[{index}]",
                    f"Unknown block: {block_id}",
                )
            )

    phrase_id = scope.get("phraseId")
    occurrence_ids = scope.get("occurrenceIds")
    if phrase_id is None:
        if occurrence_ids is not None:
            issues.append(
                Issue(
                    "scope_occurrence_without_phrase",
                    path + ".occurrenceIds",
                    "occurrenceIds require a phraseId.",
                )
            )
        return
    phrase = phrases.get(phrase_id) if isinstance(phrase_id, str) else None
    if phrase is None:
        issues.append(
            Issue("unresolved_phrase", path + ".phraseId", f"Unknown phrase: {phrase_id}")
        )
        return
    phrase_occurrences = phrase.get("occurrences")
    if not isinstance(phrase_occurrences, list):
        phrase_occurrences = []
    occurrence_map = {
        occurrence["occurrenceId"]: occurrence
        for occurrence in phrase_occurrences
        if isinstance(occurrence, dict)
        and isinstance(occurrence.get("occurrenceId"), str)
    }
    for block_id in resolved_blocks:
        if not any(
            isinstance(occurrence, dict) and occurrence.get("blockId") == block_id
            for occurrence in phrase_occurrences
        ):
            issues.append(
                Issue(
                    "scope_phrase_block_mismatch",
                    path + ".blockIds",
                    f"Phrase has no occurrence in scoped block: {block_id}",
                )
            )
    if occurrence_ids is None:
        return
    if not isinstance(occurrence_ids, list):
        return
    if _has_duplicates(occurrence_ids):
        issues.append(
            Issue(
                "duplicate_scope_occurrence",
                path + ".occurrenceIds",
                "Scope occurrenceIds must be unique.",
            )
        )
    selected_blocks: set[str] = set()
    for index, occurrence_id in enumerate(occurrence_ids):
        occurrence = occurrence_map.get(occurrence_id) if isinstance(occurrence_id, str) else None
        if occurrence is None:
            issues.append(
                Issue(
                    "unresolved_phrase_occurrence",
                    f"{path}.occurrenceIds[{index}]",
                    f"Unknown phrase occurrence: {occurrence_id}",
                )
            )
            continue
        selected_blocks.add(str(occurrence.get("blockId")))
        if occurrence.get("blockId") not in resolved_blocks:
            issues.append(
                Issue(
                    "scope_occurrence_block_mismatch",
                    f"{path}.occurrenceIds[{index}]",
                    "Scoped occurrence is outside the listed blocks.",
                )
            )
    for block_id in sorted(resolved_blocks - selected_blocks):
        issues.append(
            Issue(
                "scope_occurrence_block_gap",
                path + ".occurrenceIds",
                f"No explicit phrase occurrence was selected for block: {block_id}",
            )
        )


def _validate_notes_and_uncertainties(
    document: dict[str, Any],
    blocks_by_id: dict[str, dict[str, Any]],
    phrases: dict[str, dict[str, Any]],
    issues: list[Issue],
) -> None:
    notes = document.get("grammarNotes")
    if isinstance(notes, list):
        for index, note in enumerate(notes):
            if not isinstance(note, dict):
                continue
            path = f"$.grammarNotes[{index}]"
            _check_labels(note.get("labels"), path + ".labels", issues)
            _validate_scope(
                note.get("scope"), path + ".scope", blocks_by_id, phrases, issues
            )
    uncertainties = document.get("uncertainties")
    if isinstance(uncertainties, list):
        for index, uncertainty in enumerate(uncertainties):
            if not isinstance(uncertainty, dict):
                continue
            path = f"$.uncertainties[{index}]"
            _validate_scope(
                uncertainty.get("scope"), path + ".scope", blocks_by_id, phrases, issues
            )


def _validate_shared_occurrences(
    repetition_ranges: dict[str, dict[str, OccurrenceRange]],
    occurrence_ranges: dict[str, OccurrenceRange],
    owner_names: dict[str, str],
    issues: list[Issue],
) -> None:
    by_owner_block: dict[tuple[str, str], list[OccurrenceRange]] = {}
    for occurrence in occurrence_ranges.values():
        by_owner_block.setdefault((occurrence.owner_id, occurrence.block_id), []).append(
            occurrence
        )

    emitted: set[tuple[str, str, str]] = set()
    for repetition_id, group_ranges in repetition_ranges.items():
        for owner_id in {range_.owner_id for range_ in occurrence_ranges.values()}:
            owner_occurrences = [
                range_
                for (candidate_owner, _block_id), values in by_owner_block.items()
                if candidate_owner == owner_id
                for range_ in values
            ]
            for anchor in owner_occurrences:
                anchor_group = group_ranges.get(anchor.block_id)
                if anchor_group is None:
                    continue
                if anchor.start < anchor_group.start or anchor.end > anchor_group.end:
                    continue
                relative_start = anchor.start - anchor_group.start
                relative_end = anchor.end - anchor_group.start
                expected_text = anchor_group.normalized_text[relative_start:relative_end]
                for target_block, target_group in group_ranges.items():
                    expected_start = target_group.start + relative_start
                    expected_end = target_group.start + relative_end
                    exact = [
                        candidate
                        for candidate in by_owner_block.get((owner_id, target_block), [])
                        if candidate.start == expected_start and candidate.end == expected_end
                    ]
                    if exact:
                        continue
                    same_text = [
                        candidate
                        for candidate in by_owner_block.get((owner_id, target_block), [])
                        if candidate.normalized_text == expected_text
                    ]
                    code = (
                        "shared-occurrence-mismatch"
                        if same_text
                        else "shared-occurrence-incomplete"
                    )
                    identity = (owner_id, repetition_id, target_block)
                    if identity in emitted:
                        continue
                    emitted.add(identity)
                    issues.append(
                        Issue(
                            code,
                            f"$.{owner_names.get(owner_id, 'phrases')}",
                            f"{owner_id} does not map to {repetition_id} in block {target_block}.",
                        )
                    )


def validate_grammar_analysis_v2(
    document: Any,
    projection: dict[str, Any] | None = None,
    expected_lrc_file: str | None = None,
    require_projection: bool = False,
    learner_language: str | None = None,
) -> list[Issue]:
    """Validate a Schema v2 analysis without running repetition discovery."""

    if not isinstance(document, dict):
        return [Issue("invalid_sidecar", "$", "Sidecar must be a JSON object.")]
    if document.get("schema") != SIDECAR_SCHEMA_ID:
        return [
            Issue(
                "invalid_sidecar",
                "$.schema",
                f"Expected schema {SIDECAR_SCHEMA_ID}.",
            )
        ]
    if document.get("schemaVersion") != SCHEMA_VERSION:
        found = document.get("schemaVersion")
        message = (
            f"Schema v{found} is not supported; regenerate a Schema v2 sidecar."
            if found is not None
            else "Schema v2 is required; regenerate the sidecar."
        )
        return [Issue("unsupported_schema_version", "$.schemaVersion", message)]

    value_issues = json_value_issues(document)
    if value_issues:
        return value_issues

    schema = load_v2_schema()
    schema_issues = _walk_schema(schema)
    if schema_issues:
        return schema_issues
    from sidecarlib import validate_schema_instance

    issues = validate_schema_instance(document, schema)
    if issues:
        return issues

    _check_html_and_paths(document, issues)
    _validate_override_rules(document, issues)
    _collect_global_ids(document, issues)
    lines_by_id, normalized_by_id = _validate_source_and_lines(
        document, projection, expected_lrc_file, issues
    )
    blocks_list, blocks_by_id, views = _validate_blocks(
        document, lines_by_id, normalized_by_id, issues
    )
    repetition_ranges = _validate_repetition_groups(
        document, blocks_by_id, views, lines_by_id, issues
    )
    meaning_units, meaning_ranges = _validate_meaning_units(
        document, blocks_by_id, views, lines_by_id, issues
    )
    phrases, phrase_ranges = _validate_phrases(
        document, blocks_by_id, views, lines_by_id, issues
    )
    _validate_block_meanings(
        blocks_list, views, meaning_units, meaning_ranges, issues
    )
    _validate_relations(document, phrases, issues)
    _validate_notes_and_uncertainties(document, blocks_by_id, phrases, issues)

    ownership: dict[str, str] = {}
    for phrase_id in phrases:
        ownership[phrase_id] = "phrases"
    for meaning_unit_id in meaning_units:
        ownership[meaning_unit_id] = "meaningUnits"
    _validate_shared_occurrences(
        repetition_ranges,
        {**phrase_ranges, **meaning_ranges},
        ownership,
        issues,
    )

    if require_projection and projection is None:
        issues.append(
            Issue(
                "missing_projection",
                "$.source.lrcProjectionHash",
                "Offline verification requires the frozen source projection.",
            )
        )
    issues.extend(validate_learner_language_v2(document, learner_language))
    return issues


def ensure_v2_document(document: Any) -> None:
    if not isinstance(document, dict) or document.get("schema") != SIDECAR_SCHEMA_ID:
        raise _error("invalid_sidecar", "$", "Expected a lyrics grammar analysis sidecar.")
    if document.get("schemaVersion") != SCHEMA_VERSION:
        found = document.get("schemaVersion")
        raise _error(
            "unsupported_schema_version",
            "$.schemaVersion",
            f"Schema v{found} is not supported; regenerate a Schema v2 sidecar.",
        )


def normalized_analysis_candidate(
    draft: dict[str, Any],
    projection: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild source identity fields and retain only Schema v2 semantic data."""

    draft_lines = draft.get("lines")
    if not isinstance(draft_lines, list):
        raise _error("normalization_failed", "$.lines", "Draft lines must be an array.")
    lines_by_id = {
        line["lineId"]: line
        for line in draft_lines
        if isinstance(line, dict) and isinstance(line.get("lineId"), str)
    }
    normalized_lines: list[dict[str, Any]] = []
    for projected in projection["lines"]:
        line = lines_by_id.get(projected["lineId"])
        if line is None:
            raise _error(
                "normalization_failed",
                "$.lines",
                f"Missing draft line: {projected['lineId']}",
            )
        normalized: dict[str, Any] = {
            "lineId": projected["lineId"],
            "sourceOrder": projected["sourceOrder"],
            "timeMs": projected["timeMs"],
            "text": projected["text"],
            "fingerprint": projected["fingerprint"],
            "role": "text",
            "language": line["language"],
        }
        if "endTimeMs" in line:
            normalized["endTimeMs"] = line["endTimeMs"]
        if "languageSpans" in line:
            normalized["languageSpans"] = line["languageSpans"]
        normalized_lines.append(normalized)

    source = draft["source"]
    return {
        "schema": SIDECAR_SCHEMA_ID,
        "schemaVersion": SCHEMA_VERSION,
        "source": {
            "lrcFile": projection.get("lrcFile") or source["lrcFile"],
            "songId": projection["songId"],
            "sourceLanguages": sorted(set(source["sourceLanguages"])),
            "lineCount": projection["lineCount"],
            "lrcProjectionHash": projection["lrcProjectionHash"],
        },
        "lines": normalized_lines,
        "repetitionGroups": draft["repetitionGroups"],
        "meaningUnits": draft["meaningUnits"],
        "blocks": draft["blocks"],
        "phrases": draft["phrases"],
        "relations": draft["relations"],
        "grammarNotes": draft["grammarNotes"],
        "uncertainties": draft["uncertainties"],
        "extensions": draft["extensions"],
    }


def _source_segments_for_range(
    view: BlockView,
    start: int,
    end: int,
) -> list[dict[str, Any]] | None:
    if start < 0 or end <= start or end > len(view.normalized_text):
        return None
    expected_normalized = view.normalized_text[start:end]
    for raw_start in view.normalized_to_raw.get(start, ()):
        for raw_end in view.normalized_to_raw.get(end, ()):
            if raw_end <= raw_start:
                continue
            selected: list[tuple[NormalizedLine, int, int]] = []
            for line in view.lines:
                line_start, line_end = view.line_raw_ranges[line.line_id]
                part_start = max(raw_start, line_start)
                part_end = min(raw_end, line_end)
                if part_start >= part_end:
                    continue
                selected.append((line, part_start - line_start, part_end - line_start))
            if not selected:
                continue

            valid = True
            for index, (line, local_start, local_end) in enumerate(selected):
                if index:
                    previous_line = selected[index - 1][0]
                    if not (
                        line.source_order == previous_line.source_order + 1
                        and view.line_indexes[line.line_id]
                        == view.line_indexes[previous_line.line_id] + 1
                        and local_start == 0
                    ):
                        valid = False
                        break
                if index < len(selected) - 1 and local_end != len(line.raw_text):
                    valid = False
                    break
            if not valid:
                continue

            segments: list[dict[str, Any]] = [
                {
                    "lineId": line.line_id,
                    "start": local_start,
                    "end": local_end,
                    "text": line.raw_text[local_start:local_end],
                }
                for line, local_start, local_end in selected
            ]
            combined_text = "".join(segment["text"] for segment in segments)
            if combined_text != view.raw_text[raw_start:raw_end]:
                continue
            if _nfc(combined_text) != expected_normalized:
                continue
            return segments
    return None


def _repetition_id(text: str, block_ids: Sequence[str]) -> str:
    payload = {
        "kind": "maximal-exact-repetition",
        "text": text,
        "blockIds": sorted(block_ids),
    }
    return "rep_" + sha256_bytes(canonical_json(payload).encode("utf-8"))[7:23]


def _repetition_occurrence_id(
    repetition_id: str,
    block_id: str,
    segments: Sequence[dict[str, Any]],
) -> str:
    payload = {
        "repeat": repetition_id,
        "blockId": block_id,
        "segments": list(segments),
    }
    return "repo_" + sha256_bytes(canonical_json(payload).encode("utf-8"))[7:23]


def _pair_maximal_matches(
    left: BlockView,
    right: BlockView,
    minimum: int,
) -> Iterable[tuple[str, int, int, int, int]]:
    left_text = left.normalized_text
    right_text = right.normalized_text
    for offset in range(-(len(right_text) - 1), len(left_text)):
        left_index = max(0, offset)
        right_index = max(0, -offset)
        while left_index < len(left_text) and right_index < len(right_text):
            if left_text[left_index] != right_text[right_index]:
                left_index += 1
                right_index += 1
                continue
            left_start = left_index
            right_start = right_index
            while (
                left_index < len(left_text)
                and right_index < len(right_text)
                and left_text[left_index] == right_text[right_index]
            ):
                left_index += 1
                right_index += 1
            length = left_index - left_start
            if length >= minimum:
                yield (
                    left_text[left_start:left_index],
                    left_start,
                    left_index,
                    right_start,
                    right_index,
                )


def _group_candidate_matches(
    matches_by_text: dict[str, dict[str, list[tuple[int, int]]]],
) -> list[dict[str, Any]]:
    return [
        {
            "text": text,
            "ranges": {
                block_id: min(spans)
                for block_id, spans in spans_by_block.items()
            },
            "all_ranges": {
                block_id: sorted(set(spans))
                for block_id, spans in spans_by_block.items()
            },
        }
        for text, spans_by_block in matches_by_text.items()
        if len(spans_by_block) >= 2
    ]


def _drop_contained_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: -len(item["text"])):
        for block_id, spans in candidate["all_ranges"].items():
            escaping = [
                span
                for span in spans
                if not any(
                    len(other["text"]) > len(candidate["text"])
                    and block_id in other["ranges"]
                    and other["ranges"][block_id][0]
                    <= span[0]
                    <= span[1]
                    <= other["ranges"][block_id][1]
                    for other in kept
                )
            ]
            candidate["ranges"][block_id] = (escaping or spans)[0]
        contained = False
        for other in kept:
            if candidate is other:
                continue
            if len(other["text"]) <= len(candidate["text"]):
                continue
            if candidate["text"] not in other["text"]:
                continue
            if not all(
                block_id in other["ranges"]
                and other["ranges"][block_id][0]
                <= span[0]
                <= span[1]
                <= other["ranges"][block_id][1]
                for block_id, span in candidate["ranges"].items()
            ):
                continue
            contained = True
            break
        if not contained:
            candidate.pop("all_ranges", None)
            kept.append(candidate)
    return kept


def _learning_block_views(
    document: dict[str, Any],
    lines_by_id: dict[str, dict[str, Any]],
) -> list[BlockView]:
    normalized = {
        line_id: _normalized_line(line)
        for line_id, line in lines_by_id.items()
    }
    result: list[BlockView] = []
    blocks = document.get("blocks")
    if not isinstance(blocks, list):
        return result
    for block in blocks:
        if not isinstance(block, dict) or _is_metadata_block(block):
            continue
        view = _build_block_view(block, lines_by_id, normalized)
        if view is not None:
            result.append(view)
    return result


def discover_repetition_candidates(
    document: dict[str, Any],
    projection: dict[str, Any],
    *,
    learner_language: str | None = None,
) -> dict[str, Any]:
    """Return deterministic maximal exact repetition candidates."""

    issues = validate_grammar_analysis_v2(
        document,
        projection=projection,
        expected_lrc_file=projection.get("lrcFile"),
        require_projection=True,
        learner_language=learner_language,
    )
    if issues:
        raise _error(issues[0].code, issues[0].path, issues[0].message)

    lines_by_id = {
        line["lineId"]: line
        for line in document["lines"]
        if isinstance(line, dict)
    }
    views = _learning_block_views(document, lines_by_id)
    views.sort(
        key=lambda view: min(
            lines_by_id[line_id]["sourceOrder"] for line_id in view.line_ids
        )
    )
    block_rank = {view.block_id: index for index, view in enumerate(views)}

    matches_by_text: dict[str, dict[str, list[tuple[int, int]]]] = {}
    for left_index, left in enumerate(views):
        for right in views[left_index + 1 :]:
            for text, left_start, left_end, right_start, right_end in _pair_maximal_matches(
                left, right, MIN_REPETITION_CODE_POINTS
            ):
                if _source_segments_for_range(left, left_start, left_end) is None:
                    continue
                if _source_segments_for_range(right, right_start, right_end) is None:
                    continue
                spans_by_block = matches_by_text.setdefault(text, {})
                for block_id, span in (
                    (left.block_id, (left_start, left_end)),
                    (right.block_id, (right_start, right_end)),
                ):
                    spans = spans_by_block.setdefault(block_id, [])
                    if span not in spans:
                        spans.append(span)

    candidates = _drop_contained_candidates(_group_candidate_matches(matches_by_text))
    occurrences_by_block = {
        view.block_id: view for view in views
    }
    prepared: list[dict[str, Any]] = []
    for candidate in candidates:
        text = candidate["text"]
        block_ids = sorted(
            candidate["ranges"],
            key=lambda block_id: block_rank[block_id],
        )
        repetition_id = _repetition_id(text, block_ids)
        occurrences: list[dict[str, Any]] = []
        for block_id in block_ids:
            view = occurrences_by_block[block_id]
            start, end = candidate["ranges"][block_id]
            segments = _source_segments_for_range(view, start, end)
            if segments is None:
                continue
            occurrences.append(
                {
                    "occurrenceId": _repetition_occurrence_id(
                        repetition_id, block_id, segments
                    ),
                    "blockId": block_id,
                    "sourceSegments": segments,
                }
            )
        if len(occurrences) < 2:
            continue
        prepared.append(
            {
                "repetitionId": repetition_id,
                "normalizedText": text,
                "occurrences": occurrences,
                "_anchor": min(
                    lines_by_id[occurrence["sourceSegments"][0]["lineId"]]["sourceOrder"]
                    for occurrence in occurrences
                ),
            }
        )

    prepared.sort(
        key=lambda candidate: (
            candidate["_anchor"],
            candidate["normalizedText"],
            candidate["repetitionId"],
        )
    )
    for candidate in prepared:
        candidate.pop("_anchor", None)

    blocks_payload: list[dict[str, Any]] = []
    for view in views:
        blocks_payload.append(
            {
                "blockId": view.block_id,
                "lineIds": list(view.line_ids),
            }
        )
    result = {
        "schema": REPETITION_CANDIDATE_SCHEMA_ID,
        "candidateVersion": REPETITION_CANDIDATE_VERSION,
        "sourceProjectionHash": projection["lrcProjectionHash"],
        "minimumCodePoints": MIN_REPETITION_CODE_POINTS,
        "blocks": blocks_payload,
        "candidates": prepared,
    }
    candidate_issues = _schema_issues(
        result, "repetition-candidates.schema.json"
    )
    if candidate_issues:
        raise _error(
            "invalid_repetition_candidates",
            candidate_issues[0].path,
            candidate_issues[0].message,
        )
    return result


def candidate_groups(candidate_document: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = candidate_document.get("candidates")
    if not isinstance(candidates, list):
        return []
    groups: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        groups.append(
            {
                "repetitionId": candidate.get("repetitionId"),
                "occurrences": candidate.get("occurrences"),
            }
        )
    return groups


def validate_repetition_completion(
    document: dict[str, Any],
    projection: dict[str, Any],
    *,
    learner_language: str | None = None,
) -> tuple[dict[str, Any], list[Issue]]:
    candidates = discover_repetition_candidates(
        document,
        projection,
        learner_language=learner_language,
    )
    expected = candidate_groups(candidates)
    actual = document.get("repetitionGroups")
    if _json_equal(expected, actual):
        return candidates, []
    if not isinstance(actual, list):
        return candidates, [
            Issue(
                "missing_repetition_candidate",
                "$.repetitionGroups",
                "repetitionGroups must be an array of discovered candidates.",
            )
        ]
    expected_ids = {
        group["repetitionId"]
        for group in expected
        if isinstance(group, dict) and isinstance(group.get("repetitionId"), str)
    }
    actual_ids = {
        group["repetitionId"]
        for group in actual
        if isinstance(group, dict) and isinstance(group.get("repetitionId"), str)
    }
    missing = expected_ids - actual_ids
    if missing:
        return candidates, [
            Issue(
                "missing_repetition_candidate",
                "$.repetitionGroups",
                "Missing deterministic candidates: " + ", ".join(sorted(missing)),
            )
        ]
    return candidates, [
        Issue(
            "repetition_candidate_mismatch",
            "$.repetitionGroups",
            "repetitionGroups must equal the deterministic candidate list exactly.",
        )
    ]


def write_text_atomic(path: Path, text: str) -> bool:
    if path.is_symlink():
        raise _error(
            "artifact_symlink",
            "$",
            f"Refusing to write through a symlink: {path}",
        )
    data = text.encode("utf-8")
    if path.exists() and path.read_bytes() == data:
        return False
    _write_bytes_atomic(path, data)
    return True


def write_candidate_hash(path: Path, candidate_hash: str) -> bool:
    if HASH_PATTERN.fullmatch(candidate_hash) is None:
        raise _error("invalid_candidate_hash", "$", "Candidate hash is not canonical.")
    return write_text_atomic(path, candidate_hash + "\n")


def read_candidate_hash(path: Path) -> str:
    if path.is_symlink():
        raise _error(
            "unsupported_source",
            "$",
            f"Symlinked candidate hashes are not supported: {path}",
        )
    try:
        text = path.read_bytes().decode("utf-8")
    except FileNotFoundError as exc:
        raise _error("file_not_found", "$", f"Candidate hash not found: {path}") from exc
    except UnicodeDecodeError as exc:
        raise _error("invalid_utf8", "$", f"Candidate hash is not valid UTF-8: {path}") from exc
    except OSError as exc:
        raise _error(
            "file_read_failed", "$", f"Could not read candidate hash: {path}"
        ) from exc
    value = text.rstrip("\r\n")
    if HASH_PATTERN.fullmatch(value) is None or text not in {value, value + "\n", value + "\r\n"}:
        raise _error(
            "invalid_candidate_hash",
            "$",
            "Candidate hash file must contain exactly one sha256 value.",
        )
    return value


def _report(
    issues: Sequence[Issue],
    *,
    schema_version: int = SCHEMA_VERSION,
    **extra: Any,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "ok": not issues,
        "schemaVersion": schema_version,
        "errors": [issue.as_dict() for issue in issues],
    }
    report.update(extra)
    return report


def write_validation_report(path: Path, report: dict[str, Any]) -> None:
    write_json_atomic(path, report, replace=True)


def prepare_semantic_candidate(
    lrc_path: Path,
    projection_path: Path,
    workdir: Path,
    draft_path: Path,
    *,
    learner_language: str,
    report_path: Path | None = None,
    candidate_path: Path | None = None,
    candidate_hash_path: Path | None = None,
    repetition_candidates_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a draft, freeze its normalized candidate, and write its hash."""

    workdir = workdir.expanduser().resolve()
    draft_path = ensure_inside_workdir(draft_path, workdir, "semantic draft")
    candidate_path = ensure_inside_workdir(
        candidate_path or (workdir / "analysis-candidate.json"),
        workdir,
        "analysis candidate",
    )
    candidate_hash_path = ensure_inside_workdir(
        candidate_hash_path or (workdir / "analysis-candidate.sha256"),
        workdir,
        "candidate hash",
    )
    repetition_candidates_path = ensure_inside_workdir(
        repetition_candidates_path or (workdir / "repetition-candidates.json"),
        workdir,
        "repetition candidates",
    )
    report_path = ensure_inside_workdir(
        report_path or (workdir / "validation-report.json"),
        workdir,
        "validation report",
    )
    translated = workdir / "translated.lrc"
    manifest_path = ensure_inside_workdir(
        workdir / "translation-manifest.json",
        workdir,
        "translation manifest",
    )
    assert_distinct_paths(
        ("source LRC", lrc_path),
        ("source projection", projection_path),
        ("semantic draft", draft_path),
        ("frozen translation", translated),
        ("translation manifest", manifest_path),
        ("analysis candidate", candidate_path),
        ("candidate hash", candidate_hash_path),
        ("repetition candidates", repetition_candidates_path),
        ("validation report", report_path),
    )

    try:
        projection = load_projection(projection_path)
        source_lrc = ensure_local_lrc(lrc_path)
        _, source_issues = verify_source_matches_projection(projection, source_lrc)
        if source_issues:
            report = _report(source_issues, stage="prepare")
            write_validation_report(report_path, report)
            raise _error(
                source_issues[0].code,
                source_issues[0].path,
                source_issues[0].message,
            )

        if not translated.is_file():
            raise _error("file_not_found", "$", f"Frozen translation not found: {translated}")
        manifest = read_json(manifest_path)
        _, freeze_issues = verify_frozen_translation(projection, translated, manifest)
        if freeze_issues:
            report = _report(freeze_issues, stage="prepare")
            write_validation_report(report_path, report)
            raise _error(
                freeze_issues[0].code,
                freeze_issues[0].path,
                freeze_issues[0].message,
            )

        draft = read_json(draft_path)
        candidate_projection = {**projection, "lrcFile": source_lrc.name}
        issues = validate_grammar_analysis_v2(
            draft,
            projection=candidate_projection,
            expected_lrc_file=source_lrc.name,
            require_projection=True,
            learner_language=learner_language,
        )
        if not issues:
            candidates, completion_issues = validate_repetition_completion(
                draft,
                projection,
                learner_language=learner_language,
            )
            issues.extend(completion_issues)
        else:
            candidates = {}
        if issues:
            report = _report(issues, stage="prepare")
            write_validation_report(report_path, report)
            raise _error(issues[0].code, issues[0].path, issues[0].message)

        normalized = normalized_analysis_candidate(draft, candidate_projection)
        normalized_issues = validate_grammar_analysis_v2(
            normalized,
            projection=candidate_projection,
            expected_lrc_file=source_lrc.name,
            require_projection=True,
            learner_language=learner_language,
        )
        if normalized_issues:
            report = _report(normalized_issues, stage="prepare")
            write_validation_report(report_path, report)
            raise _error(
                normalized_issues[0].code,
                normalized_issues[0].path,
                normalized_issues[0].message,
            )

        write_json_atomic(repetition_candidates_path, candidates, replace=True)
        write_json_atomic(candidate_path, normalized, replace=True)
        candidate_hash = sha256_file(candidate_path)
        write_candidate_hash(candidate_hash_path, candidate_hash)
        report = _report(
            [],
            stage="prepare",
            projectionHash=projection["lrcProjectionHash"],
            candidateHash=candidate_hash,
            candidate=str(candidate_path.name),
        )
        write_validation_report(report_path, report)
        return normalized, report
    except SidecarError as exc:
        try:
            write_validation_report(
                report_path,
                _report([Issue(exc.code, exc.path, exc.message)], stage="prepare"),
            )
        except OSError:
            pass
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        issue = Issue("prepare_failed", "$", str(exc))
        write_validation_report(report_path, _report([issue], stage="prepare"))
        raise _error(issue.code, issue.path, issue.message) from exc


def load_frozen_candidate(
    candidate_path: Path,
    candidate_hash_path: Path,
) -> tuple[dict[str, Any], str]:
    candidate, candidate_bytes = _read_json_and_bytes(candidate_path)
    ensure_v2_document(candidate)
    canonical_bytes = pretty_json(candidate).encode("utf-8")
    if candidate_bytes != canonical_bytes:
        raise _error(
            "candidate_not_canonical",
            "$",
            "analysis-candidate.json must use the finalizer's canonical UTF-8 JSON encoding.",
        )
    actual_hash = sha256_bytes(candidate_bytes)
    recorded_hash = read_candidate_hash(candidate_hash_path)
    if actual_hash != recorded_hash:
        raise _error(
            "candidate_hash_drift",
            "$",
            "analysis-candidate.json no longer matches analysis-candidate.sha256.",
        )
    return candidate, actual_hash


def _read_json_and_bytes(path: Path) -> tuple[Any, bytes]:
    if path.is_symlink():
        raise _error(
            "unsupported_source",
            "$",
            f"Symlinked JSON inputs are not supported: {path}",
        )
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise _error("file_not_found", "$", f"File not found: {path}") from exc
    except OSError as exc:
        raise _error(
            "file_read_failed", "$", f"Could not read JSON file: {path}"
        ) from exc
    return parse_json_bytes(data, path.name), data


def validate_semantic_audit(
    report: Any,
    candidate: dict[str, Any],
    candidate_hash: str,
    learner_language: str,
) -> list[Issue]:
    if not isinstance(report, dict):
        return [Issue("invalid_semantic_audit", "$", "Audit report must be an object.")]
    if report.get("schema") != SEMANTIC_AUDIT_SCHEMA_ID:
        return [
            Issue(
                "invalid_semantic_audit",
                "$.schema",
                f"Expected schema {SEMANTIC_AUDIT_SCHEMA_ID}.",
            )
        ]
    if report.get("auditVersion") != AUDIT_VERSION:
        return [
            Issue(
                "unsupported_audit_version",
                "$.auditVersion",
                "Semantic audit version 1 is required.",
            )
        ]
    issues = _schema_issues(report, "semantic-audit.schema.json")
    if issues:
        return issues
    if report.get("candidateSha256") != candidate_hash:
        issues.append(
            Issue(
                "audit_candidate_hash_mismatch",
                "$.candidateSha256",
                "Audit report is bound to a different analysis candidate.",
            )
        )
    expected_language = learner_language.strip().lower()
    if report.get("learnerLanguage", "").strip().lower() != expected_language:
        issues.append(
            Issue(
                "audit_learner_language_mismatch",
                "$.learnerLanguage",
                f"Audit must use learner language {expected_language}.",
            )
        )
    candidate_blocks = candidate.get("blocks", [])
    expected_blocks = [
        block.get("blockId")
        for block in candidate_blocks
        if isinstance(block, dict) and isinstance(block.get("blockId"), str)
    ]
    reviewed_blocks = report.get("reviewedBlockIds")
    if (
        not isinstance(reviewed_blocks, list)
        or _has_duplicates(reviewed_blocks)
        or set(reviewed_blocks) != set(expected_blocks)
        or len(reviewed_blocks) != len(expected_blocks)
    ):
        issues.append(
            Issue(
                "audit_review_coverage",
                "$.reviewedBlockIds",
                "reviewedBlockIds must exactly match the candidate blocks.",
            )
        )
    candidate_units = candidate.get("meaningUnits", [])
    expected_units = [
        unit.get("meaningUnitId")
        for unit in candidate_units
        if isinstance(unit, dict) and isinstance(unit.get("meaningUnitId"), str)
    ]
    reviewed_units = report.get("reviewedMeaningUnitIds")
    if (
        not isinstance(reviewed_units, list)
        or _has_duplicates(reviewed_units)
        or set(reviewed_units) != set(expected_units)
        or len(reviewed_units) != len(expected_units)
    ):
        issues.append(
            Issue(
                "audit_review_coverage",
                "$.reviewedMeaningUnitIds",
                "reviewedMeaningUnitIds must exactly match the candidate meaning units.",
            )
        )
    findings = report.get("findings")
    if isinstance(findings, list):
        invalid_codes = [
            finding.get("code")
            for finding in findings
            if isinstance(finding, dict) and finding.get("code") not in AUDIT_FINDING_CODES
        ]
        if invalid_codes:
            issues.append(
                Issue(
                    "invalid_audit_finding",
                    "$.findings",
                    "Audit findings use unsupported codes.",
                )
            )
        errors = [
            finding
            for finding in findings
            if isinstance(finding, dict) and finding.get("severity") == "error"
        ]
    else:
        errors = []
    if report.get("status") == "pass" and errors:
        issues.append(
            Issue(
                "audit_status_mismatch",
                "$.status",
                "A pass audit cannot contain error findings.",
            )
        )
    if report.get("status") == "fail" and not errors:
        issues.append(
            Issue(
                "audit_status_mismatch",
                "$.status",
                "A fail audit must contain at least one error finding.",
            )
        )
    if report.get("status") != "pass":
        issues.append(
            Issue(
                "semantic_audit_failed",
                "$.status",
                "Semantic audit must pass before finalization.",
            )
        )
    return issues


def build_isolation_proof_v2(
    projection: dict[str, Any],
    stage: str,
    artifact_path: Path,
    method: str,
    child_transcript_id: str,
    parent_transcript_id: str | None = None,
) -> dict[str, Any]:
    if stage not in ISOLATION_STAGES:
        raise _error("unsupported_isolation_stage", "$.stage", "Unsupported isolation stage.")
    if method not in ISOLATION_METHODS:
        raise _error(
            "unsupported_isolation_method",
            "$.method",
            "Use native-subagent, codex-exec, or documented-harness.",
        )
    if not isinstance(child_transcript_id, str):
        raise _error(
            "missing_isolation_proof",
            "$.childTranscriptId",
            "A separate child transcript ID is required.",
        )
    child_transcript_id = child_transcript_id.strip()
    if not child_transcript_id:
        raise _error(
            "missing_isolation_proof",
            "$.childTranscriptId",
            "A separate child transcript ID is required.",
        )
    if parent_transcript_id is not None:
        if not isinstance(parent_transcript_id, str):
            raise _error(
                "invalid_parent_transcript",
                "$.parentTranscriptId",
                "parentTranscriptId must be a string when provided.",
            )
        parent_transcript_id = parent_transcript_id.strip()
        if not parent_transcript_id:
            raise _error(
                "invalid_parent_transcript",
                "$.parentTranscriptId",
                "parentTranscriptId must not be blank.",
            )
    if parent_transcript_id and parent_transcript_id == child_transcript_id:
        raise _error(
            "inline_generation",
            "$.childTranscriptId",
            "The child transcript must differ from the parent transcript.",
        )
    expanded_artifact = artifact_path.expanduser()
    if expanded_artifact.is_symlink():
        raise _error(
            "unsupported_source",
            "$",
            f"Symlinked isolation artifacts are not supported: {expanded_artifact}",
        )
    artifact = expanded_artifact.resolve()
    if not artifact.is_file():
        raise _error("file_not_found", "$", f"Isolation artifact not found: {artifact}")
    proof = {
        "schema": ISOLATION_SCHEMA_ID,
        "isolationVersion": ISOLATION_VERSION,
        "stage": stage,
        "method": method,
        "context": ISOLATION_CONTEXT,
        "transcriptIsolated": True,
        "childTranscriptId": child_transcript_id,
        "parentTranscriptId": parent_transcript_id,
        "sourceProjectionHash": projection["lrcProjectionHash"],
        "artifactSha256": sha256_file(artifact),
    }
    issues = validate_isolation_proof_v2(
        proof,
        projection,
        stage=stage,
        artifact_path=artifact,
    )
    if issues:
        raise _error(issues[0].code, issues[0].path, issues[0].message)
    return proof


def validate_isolation_proof_v2(
    proof: Any,
    projection: dict[str, Any],
    *,
    stage: str,
    artifact_path: Path,
) -> list[Issue]:
    if not isinstance(proof, dict):
        return [
            Issue(
                "missing_isolation_proof",
                "$",
                f"A {stage} isolation proof is required.",
            )
        ]
    if proof.get("isolationVersion") != ISOLATION_VERSION:
        return [
            Issue(
                "unsupported_isolation_version",
                "$.isolationVersion",
                "Isolation proof v1 is no longer supported; record a v2 proof.",
            )
        ]
    issues = _schema_issues(proof, "isolation-proof.schema.json")
    if issues:
        return issues
    if proof.get("stage") != stage:
        issues.append(
            Issue(
                "isolation_stage_mismatch",
                "$.stage",
                f"Expected stage {stage}.",
            )
        )
    if proof.get("sourceProjectionHash") != projection.get("lrcProjectionHash"):
        issues.append(
            Issue(
                "stale_projection_hash",
                "$.sourceProjectionHash",
                "Isolation proof belongs to a different source projection.",
            )
        )
    child = proof.get("childTranscriptId")
    parent = proof.get("parentTranscriptId")
    if not isinstance(child, str) or not child.strip():
        issues.append(
            Issue(
                "missing_isolation_proof",
                "$.childTranscriptId",
                "A separate child transcript ID is required.",
            )
        )
    if isinstance(parent, str) and not parent.strip():
        issues.append(
            Issue(
                "invalid_parent_transcript",
                "$.parentTranscriptId",
                "parentTranscriptId must be null or a non-blank transcript ID.",
            )
        )
    if (
        isinstance(child, str)
        and isinstance(parent, str)
        and child.strip() == parent.strip()
    ):
        issues.append(
            Issue(
                "inline_generation",
                "$.childTranscriptId",
                "The child transcript must differ from the parent transcript.",
            )
        )
    expanded_artifact = artifact_path.expanduser()
    artifact = expanded_artifact.resolve()
    if expanded_artifact.is_symlink():
        issues.append(
            Issue(
                "unsupported_source",
                "$",
                f"Symlinked isolation artifacts are not supported: {expanded_artifact}",
            )
        )
    elif not artifact.is_file():
        issues.append(
            Issue("file_not_found", "$", f"Isolation artifact not found: {artifact}")
        )
    else:
        actual_hash = sha256_file(artifact)
        if proof.get("artifactSha256") != actual_hash:
            issues.append(
                Issue(
                    "artifact_hash_mismatch",
                    "$.artifactSha256",
                    f"The {stage} artifact changed after its isolation proof was recorded.",
                )
            )
    return issues


def validate_distinct_children(
    grammar_proof: dict[str, Any],
    audit_proof: dict[str, Any],
) -> list[Issue]:
    grammar_child = grammar_proof.get("childTranscriptId")
    audit_child = audit_proof.get("childTranscriptId")
    issues: list[Issue] = []
    if (
        isinstance(grammar_child, str)
        and isinstance(audit_child, str)
        and grammar_child.strip() == audit_child.strip()
    ):
        issues.append(
            Issue(
                "same_child_self_audit",
                "$.audit.childTranscriptId",
                "Grammar analysis and semantic audit must use different child transcripts.",
            )
        )
    for label, proof in (("grammar", grammar_proof), ("audit", audit_proof)):
        child = proof.get("childTranscriptId")
        parent = proof.get("parentTranscriptId")
        if (
            isinstance(child, str)
            and isinstance(parent, str)
            and child.strip() == parent.strip()
        ):
            issues.append(
                Issue(
                    "inline_generation",
                    f"$.{label}.childTranscriptId",
                    "A child transcript must differ from the parent transcript.",
                )
            )
    return issues


def _validate_frozen_chain(
    *,
    projection: dict[str, Any],
    workdir: Path,
    draft_path: Path,
    candidate_path: Path,
    candidate_hash_path: Path,
    audit_path: Path,
    grammar_proof_path: Path,
    audit_proof_path: Path,
    learner_language: str,
    expected_lrc_file: str,
) -> tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]:
    draft_path = ensure_inside_workdir(draft_path, workdir, "semantic draft")
    candidate_path = ensure_inside_workdir(
        candidate_path, workdir, "analysis candidate"
    )
    candidate_hash_path = ensure_inside_workdir(
        candidate_hash_path, workdir, "candidate hash"
    )
    audit_path = ensure_inside_workdir(audit_path, workdir, "semantic audit")
    grammar_proof_path = ensure_inside_workdir(
        grammar_proof_path, workdir, "grammar isolation proof"
    )
    audit_proof_path = ensure_inside_workdir(
        audit_proof_path, workdir, "audit isolation proof"
    )

    draft, draft_bytes = _read_json_and_bytes(draft_path)
    draft_issues = validate_grammar_analysis_v2(
        draft,
        projection=projection,
        expected_lrc_file=expected_lrc_file,
        require_projection=True,
        learner_language=learner_language,
    )
    if draft_issues:
        raise _error(
            draft_issues[0].code,
            draft_issues[0].path,
            draft_issues[0].message,
        )
    _, completion_issues = validate_repetition_completion(
        draft,
        projection,
        learner_language=learner_language,
    )
    if completion_issues:
        raise _error(
            completion_issues[0].code,
            completion_issues[0].path,
            completion_issues[0].message,
        )

    rebuilt = normalized_analysis_candidate(draft, projection)
    frozen_candidate, candidate_hash = load_frozen_candidate(
        candidate_path, candidate_hash_path
    )
    if not _json_equal(rebuilt, frozen_candidate):
        raise _error(
            "candidate_content_drift",
            "$",
            "analysis-candidate.json no longer matches a fresh normalization of semantic-draft.json.",
        )
    candidate_issues = validate_grammar_analysis_v2(
        frozen_candidate,
        projection=projection,
        expected_lrc_file=expected_lrc_file,
        require_projection=True,
        learner_language=learner_language,
    )
    if candidate_issues:
        raise _error(
            candidate_issues[0].code,
            candidate_issues[0].path,
            candidate_issues[0].message,
        )

    audit, audit_bytes = _read_json_and_bytes(audit_path)
    audit_issues = validate_semantic_audit(
        audit, frozen_candidate, candidate_hash, learner_language
    )
    if audit_issues:
        raise _error(
            audit_issues[0].code,
            audit_issues[0].path,
            audit_issues[0].message,
        )

    grammar_proof = read_json(grammar_proof_path)
    audit_proof = read_json(audit_proof_path)
    grammar_issues = validate_isolation_proof_v2(
        grammar_proof,
        projection,
        stage="grammar-analysis",
        artifact_path=draft_path,
    )
    if grammar_issues:
        raise _error(
            grammar_issues[0].code,
            grammar_issues[0].path,
            grammar_issues[0].message,
        )
    audit_proof_issues = validate_isolation_proof_v2(
        audit_proof,
        projection,
        stage="semantic-audit",
        artifact_path=audit_path,
    )
    if audit_proof_issues:
        raise _error(
            audit_proof_issues[0].code,
            audit_proof_issues[0].path,
            audit_proof_issues[0].message,
        )
    child_issues = validate_distinct_children(grammar_proof, audit_proof)
    if child_issues:
        raise _error(
            child_issues[0].code,
            child_issues[0].path,
            child_issues[0].message,
        )
    if grammar_proof.get("artifactSha256") != sha256_bytes(draft_bytes):
        raise _error(
            "artifact_hash_mismatch",
            "$.grammar.artifactSha256",
            "Grammar isolation proof does not bind the current semantic draft.",
        )
    if audit_proof.get("artifactSha256") != sha256_bytes(audit_bytes):
        raise _error(
            "artifact_hash_mismatch",
            "$.audit.artifactSha256",
            "Audit isolation proof does not bind the audited semantic report.",
        )
    return frozen_candidate, draft, candidate_hash, audit


def finalize_sidecar_v2(
    lrc_path: Path,
    projection_path: Path,
    workdir: Path,
    *,
    learner_language: str,
    draft_path: Path | None = None,
    candidate_path: Path | None = None,
    candidate_hash_path: Path | None = None,
    audit_path: Path | None = None,
    grammar_proof_path: Path | None = None,
    audit_proof_path: Path | None = None,
    report_path: Path | None = None,
    replace: bool = False,
) -> tuple[Path, bool]:
    workdir = workdir.expanduser().resolve()
    draft_path = draft_path or (workdir / "semantic-draft.json")
    candidate_path = candidate_path or (workdir / "analysis-candidate.json")
    candidate_hash_path = candidate_hash_path or (workdir / "analysis-candidate.sha256")
    audit_path = audit_path or (workdir / "semantic-audit.json")
    grammar_proof_path = grammar_proof_path or (workdir / "isolation-grammar-analysis.json")
    audit_proof_path = audit_proof_path or (workdir / "isolation-semantic-audit.json")
    report_path = ensure_inside_workdir(
        report_path or (workdir / "finalize-report.json"),
        workdir,
        "finalize report",
    )
    translated = workdir / "translated.lrc"
    manifest_path = ensure_inside_workdir(
        workdir / "translation-manifest.json",
        workdir,
        "translation manifest",
    )
    source_lrc = ensure_local_lrc(lrc_path)
    output = source_lrc.with_suffix(".json")
    if output.is_symlink():
        raise _error(
            "unsupported_source",
            "$",
            "The production sidecar path must not be a symlink.",
        )
    assert_distinct_paths(
        ("source LRC", source_lrc),
        ("production sidecar", output),
        ("source projection", projection_path),
        ("semantic draft", draft_path),
        ("frozen translation", translated),
        ("translation manifest", manifest_path),
        ("analysis candidate", candidate_path),
        ("candidate hash", candidate_hash_path),
        ("semantic audit", audit_path),
        ("grammar isolation proof", grammar_proof_path),
        ("audit isolation proof", audit_proof_path),
        ("finalize report", report_path),
    )

    try:
        projection = load_projection(projection_path)
        _, source_issues = verify_source_matches_projection(projection, source_lrc)
        if source_issues:
            write_validation_report(
                report_path, _report(source_issues, stage="finalize")
            )
            raise _error(
                source_issues[0].code,
                source_issues[0].path,
                source_issues[0].message,
            )
        manifest = read_json(manifest_path)
        _, freeze_issues = verify_frozen_translation(projection, translated, manifest)
        if freeze_issues:
            write_validation_report(
                report_path, _report(freeze_issues, stage="finalize")
            )
            raise _error(
                freeze_issues[0].code,
                freeze_issues[0].path,
                freeze_issues[0].message,
            )

        candidate, _draft, candidate_hash, _audit = _validate_frozen_chain(
            projection=projection,
            workdir=workdir,
            draft_path=draft_path,
            candidate_path=candidate_path,
            candidate_hash_path=candidate_hash_path,
            audit_path=audit_path,
            grammar_proof_path=grammar_proof_path,
            audit_proof_path=audit_proof_path,
            learner_language=learner_language,
            expected_lrc_file=source_lrc.name,
        )
        if output.exists() and not replace:
            try:
                existing = read_json(output)
            except SidecarError:
                existing = None
            if isinstance(existing, dict) and existing.get("schemaVersion") == 1:
                raise _error(
                    "unsupported_schema_version",
                    "$.schemaVersion",
                    "Existing production sidecar is Schema v1; pass --replace to regenerate Schema v2.",
                )
        changed = write_json_atomic(output, candidate, replace=replace)
        if sha256_file(output) != candidate_hash:
            raise _error(
                "output_hash_mismatch",
                "$",
                "The atomically written sidecar does not match the audited candidate.",
            )
        report = _report(
            [],
            stage="finalize",
            projectionHash=projection["lrcProjectionHash"],
            candidateHash=candidate_hash,
            output=output.name,
        )
        write_validation_report(report_path, report)
        return output, changed
    except SidecarError as exc:
        try:
            write_validation_report(
                report_path,
                _report([Issue(exc.code, exc.path, exc.message)], stage="finalize"),
            )
        except OSError:
            pass
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        issue = Issue("finalize_failed", "$", str(exc))
        write_validation_report(report_path, _report([issue], stage="finalize"))
        raise _error(issue.code, issue.path, issue.message) from exc


def validate_finalized_sidecar_v2(
    lrc_path: Path,
    sidecar_path: Path,
    projection_path: Path,
    workdir: Path,
    learner_language: str,
) -> list[Issue]:
    workdir = workdir.expanduser().resolve()
    sidecar_path = sidecar_path.expanduser()
    if sidecar_path.is_symlink():
        return [
            Issue(
                "unsupported_source",
                "$",
                "The production sidecar must not be a symlink.",
            )
        ]
    projection = load_projection(projection_path)
    source_lrc = ensure_local_lrc(lrc_path)
    _, source_issues = verify_source_matches_projection(projection, source_lrc)
    translated = workdir / "translated.lrc"
    manifest_path = ensure_inside_workdir(
        workdir / "translation-manifest.json",
        workdir,
        "translation manifest",
    )
    manifest = read_json(manifest_path)
    _, freeze_issues = verify_frozen_translation(projection, translated, manifest)
    document, document_bytes = _read_json_and_bytes(sidecar_path)
    document_issues = validate_grammar_analysis_v2(
        document,
        projection=projection,
        expected_lrc_file=source_lrc.name,
        require_projection=True,
        learner_language=learner_language,
    )
    issues = [*source_issues, *freeze_issues, *document_issues]
    if issues:
        return issues
    try:
        candidate, _draft, candidate_hash, _audit = _validate_frozen_chain(
            projection=projection,
            workdir=workdir,
            draft_path=workdir / "semantic-draft.json",
            candidate_path=workdir / "analysis-candidate.json",
            candidate_hash_path=workdir / "analysis-candidate.sha256",
            audit_path=workdir / "semantic-audit.json",
            grammar_proof_path=workdir / "isolation-grammar-analysis.json",
            audit_proof_path=workdir / "isolation-semantic-audit.json",
            learner_language=learner_language,
            expected_lrc_file=source_lrc.name,
        )
    except SidecarError as exc:
        return [Issue(exc.code, exc.path, exc.message)]
    if not _json_equal(document, candidate):
        issues.append(
            Issue(
                "production_candidate_mismatch",
                "$",
                "Production sidecar differs from the audited analysis candidate.",
            )
        )
    if sha256_bytes(document_bytes) != candidate_hash:
        issues.append(
            Issue(
                "production_candidate_mismatch",
                "$",
                "Production sidecar bytes differ from the frozen candidate.",
            )
        )
    return issues
