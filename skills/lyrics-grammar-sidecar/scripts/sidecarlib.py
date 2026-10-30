#!/usr/bin/env python3
"""Shared implementation for the lyrics grammar sidecar workflow.

The module intentionally uses only the Python standard library. It owns the
canonical LRC projection, deterministic identities, translation handoff
checks, the closed schema-subset validator, and atomic JSON writes. Schema v2
sidecar semantics are implemented in :mod:`v2lib`.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import tempfile
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SIDECAR_SCHEMA_ID = "sakurauta.lyrics-grammar-analysis"
PROJECTION_SCHEMA_ID = "sakurauta.lyrics-grammar-source-projection"
MANIFEST_SCHEMA_ID = "sakurauta.lyrics-grammar-translation-manifest"
ISOLATION_SCHEMA_ID = "sakurauta.lyrics-grammar-isolation-proof"
SEMANTIC_AUDIT_SCHEMA_ID = "sakurauta.lyrics-grammar-semantic-audit"
REPETITION_CANDIDATE_SCHEMA_ID = "sakurauta.lyrics-grammar-repetition-candidates"
SCHEMA_VERSION = 2
PROJECTION_VERSION = "sakurauta-lrc-text-projection-v1"
LINE_ID_VERSION = "sakurauta-lyric-line-id-v1"
FINGERPRINT_VERSION = "sakurauta-lyric-text-fingerprint-v1"
MANIFEST_VERSION = 1
AUDIT_VERSION = 1
REPETITION_CANDIDATE_VERSION = 1
ISOLATION_VERSION = 2
MIN_REPETITION_CODE_POINTS = 8
ISOLATION_METHODS = {"native-subagent", "codex-exec", "documented-harness"}
ISOLATION_CONTEXT = "non-forked-transcript"
ISOLATION_STAGES = {"grammar-analysis", "semantic-audit"}
LEARNER_LANGUAGE_EXTENSION = "learnerLanguage"
CJK_LEARNER_LANGUAGES = {"zh", "ja", "ko", "yue"}

HASH_PATTERN = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
ID_PATTERN = re.compile(r"\A[a-z][a-z0-9_-]{2,63}\Z")
LRC_SUFFIX = ".lrc"
LRC_LINE_RE = re.compile(r"^\[(\d{2}):([0-5]\d)(?:\.(\d{1,3}))?\]\s*(.*)$")
CREDIT_LINE_PATTERNS = (
    re.compile(
        r"^\s*(?:词|詞|作词|作詞|作曲|曲|编曲|編曲|編词|編詞|混音|录音|錄音|"
        r"制作人|製作人|歌|唄|演唱|歌手)\s*[:：]"
    ),
    re.compile(
        r"^\s*(?:Lyrics?|Music|Arrangement|Arrange|Composed|Composer|Produced|"
        r"Producer|Singer|Vocal|Vo\.?)\s*(?:by\s*)?[:：]",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:Lyrics?|Music|Arrangement|Composed|Produced)\s+by\s+\S",
        re.IGNORECASE,
    ),
)
HTML_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>")
HTML_DECL_RE = re.compile(r"<![A-Za-z]|<\?", re.IGNORECASE)
HTML_COMMENT_RE = re.compile(r"<!--")
HTML_ENTITY_RE = re.compile(r"&(?:#\d+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]+);")
FORBIDDEN_TRANSLATION_KEY_RE = re.compile(r"translat", re.IGNORECASE)
ABSOLUTE_PATH_RE = re.compile(
    r"^(?:[A-Za-z]:[\\/]|\\\\|/(?:[^/\s]+/)+|file://)",
    re.IGNORECASE,
)
WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"[A-Za-z]:[\\/]")
FILE_URI_RE = re.compile(r"file://", re.IGNORECASE)
REMOTE_PATH_RE = re.compile(r"^(?:https?|ftp|s3|gs|ssh|file):(?://|[\\/])", re.IGNORECASE)


class SidecarError(Exception):
    """A fatal contract error with a stable machine-readable code."""

    def __init__(self, code: str, message: str, path: str = "$") -> None:
        super().__init__(message)
        self.code = code
        self.path = path
        self.message = message

    def as_issue(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class Issue:
    code: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


class _DuplicateJsonKey(ValueError):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


class _NonFiniteJsonNumber(ValueError):
    def __init__(self, value: str) -> None:
        super().__init__(value)
        self.value = value


def _reject_duplicate_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _parse_finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise _NonFiniteJsonNumber(value)
    return parsed


def _reject_json_constant(value: str) -> None:
    raise _NonFiniteJsonNumber(value)


def _contains_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(char) <= 0xDFFF for char in value)


def json_value_issues(value: Any, path: str = "$") -> list[Issue]:
    """Return issues for Python values that cannot be represented as strict JSON."""

    issues: list[Issue] = []
    if isinstance(value, str):
        if _contains_surrogate(value):
            issues.append(
                Issue(
                    "invalid_unicode",
                    path,
                    "Unpaired UTF-16 surrogate code points are not valid UTF-8 text.",
                )
            )
    elif isinstance(value, float) and not math.isfinite(value):
        issues.append(Issue("invalid_number", path, "JSON numbers must be finite."))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            issues.extend(json_value_issues(item, f"{path}[{index}]"))
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                issues.append(
                    Issue("invalid_json_object", path, "JSON object keys must be strings.")
                )
                continue
            key_path = f"{path}.{key}"
            if _contains_surrogate(key):
                issues.append(
                    Issue(
                        "invalid_unicode",
                        key_path,
                        "JSON object keys must not contain unpaired surrogate code points.",
                    )
                )
            issues.extend(json_value_issues(item, key_path))
    return issues


def parse_json_bytes(data: bytes, label: str = "JSON") -> Any:
    """Decode strict UTF-8 JSON without duplicate keys or non-finite numbers."""

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SidecarError("invalid_utf8", f"File is not valid UTF-8: {label}") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_pairs,
            parse_float=_parse_finite_json_float,
            parse_constant=_reject_json_constant,
        )
    except _DuplicateJsonKey as exc:
        raise SidecarError(
            "invalid_json",
            "$",
            f"Duplicate JSON object key in {label}: {exc.key}",
        ) from exc
    except _NonFiniteJsonNumber as exc:
        raise SidecarError(
            "invalid_json",
            "$",
            f"Non-finite JSON number in {label}: {exc.value}",
        ) from exc
    except json.JSONDecodeError as exc:
        raise SidecarError(
            "invalid_json",
            "$",
            f"Invalid JSON in {label}: line {exc.lineno}, column {exc.colno}",
        ) from exc
    except (RecursionError, ValueError) as exc:
        raise SidecarError("invalid_json", "$", f"Invalid JSON in {label}: {exc}") from exc

    issues = json_value_issues(value)
    if issues:
        issue = issues[0]
        raise SidecarError("invalid_json", issue.path, issue.message)
    return value


@dataclass(frozen=True)
class LrcRow:
    line_number: int
    timestamps: tuple[int, ...]
    text: str
    raw: str


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def pretty_json(value: Any) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )


def configure_utf8_stdio() -> None:
    sys.dont_write_bytecode = True
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    if path.is_symlink():
        raise SidecarError(
            "unsupported_source",
            f"Symlinked files are not supported: {path}",
        )
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def assert_distinct_paths(*paths: tuple[str, Path | None]) -> None:
    """Reject aliases between readable artifacts and write destinations."""

    seen: dict[str, str] = {}
    for label, path in paths:
        if path is None:
            continue
        resolved = path.expanduser().resolve()
        key = os.path.normcase(str(resolved))
        if key in seen:
            raise SidecarError(
                "artifact_path_collision",
                "$",
                f"{label} must not alias {seen[key]}.",
            )
        seen[key] = label


def ensure_inside_workdir(path: Path, directory: Path, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise SidecarError(
            "artifact_symlink",
            "$",
            f"{label} must not be a symlink.",
        )
    resolved = expanded.resolve()
    root = directory.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SidecarError(
            "artifact_outside_workdir",
            "$",
            f"{label} must live inside the grammar work directory.",
        ) from exc
    return resolved


def read_json(path: Path) -> Any:
    if path.is_symlink():
        raise SidecarError(
            "unsupported_source",
            f"Symlinked JSON inputs are not supported: {path}",
        )
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise SidecarError("file_not_found", f"File not found: {path}") from exc
    except OSError as exc:
        raise SidecarError("file_read_failed", f"Could not read JSON file: {path}") from exc
    return parse_json_bytes(data, path.name)


def ensure_local_lrc(path: Path) -> Path:
    raw = str(path)
    if REMOTE_PATH_RE.match(raw) or raw.startswith(("\\\\", "//")):
        raise SidecarError(
            "unsupported_source",
            "Remote and network-share inputs are not supported; use a local .lrc file.",
        )
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise SidecarError(
            "unsupported_source",
            "Symlinked LRC inputs are not supported.",
        )
    resolved = expanded.resolve()
    if resolved.suffix != LRC_SUFFIX:
        raise SidecarError(
            "unsupported_source",
            "The grammar sidecar generator accepts only a local .lrc file.",
        )
    if not resolved.is_file():
        raise SidecarError("file_not_found", f"LRC file not found: {resolved}")
    return resolved


def lrc_basename_issues(value: Any, path: str) -> list[Issue]:
    if not isinstance(value, str) or not value:
        return [
            Issue(
                "invalid_lrc_basename",
                path,
                "lrcFile must be a non-empty LRC basename.",
            )
        ]
    issues: list[Issue] = []
    if value in {".", ".."} or value.startswith("."):
        issues.append(
            Issue(
                "invalid_lrc_basename",
                path,
                "lrcFile must name a visible sibling LRC file.",
            )
        )
    if value != value.strip():
        issues.append(
            Issue(
                "invalid_lrc_basename",
                path,
                "lrcFile must not begin or end with whitespace.",
            )
        )
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        issues.append(
            Issue(
                "invalid_lrc_basename",
                path,
                "lrcFile must not contain control characters.",
            )
        )
    if any(char in '<>:"/\\|?*' for char in value):
        issues.append(
            Issue(
                "invalid_lrc_basename",
                path,
                "lrcFile must be one portable Windows filename.",
            )
        )
    if not value.endswith(LRC_SUFFIX):
        issues.append(
            Issue(
                "invalid_lrc_basename",
                path,
                "lrcFile must end in .lrc.",
            )
        )
    return issues


def _timestamp_ms(minutes: str, seconds: str, fraction: str | None) -> int:
    millis = int((fraction or "0").ljust(3, "0")[:3])
    return (int(minutes) * 60 + int(seconds)) * 1000 + millis


def _is_credit_line(text: str) -> bool:
    trimmed = text.strip()
    return bool(trimmed) and any(
        pattern.search(trimmed) for pattern in CREDIT_LINE_PATTERNS
    )


def parse_lrc_rows(path: Path) -> list[LrcRow]:
    """Return timestamped, non-empty lyric rows in physical file order.

    Mirrors the App's LRC parsing contract: one leading timestamp per row,
    optional 1-3 fractional digits, leading whitespace consumed, credit lines
    excluded, and only whitespace-empty rows discarded.
    """

    lrc_path = ensure_local_lrc(path)
    try:
        text = lrc_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SidecarError("invalid_utf8", f"LRC file is not valid UTF-8: {lrc_path}") from exc

    rows: list[LrcRow] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        if "\x00" in raw:
            raise SidecarError(
                "invalid_lrc",
                f"NUL character in LRC line {line_number}.",
            )

        match = LRC_LINE_RE.match(raw)
        if match is None:
            continue

        minutes, seconds, fraction, lyric_text = match.groups()
        if not lyric_text.strip() or _is_credit_line(lyric_text):
            continue
        rows.append(
            LrcRow(
                line_number=line_number,
                timestamps=(_timestamp_ms(minutes, seconds, fraction),),
                text=lyric_text,
                raw=raw,
            )
        )
    return rows


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def grammar_line_fingerprint(text: str) -> str:
    return "sha256:" + _sha256_hex(f"{FINGERPRINT_VERSION}\n{_nfc(text)}")


def grammar_line_id(
    song_id: str,
    time_ms: int,
    same_timestamp_ordinal: int,
    text: str,
) -> str:
    payload = "\n".join(
        [
            LINE_ID_VERSION,
            song_id,
            str(time_ms),
            str(same_timestamp_ordinal),
            "text",
            _nfc(text),
        ]
    )
    return "l_" + _sha256_hex(payload)[:16]


def _projection_hash(song_id: str, lines: Sequence[dict[str, Any]]) -> str:
    payload = "\n".join(
        [
            PROJECTION_VERSION,
            song_id,
            str(len(lines)),
            *[
                f"{line['timeMs']}\0{line['sameTimestampOrdinal']}\0{_nfc(line['text'])}"
                for line in lines
            ],
        ]
    )
    return "sha256:" + _sha256_hex(payload)


def source_projection_from_lrc(path: Path, song_id: str) -> dict[str, Any]:
    lrc_path = ensure_local_lrc(path)
    if not isinstance(song_id, str) or not song_id.strip():
        raise SidecarError("missing_song_id", "A non-empty songId is required.")
    rows = parse_lrc_rows(lrc_path)
    if not rows:
        raise SidecarError("empty_source", "No timestamped lyric lines were found.")

    projected: list[dict[str, Any]] = []
    ordinals: dict[int, int] = {}
    seen_times: set[int] = set()
    seen_ids: set[str] = set()
    source_order = 0

    for row in rows:
        for time_ms in row.timestamps:
            # The first lexical source row at a timestamp is authoritative.
            # A translated row inserted below it shares that timestamp and is
            # therefore ignored by the canonical source projection.
            if time_ms in seen_times:
                continue
            seen_times.add(time_ms)
            same_timestamp_ordinal = ordinals.get(time_ms, 0)
            ordinals[time_ms] = same_timestamp_ordinal + 1
            line_id = grammar_line_id(
                song_id, time_ms, same_timestamp_ordinal, row.text
            )
            fingerprint = grammar_line_fingerprint(row.text)
            if line_id in seen_ids:
                raise SidecarError(
                    "line_id_collision",
                    f"Generated lineId collision for line {row.line_number}.",
                )
            seen_ids.add(line_id)
            projected.append(
                {
                    "lineId": line_id,
                    "sourceOrder": source_order,
                    "timeMs": time_ms,
                    "text": row.text,
                    "fingerprint": fingerprint,
                    "role": "text",
                    "sameTimestampOrdinal": same_timestamp_ordinal,
                }
            )
            source_order += 1

    projection = {
        "schema": PROJECTION_SCHEMA_ID,
        "projectionVersion": PROJECTION_VERSION,
        "lrcFile": lrc_path.name,
        "songId": song_id,
        "lineCount": len(projected),
        "lrcProjectionHash": _projection_hash(song_id, projected),
        "lines": projected,
    }
    return projection


def validate_projection(projection: Any) -> list[Issue]:
    issues: list[Issue] = []
    if not isinstance(projection, dict):
        return [Issue("invalid_projection", "$", "Projection must be a JSON object.")]

    required_keys = {"songId", "lineCount", "lrcProjectionHash", "lines"}
    optional_keys = {"schema", "projectionVersion", "lrcFile"}
    allowed_keys = required_keys | optional_keys
    for key in sorted(set(projection) - allowed_keys):
        issues.append(
            Issue("invalid_projection", f"$.{key}", f"Unknown projection field: {key}")
        )
    for key in sorted(required_keys - set(projection)):
        issues.append(Issue("invalid_projection", "$", f"Missing projection field: {key}"))

    if "schema" in projection and projection.get("schema") != PROJECTION_SCHEMA_ID:
        issues.append(Issue("invalid_projection", "$.schema", "Unexpected projection schema."))
    if (
        "projectionVersion" in projection
        and projection.get("projectionVersion") != PROJECTION_VERSION
    ):
        issues.append(
            Issue(
                "unsupported_projection_version",
                "$.projectionVersion",
                f"Expected {PROJECTION_VERSION}.",
            )
        )
    song_id = projection.get("songId")
    if not isinstance(song_id, str) or not song_id.strip():
        issues.append(
            Issue("invalid_projection", "$.songId", "Expected a non-empty songId.")
        )
        song_id = ""

    if "lrcFile" in projection:
        lrc_file = projection.get("lrcFile")
        issues.extend(lrc_basename_issues(lrc_file, "$.lrcFile"))

    lines = projection.get("lines")
    if not isinstance(lines, list) or not lines:
        issues.append(Issue("invalid_projection", "$.lines", "Projection lines must be non-empty."))
        return issues

    if projection.get("lineCount") != len(lines):
        issues.append(
            Issue("invalid_projection", "$.lineCount", "lineCount does not match lines.")
        )

    ordinals: dict[int, int] = {}
    seen_ids: set[str] = set()
    seen_times: set[int] = set()
    normalized_lines: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        path = f"$.lines[{index}]"
        if not isinstance(line, dict):
            issues.append(Issue("invalid_projection", path, "Line must be an object."))
            continue
        required = {
            "lineId",
            "sourceOrder",
            "timeMs",
            "text",
            "fingerprint",
            "role",
            "sameTimestampOrdinal",
        }
        if set(line) != required:
            issues.append(
                Issue("invalid_projection", path, "Line fields do not match projection v1.")
            )
            continue
        line_id = line.get("lineId")
        time_ms = line.get("timeMs")
        text = line.get("text")
        source_order = line.get("sourceOrder")
        same_timestamp_ordinal = line.get("sameTimestampOrdinal")
        if (
            not isinstance(line_id, str)
            or not isinstance(time_ms, int)
            or isinstance(time_ms, bool)
            or time_ms < 0
            or not isinstance(text, str)
            or not text
            or not isinstance(source_order, int)
            or isinstance(source_order, bool)
            or source_order != index
            or line.get("role") != "text"
            or not isinstance(same_timestamp_ordinal, int)
            or isinstance(same_timestamp_ordinal, bool)
            or same_timestamp_ordinal < 0
        ):
            issues.append(Issue("invalid_projection", path, "Invalid projected line values."))
            continue
        if ID_PATTERN.fullmatch(line_id) is None:
            issues.append(Issue("invalid_projection", path + ".lineId", "Invalid lineId form."))
        if time_ms in seen_times:
            issues.append(
                Issue(
                    "duplicate_timestamp",
                    path + ".timeMs",
                    "Projection may contain only the first source line at a timestamp.",
                )
            )
        seen_times.add(time_ms)
        expected_ordinal = ordinals.get(time_ms, 0)
        ordinals[time_ms] = expected_ordinal + 1
        if same_timestamp_ordinal != expected_ordinal:
            issues.append(
                Issue(
                    "invalid_projection",
                    path + ".sameTimestampOrdinal",
                    "sameTimestampOrdinal is not canonical.",
                )
            )
        expected_id = grammar_line_id(
            song_id, time_ms, same_timestamp_ordinal, text
        )
        expected_fingerprint = grammar_line_fingerprint(text)
        if line_id != expected_id:
            issues.append(Issue("invalid_projection", path + ".lineId", "lineId is not canonical."))
        if line.get("fingerprint") != expected_fingerprint:
            issues.append(
                Issue("invalid_projection", path + ".fingerprint", "Fingerprint is not canonical.")
            )
        if line_id in seen_ids:
            issues.append(Issue("duplicate_id", path + ".lineId", "Duplicate lineId."))
        seen_ids.add(line_id)
        normalized_lines.append(line)

    if len(normalized_lines) == len(lines):
        expected_hash = _projection_hash(song_id, normalized_lines)
        if projection.get("lrcProjectionHash") != expected_hash:
            issues.append(
                Issue(
                    "invalid_projection",
                    "$.lrcProjectionHash",
                    "Projection hash does not match canonical line data.",
                )
            )
    return issues


def load_projection(path: Path) -> dict[str, Any]:
    projection = read_json(path)
    issues = validate_projection(projection)
    if issues:
        raise SidecarError(issues[0].code, issues[0].message, issues[0].path)
    return projection


def verify_source_matches_projection(
    projection: dict[str, Any], source_lrc: Path
) -> tuple[dict[str, Any], list[Issue]]:
    lrc_path = ensure_local_lrc(source_lrc)
    issues: list[Issue] = []
    if projection.get("lrcFile") is not None and lrc_path.name != projection.get("lrcFile"):
        issues.append(
            Issue(
                "lrc_file_mismatch",
                "$.source.lrcFile",
                "Projection belongs to a different LRC basename.",
            )
        )

    current = source_projection_from_lrc(lrc_path, projection["songId"])
    if current["lrcProjectionHash"] != projection["lrcProjectionHash"]:
        issues.append(
            Issue(
                "stale_source_projection",
                "$.source.lrcProjectionHash",
                "The source LRC text or timeline changed after the projection was created.",
            )
        )
    report = {
        "ok": not issues,
        "projectionHash": projection["lrcProjectionHash"],
        "currentProjectionHash": current["lrcProjectionHash"],
        "lineCount": current["lineCount"],
    }
    return report, issues


def verify_translation_handoff(
    projection: dict[str, Any], translated_lrc: Path
) -> tuple[dict[str, Any], list[Issue]]:
    """Verify that inserted rows did not alter the canonical source projection.

    For every rounded-millisecond timestamp, only the first lexical row is
    canonical source input. This matches SakuraUta's duplicate-timestamp rule
    and permits a bilingual LRC while translation text stays outside it.
    """

    issues: list[Issue] = []
    try:
        rows = parse_lrc_rows(translated_lrc)
    except SidecarError as exc:
        return (
            {
                "ok": False,
                "sourceProjectionHash": projection.get("lrcProjectionHash"),
                "sourceLinesMatched": 0,
                "translationLines": 0,
            },
            [Issue(exc.code, exc.path, exc.message)],
        )

    expected_lines = projection["lines"]
    expected_times = {line["timeMs"] for line in expected_lines}
    canonical_entries: list[tuple[int, str, int, tuple[int, ...]]] = []
    seen_times: set[int] = set()
    extra_rows: list[LrcRow] = []
    for row in rows:
        primary_times: list[int] = []
        for time_ms in row.timestamps:
            if time_ms not in seen_times:
                primary_times.append(time_ms)
                seen_times.add(time_ms)
        if not primary_times:
            extra_rows.append(row)
            continue
        if len(primary_times) != len(row.timestamps):
            issues.append(
                Issue(
                    "unsupported_source_shape",
                    f"$lrc.line[{row.line_number}]",
                    "Multi-timestamp rows cannot be represented as a bilingual "
                    "source/translation pair in the canonical handoff.",
                )
            )
            break
        canonical_entries.append(
            (primary_times[0], row.text, row.line_number, row.timestamps)
        )

    source_lines_matched = 0
    for index, expected in enumerate(expected_lines):
        if index >= len(canonical_entries):
            break
        first_time, text, line_number, all_times = canonical_entries[index]
        # Multi-timestamp rows project once but must not fabricate timestamps
        # outside the frozen projection.
        if (
            first_time != expected["timeMs"]
            or text != expected["text"]
            or any(item not in expected_times for item in all_times)
        ):
            issues.append(
                Issue(
                    "stale_source_projection",
                    f"$lrc.line[{line_number}]",
                    "Canonical source text or timeline does not match the frozen projection.",
                )
            )
            break
        source_lines_matched += 1

    if len(canonical_entries) != len(expected_lines):
        issues.append(
            Issue(
                "stale_source_projection",
                "$lrc",
                f"Expected {len(expected_lines)} canonical source rows, "
                f"found {len(canonical_entries)}.",
            )
        )
    canonical_line_numbers = {entry[2] for entry in canonical_entries}
    previous_row: LrcRow | None = None
    for row in rows:
        if row.line_number in canonical_line_numbers:
            previous_row = row
            continue
        if (
            previous_row is None
            or previous_row.line_number not in canonical_line_numbers
            or previous_row.timestamps != row.timestamps
        ):
            issues.append(
                Issue(
                    "stale_source_projection",
                    f"$lrc.line[{row.line_number}]",
                    "Each translation row must immediately follow its same-timestamp "
                    "canonical source row.",
                )
            )
            break
        previous_row = row
    extra_counts: dict[int, int] = {}
    for row in extra_rows:
        if not row.timestamps:
            issues.append(
                Issue(
                    "stale_source_projection",
                    f"$lrc.line[{row.line_number}]",
                    "A translation row must retain at least one source timestamp.",
                )
            )
            break
        if any(time_ms not in expected_times for time_ms in row.timestamps):
            issues.append(
                Issue(
                    "stale_source_projection",
                    f"$lrc.line[{row.line_number}]",
                    "Inserted row contains a timestamp absent from the source projection.",
                )
            )
            break
        for time_ms in row.timestamps:
            extra_counts[time_ms] = extra_counts.get(time_ms, 0) + 1
        if any(extra_counts[time_ms] > 1 for time_ms in row.timestamps):
            issues.append(
                Issue(
                    "stale_source_projection",
                    f"$lrc.line[{row.line_number}]",
                    "More than one translation row was inserted for a source timestamp.",
                )
            )
            break

    translation_lines = len(extra_rows)
    if not issues and not translation_lines:
        issues.append(
            Issue(
                "missing_translation",
                "$lrc",
                "The bilingual LRC contains no inserted translation rows.",
            )
        )

    report = {
        "ok": not issues,
        "sourceProjectionHash": projection["lrcProjectionHash"],
        "sourceLinesMatched": source_lines_matched,
        "translationLines": translation_lines,
    }
    return report, issues


def build_translation_manifest(
    projection: dict[str, Any], translated_lrc: Path
) -> tuple[dict[str, Any], list[Issue]]:
    lrc_path = ensure_local_lrc(translated_lrc)
    handoff, issues = verify_translation_handoff(projection, lrc_path)
    if issues:
        return handoff, issues
    manifest = {
        "schema": MANIFEST_SCHEMA_ID,
        "manifestVersion": MANIFEST_VERSION,
        "lrcFile": projection.get("lrcFile", lrc_path.name),
        "lineCount": projection["lineCount"],
        "lrcProjectionHash": projection["lrcProjectionHash"],
        "translatedLrcSha256": sha256_file(lrc_path),
    }
    return manifest, []


def write_accepted_translation(
    projection: dict[str, Any],
    accepted_lrc: Path,
    workdir: Path,
) -> tuple[Path, dict[str, Any], list[Issue]]:
    """Freeze an already-translated bilingual LRC into the work directory."""

    workdir = workdir.expanduser().resolve()
    if not workdir.is_dir():
        raise SidecarError("invalid_workdir", f"Work directory not found: {workdir}")
    accepted_lrc = ensure_local_lrc(accepted_lrc)
    translated_lrc = workdir / "translated.lrc"
    manifest_path = workdir / "translation-manifest.json"
    if translated_lrc.exists() or manifest_path.exists():
        raise SidecarError(
            "translation_already_frozen",
            "The work directory already contains a frozen translation.",
        )
    data = accepted_lrc.read_bytes()
    manifest, issues = build_translation_manifest(projection, accepted_lrc)
    if issues:
        return translated_lrc, manifest, issues
    _write_bytes_atomic(translated_lrc, data)
    if sha256_file(translated_lrc) != manifest["translatedLrcSha256"]:
        translated_lrc.unlink(missing_ok=True)
        return translated_lrc, manifest, [
            Issue(
                "translation_changed_after_freeze",
                "$.translatedLrcSha256",
                "The accepted translation changed while it was being frozen.",
            )
        ]
    write_json_atomic(manifest_path, manifest, replace=False)
    return translated_lrc, manifest, []


def validate_manifest(manifest: Any) -> list[Issue]:
    if not isinstance(manifest, dict):
        return [Issue("invalid_manifest", "$", "Translation manifest must be an object.")]
    required = {
        "schema",
        "manifestVersion",
        "lrcFile",
        "lineCount",
        "lrcProjectionHash",
        "translatedLrcSha256",
    }
    issues: list[Issue] = []
    if set(manifest) != required:
        issues.append(
            Issue(
                "invalid_manifest",
                "$",
                "Manifest fields do not match translation manifest v1.",
            )
        )
    if manifest.get("schema") != MANIFEST_SCHEMA_ID:
        issues.append(Issue("invalid_manifest", "$.schema", "Unexpected manifest schema."))
    if manifest.get("manifestVersion") != MANIFEST_VERSION:
        issues.append(
            Issue("unsupported_manifest_version", "$.manifestVersion", "Expected version 1.")
        )
    if not isinstance(manifest.get("lrcFile"), str) or not manifest["lrcFile"].lower().endswith(
        LRC_SUFFIX
    ):
        issues.append(Issue("invalid_manifest", "$.lrcFile", "Expected an LRC basename."))
    line_count = manifest.get("lineCount")
    if (
        not isinstance(line_count, int)
        or isinstance(line_count, bool)
        or line_count < 1
    ):
        issues.append(Issue("invalid_manifest", "$.lineCount", "Expected a positive integer."))
    hash_value = manifest.get("translatedLrcSha256")
    if not isinstance(hash_value, str) or HASH_PATTERN.fullmatch(hash_value) is None:
        issues.append(
            Issue("invalid_manifest", "$.translatedLrcSha256", "Invalid translated LRC hash.")
        )
    projection_hash = manifest.get("lrcProjectionHash")
    if not isinstance(projection_hash, str) or HASH_PATTERN.fullmatch(projection_hash) is None:
        issues.append(
            Issue("invalid_manifest", "$.lrcProjectionHash", "Invalid projection hash.")
        )
    return issues


def verify_frozen_translation(
    projection: dict[str, Any],
    translated_lrc: Path,
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], list[Issue]]:
    issues = validate_manifest(manifest)
    if issues:
        return (
            {
                "ok": False,
                "sourceProjectionHash": projection.get("lrcProjectionHash"),
                "sourceLinesMatched": 0,
                "translationLines": 0,
            },
            issues,
        )
    handoff, handoff_issues = verify_translation_handoff(projection, translated_lrc)
    issues.extend(handoff_issues)

    if manifest.get("lrcProjectionHash") != projection.get("lrcProjectionHash"):
        issues.append(
            Issue(
                "stale_projection_hash",
                "$.lrcProjectionHash",
                "Translation manifest belongs to a different source projection.",
            )
        )
    if manifest.get("lineCount") != projection.get("lineCount"):
        issues.append(
            Issue("invalid_manifest", "$.lineCount", "Manifest lineCount does not match.")
        )
    expected_lrc_file = projection.get("lrcFile", translated_lrc.name)
    if manifest.get("lrcFile") != expected_lrc_file:
        issues.append(
            Issue("invalid_manifest", "$.lrcFile", "Manifest lrcFile does not match.")
        )

    actual_hash = sha256_file(translated_lrc)
    if manifest.get("translatedLrcSha256") != actual_hash:
        issues.append(
            Issue(
                "translation_changed_after_freeze",
                "$.translatedLrcSha256",
                "The translated LRC changed after the accepted translation was frozen.",
            )
        )

    report = dict(handoff)
    report["ok"] = not issues
    report["translatedLrcSha256"] = actual_hash
    return report, issues


SUPPORTED_SCHEMA_KEYWORDS = {
    "$schema",
    "$id",
    "$defs",
    "$ref",
    "title",
    "description",
    "type",
    "additionalProperties",
    "required",
    "properties",
    "items",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minLength",
    "maxLength",
    "pattern",
    "minimum",
    "maximum",
    "enum",
    "const",
}


def _walk_schema(schema: Any, path: str = "$schema") -> list[Issue]:
    issues: list[Issue] = []
    if not isinstance(schema, dict):
        return [Issue("invalid_schema", path, "Schema node must be an object.")]
    for key in sorted(set(schema) - SUPPORTED_SCHEMA_KEYWORDS):
        issues.append(
            Issue(
                "unsupported_schema_keyword",
                f"{path}.{key}",
                f"Unsupported schema keyword: {key}",
            )
        )
    if "pattern" in schema:
        try:
            re.compile(schema["pattern"])
        except (re.error, TypeError) as exc:
            issues.append(
                Issue("invalid_schema", f"{path}.pattern", f"Invalid regex pattern: {exc}")
            )
    if "$ref" in schema and len(schema) != 1:
        issues.append(
            Issue(
                "invalid_schema",
                path,
                "$ref nodes may not contain sibling schema keywords.",
            )
        )
    if "$defs" in schema and isinstance(schema["$defs"], dict):
        for name, child in schema["$defs"].items():
            issues.extend(_walk_schema(child, f"{path}.$defs.{name}"))
    if "properties" in schema and isinstance(schema["properties"], dict):
        for name, child in schema["properties"].items():
            issues.extend(_walk_schema(child, f"{path}.properties.{name}"))
    if "items" in schema:
        issues.extend(_walk_schema(schema["items"], f"{path}.items"))
    if isinstance(schema.get("additionalProperties"), dict):
        issues.extend(
            _walk_schema(schema["additionalProperties"], f"{path}.additionalProperties")
        )
    return issues


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "unknown"


def _type_matches(value: Any, expected: str) -> bool:
    actual = _json_type(value)
    if expected == "number":
        return actual in {"integer", "number"}
    return actual == expected


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right)
        )
    if isinstance(left, dict):
        return set(left) == set(right) and all(
            _json_equal(left[key], right[key]) for key in left
        )
    return left == right


def _resolve_ref(root: dict[str, Any], reference: str) -> tuple[dict[str, Any] | None, str]:
    if not reference.startswith("#/"):
        return None, f"Only local $ref values are supported: {reference}"
    node: Any = root
    for part in reference[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return None, f"Unresolved $ref: {reference}"
        node = node[part]
    if not isinstance(node, dict):
        return None, f"$ref does not resolve to a schema object: {reference}"
    return node, ""


def validate_schema_instance(
    instance: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any] | None = None,
    path: str = "$",
) -> list[Issue]:
    """Validate the deliberately small, closed schema subset used by v2."""

    if root_schema is None:
        value_issues = json_value_issues(instance, path=path)
        if value_issues:
            return value_issues
    root = root_schema or schema
    issues: list[Issue] = []

    if "$ref" in schema:
        target, error = _resolve_ref(root, schema["$ref"])
        if target is None:
            return [Issue("invalid_schema", path, error)]
        return validate_schema_instance(instance, target, root, path)

    expected_type = schema.get("type")
    if expected_type is not None:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not all(isinstance(item, str) for item in types):
            return [Issue("invalid_schema", path, "Schema type must be a string or array.")]
        if not any(_type_matches(instance, item) for item in types):
            issues.append(
                Issue(
                    "schema_type",
                    path,
                    f"Expected type {types}, got {_json_type(instance)}.",
                )
            )
            return issues
    if (
        "minimum" in schema
        and isinstance(instance, (int, float))
        and not isinstance(instance, bool)
        and instance < schema["minimum"]
    ):
        issues.append(
            Issue("schema_minimum", path, f"Value must be at least {schema['minimum']}.")
        )
    if (
        "maximum" in schema
        and isinstance(instance, (int, float))
        and not isinstance(instance, bool)
        and instance > schema["maximum"]
    ):
        issues.append(
            Issue("schema_maximum", path, f"Value must be at most {schema['maximum']}.")
        )
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            issues.append(
                Issue(
                    "schema_min_length",
                    path,
                    f"Expected at least {schema['minLength']} characters.",
                )
            )
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            issues.append(
                Issue(
                    "schema_max_length",
                    path,
                    f"Expected at most {schema['maxLength']} characters.",
                )
            )
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            issues.append(Issue("schema_pattern", path, "String does not match pattern."))

    if "const" in schema and not _json_equal(instance, schema["const"]):
        issues.append(Issue("schema_const", path, f"Expected constant {schema['const']!r}."))
    if "enum" in schema and not any(_json_equal(instance, item) for item in schema["enum"]):
        issues.append(Issue("schema_enum", path, "Value is not in the allowed enum."))

    if isinstance(instance, dict):
        required = schema.get("required", [])
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in instance:
                    issues.append(Issue("schema_required", f"{path}.{key}", "Required field."))
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            issues.append(Issue("invalid_schema", path, "properties must be an object."))
            properties = {}
        for key, value in instance.items():
            if key in properties and isinstance(properties[key], dict):
                issues.extend(
                    validate_schema_instance(
                        value, properties[key], root, f"{path}.{key}"
                    )
                )
                continue
            additional = schema.get("additionalProperties", True)
            if additional is False:
                issues.append(
                    Issue("schema_additional_property", f"{path}.{key}", "Unknown field.")
                )
            elif isinstance(additional, dict):
                issues.extend(
                    validate_schema_instance(value, additional, root, f"{path}.{key}")
                )

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            issues.append(
                Issue("schema_min_items", path, f"Expected at least {schema['minItems']} items.")
            )
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            issues.append(
                Issue("schema_max_items", path, f"Expected at most {schema['maxItems']} items.")
            )
        if schema.get("uniqueItems") is True:
            seen: list[Any] = []
            for index, item in enumerate(instance):
                if any(_json_equal(item, previous) for previous in seen):
                    issues.append(
                        Issue(
                            "schema_unique_items",
                            f"{path}[{index}]",
                            "Duplicate array item.",
                        )
                    )
                seen.append(item)
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(instance):
                issues.extend(
                    validate_schema_instance(
                        item, schema["items"], root, f"{path}[{index}]"
                    )
                )

    return issues


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    if path.is_symlink():
        raise SidecarError(
            "artifact_symlink",
            "$",
            f"Refusing to write through a symlink: {path}",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def write_json_atomic(path: Path, value: Any, replace: bool = False) -> bool:
    if path.is_symlink():
        raise SidecarError(
            "artifact_symlink",
            "$",
            f"Refusing to write through a symlink: {path}",
        )
    data = pretty_json(value).encode("utf-8")
    if path.exists():
        existing = path.read_bytes()
        if existing == data:
            return False
        if not replace:
            raise SidecarError(
                "output_exists",
                f"Refusing to overwrite a different file without --replace: {path}",
            )
    _write_bytes_atomic(path, data)
    return True
