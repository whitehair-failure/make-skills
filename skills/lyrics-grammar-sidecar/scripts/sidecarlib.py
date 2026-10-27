#!/usr/bin/env python3
"""Shared implementation for the lyrics grammar sidecar workflow.

The module intentionally uses only the Python standard library.  It owns the
canonical LRC projection, deterministic identities, schema validation,
cross-reference validation, translation handoff checks, and atomic JSON writes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


SIDECAR_SCHEMA_ID = "sakurauta.lyrics-grammar-analysis"
PROJECTION_SCHEMA_ID = "sakurauta.lyrics-grammar-source-projection"
MANIFEST_SCHEMA_ID = "sakurauta.lyrics-grammar-translation-manifest"
ISOLATION_SCHEMA_ID = "sakurauta.lyrics-grammar-isolation-proof"
SCHEMA_VERSION = 1
PROJECTION_VERSION = "sakurauta-lrc-text-projection-v1"
LINE_ID_VERSION = "sakurauta-lyric-line-id-v1"
FINGERPRINT_VERSION = "sakurauta-lyric-text-fingerprint-v1"
MANIFEST_VERSION = 1
ISOLATION_VERSION = 1
ISOLATION_METHODS = {"native-subagent", "codex-exec", "documented-harness"}
ISOLATION_CONTEXT = "non-forked-transcript"
LEARNER_LANGUAGE_EXTENSION = "learnerLanguage"
CJK_LEARNER_LANGUAGES = {"zh", "ja", "ko", "yue"}

HASH_PATTERN = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
ID_PATTERN = re.compile(r"\A[a-z][a-z0-9_-]{2,63}\Z")
LANGUAGE_PATTERN = re.compile(r"\A[a-z]{2,3}(?:-[A-Za-z0-9]+)*\Z")
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
HTML_ENTITY_RE = re.compile(r"&(?:#\d+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]+);")
FORBIDDEN_TRANSLATION_KEY_RE = re.compile(r"translat", re.IGNORECASE)
ABSOLUTE_PATH_RE = re.compile(
    r"^(?:[A-Za-z]:[\\/]|\\\\|/(?:[^/\s]+/)+|file://)",
    re.IGNORECASE,
)
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


@dataclass(frozen=True)
class LrcRow:
    line_number: int
    timestamps: tuple[int, ...]
    text: str
    raw: str


def default_schema_path() -> Path:
    override = os.environ.get("LYRICS_GRAMMAR_SCHEMA")
    if override:
        return Path(override).expanduser().resolve()
    return (
        Path(__file__).resolve().parents[1]
        / "schema"
        / "v1"
        / "lyrics-grammar-analysis.schema.json"
    )


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def configure_utf8_stdio() -> None:
    sys.dont_write_bytecode = True
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SidecarError("file_not_found", f"File not found: {path}") from exc
    except UnicodeDecodeError as exc:
        raise SidecarError("invalid_utf8", f"File is not valid UTF-8: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SidecarError(
            "invalid_json",
            f"Invalid JSON in {path.name}: line {exc.lineno}, column {exc.colno}",
        ) from exc


def ensure_local_lrc(path: Path) -> Path:
    raw = str(path)
    if REMOTE_PATH_RE.match(raw) or raw.startswith("\\\\"):
        raise SidecarError(
            "unsupported_source",
            "Remote and network-share inputs are not supported; use a local .lrc file.",
        )
    resolved = path.expanduser().resolve()
    if resolved.suffix != LRC_SUFFIX:
        raise SidecarError(
            "unsupported_source",
            "The grammar sidecar generator accepts only a local .lrc file.",
        )
    if not resolved.is_file():
        raise SidecarError("file_not_found", f"LRC file not found: {resolved}")
    return resolved


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
        if not isinstance(lrc_file, str) or not lrc_file.endswith(LRC_SUFFIX):
            issues.append(
                Issue("invalid_projection", "$.lrcFile", "Expected an LRC basename.")
            )
        elif "/" in lrc_file or "\\" in lrc_file:
            issues.append(
                Issue("absolute_path", "$.lrcFile", "lrcFile must be a basename.")
            )

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
                    "source/translation pair in v1.",
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
    translated_lrc.write_bytes(data)
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
        issues.append(Issue("invalid_manifest", "$", "Manifest fields do not match v1."))
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


def build_isolation_proof(
    projection: dict[str, Any],
    method: str,
    *,
    child_transcript_id: str | None = None,
    parent_transcript_id: str | None = None,
) -> dict[str, Any]:
    if method not in ISOLATION_METHODS:
        raise SidecarError(
            "unsupported_isolation_method",
            "Use native-subagent, codex-exec, or documented-harness.",
        )
    if not child_transcript_id:
        raise SidecarError(
            "missing_isolation_proof",
            "A separate child transcript ID is required for formal generation.",
        )
    if parent_transcript_id and child_transcript_id == parent_transcript_id:
        raise SidecarError(
            "inline_generation",
            "The analysis transcript must differ from the parent transcript.",
        )
    return {
        "schema": ISOLATION_SCHEMA_ID,
        "isolationVersion": ISOLATION_VERSION,
        "method": method,
        "context": ISOLATION_CONTEXT,
        "transcriptIsolated": True,
        "childTranscriptId": child_transcript_id,
        "parentTranscriptId": parent_transcript_id,
        "sourceProjectionHash": projection["lrcProjectionHash"],
    }


def validate_isolation_proof(
    proof: Any,
    projection: dict[str, Any] | None = None,
    reject_forked: bool = True,
) -> list[Issue]:
    issues: list[Issue] = []
    if not isinstance(proof, dict):
        return [
            Issue(
                "missing_isolation_proof",
                "$",
                "Formal analysis requires a separate-transcript isolation proof.",
            )
        ]
    allowed = {
        "schema",
        "isolationVersion",
        "method",
        "context",
        "transcriptIsolated",
        "childTranscriptId",
        "parentTranscriptId",
        "sourceProjectionHash",
    }
    for key in sorted(set(proof) - allowed):
        issues.append(
            Issue("invalid_isolation_proof", f"$.{key}", "Unknown isolation proof field.")
        )
    if proof.get("schema") != ISOLATION_SCHEMA_ID:
        issues.append(
            Issue("invalid_isolation_proof", "$.schema", "Unexpected isolation proof schema.")
        )
    if proof.get("isolationVersion") != ISOLATION_VERSION:
        issues.append(
            Issue(
                "unsupported_isolation_version",
                "$.isolationVersion",
                "Expected isolation proof version 1.",
            )
        )
    if proof.get("method") not in ISOLATION_METHODS:
        issues.append(
            Issue(
                "unsupported_isolation_method",
                "$.method",
                "Unsupported isolation method.",
            )
        )
    if proof.get("context") != ISOLATION_CONTEXT:
        issues.append(
            Issue(
                "inline_generation",
                "$.context",
                "Formal analysis must run in a non-forked transcript.",
            )
        )
    if proof.get("transcriptIsolated") is not True:
        issues.append(
            Issue(
                "inline_generation",
                "$.transcriptIsolated",
                "Formal analysis cannot be generated inline.",
            )
        )
    child = proof.get("childTranscriptId")
    if not isinstance(child, str) or not child.strip():
        issues.append(
            Issue(
                "missing_isolation_proof",
                "$.childTranscriptId",
                "A separate child transcript ID is required.",
            )
        )
    parent = proof.get("parentTranscriptId")
    if reject_forked and isinstance(child, str) and isinstance(parent, str) and child == parent:
        issues.append(
            Issue(
                "inline_generation",
                "$.childTranscriptId",
                "The child transcript must differ from the parent transcript.",
            )
        )
    if projection is not None and proof.get("sourceProjectionHash") != projection.get(
        "lrcProjectionHash"
    ):
        issues.append(
            Issue(
                "stale_projection_hash",
                "$.sourceProjectionHash",
                "Isolation proof belongs to a different source projection.",
            )
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


def verify_frozen_translation_files(
    projection: dict[str, Any],
    translated_lrc: Path,
    manifest: dict[str, Any],
    analysis_workdir: Path,
) -> list[Issue]:
    """Require the frozen translated LRC inside the documented work directory."""

    issues: list[Issue] = []
    workdir = analysis_workdir.expanduser().resolve()
    if not workdir.is_dir():
        return [
            Issue(
                "invalid_workdir",
                "$workdir",
                "The translation/analysis work directory does not exist.",
            )
        ]
    translated = translated_lrc.expanduser().resolve()
    try:
        translated.relative_to(workdir)
    except ValueError:
        issues.append(
            Issue(
                "translation_outside_workdir",
                "$translated",
                "The frozen translation must live inside the documented work directory.",
            )
        )
    return issues


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


def load_schema(path: Path | None = None) -> dict[str, Any]:
    schema_path = path or default_schema_path()
    schema = read_json(schema_path)
    issues = _walk_schema(schema)
    if issues:
        raise SidecarError(issues[0].code, issues[0].message, issues[0].path)
    return schema


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
    """Validate the deliberately small, closed schema subset emitted by v1."""

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


def _iter_strings(value: Any, path: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_strings(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_strings(item, f"{path}.{key}")


def _iter_keys(value: Any, path: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield path, key
            yield from _iter_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_keys(item, f"{path}[{index}]")


def _as_object_list(document: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = document.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _unique_by_id(
    entities: Sequence[dict[str, Any]],
    id_key: str,
    collection: str,
    issues: list[Issue],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, entity in enumerate(entities):
        entity_id = entity.get(id_key)
        if not isinstance(entity_id, str):
            continue
        if entity_id in result:
            issues.append(
                Issue(
                    "duplicate_id",
                    f"$.{collection}[{index}].{id_key}",
                    f"Duplicate {id_key}: {entity_id}",
                )
            )
        else:
            result[entity_id] = entity
    return result


def _check_html_and_paths(document: dict[str, Any], issues: list[Issue]) -> None:
    for path, text in _iter_strings(document):
        lower_path = path.lower()
        if (
            "<!doctype" in text.lower()
            or "<![cdata[" in text.lower()
            or "<?xml" in text.lower()
            or HTML_DECL_RE.search(text)
            or HTML_TAG_RE.search(text)
            or HTML_ENTITY_RE.search(text)
        ):
            issues.append(Issue("html_content", path, "Inline HTML is not allowed."))
        if path.endswith(".lrcFile") and ("/" in text or "\\" in text):
            issues.append(Issue("absolute_path", path, "lrcFile must be a basename."))
        if ABSOLUTE_PATH_RE.match(text) and (
            "path" in lower_path
            or "file" in lower_path
            or text.lower().startswith("file://")
        ):
            issues.append(Issue("absolute_path", path, "Absolute paths are not allowed."))

    for path, key in _iter_keys(document):
        if FORBIDDEN_TRANSLATION_KEY_RE.search(key) and key != "kind":
            issues.append(
                Issue(
                    "per_line_translation",
                    f"{path}.{key}",
                    "Per-line translation fields are not allowed in the sidecar.",
                )
            )


def _check_labels(labels: Any, path: str, issues: list[Issue]) -> None:
    if not isinstance(labels, list):
        return
    for index, label in enumerate(labels):
        if not isinstance(label, dict):
            continue
        namespace = label.get("namespace")
        key = label.get("key")
        if not isinstance(namespace, str) or not namespace:
            issues.append(
                Issue("missing_namespace", f"{path}[{index}].namespace", "Missing label namespace.")
            )
        if not isinstance(key, str) or not key:
            issues.append(Issue("missing_label_key", f"{path}[{index}].key", "Missing label key."))


def _check_scope(
    scope: Any,
    path: str,
    blocks: dict[str, dict[str, Any]],
    phrases: dict[str, dict[str, Any]],
    issues: list[Issue],
) -> None:
    if not isinstance(scope, dict):
        return
    block_id = scope.get("blockId")
    phrase_id = scope.get("phraseId")
    if not isinstance(block_id, str):
        issues.append(Issue("missing_scope_block", path + ".blockId", "Missing block scope."))
        return
    if isinstance(block_id, str) and block_id not in blocks:
        issues.append(Issue("unresolved_block", path + ".blockId", f"Unknown block: {block_id}"))
    if isinstance(phrase_id, str):
        phrase = phrases.get(phrase_id)
        if phrase is None:
            issues.append(
                Issue("unresolved_phrase", path + ".phraseId", f"Unknown phrase: {phrase_id}")
            )
        elif isinstance(block_id, str) and block_id not in phrase.get("blockIds", []):
            issues.append(
                Issue(
                    "phrase_block_mismatch",
                    path + ".phraseId",
                    "Scoped phrase does not belong to the scoped block.",
                )
            )


def _check_source_filename(
    source: Any, issues: list[Issue]
) -> None:
    if not isinstance(source, dict):
        return
    lrc_file = source.get("lrcFile")
    if not isinstance(lrc_file, str) or not lrc_file:
        return
    if lrc_file in {".", ".."} or lrc_file.startswith("."):
        issues.append(
            Issue(
                "invalid_lrc_basename",
                "$.source.lrcFile",
                "lrcFile must name a visible sibling LRC file.",
            )
        )
    if not lrc_file.lower().endswith(LRC_SUFFIX):
        issues.append(
            Issue(
                "invalid_lrc_basename",
                "$.source.lrcFile",
                "lrcFile must end in .lrc.",
            )
        )
    if "\x00" in lrc_file:
        issues.append(
            Issue(
                "invalid_lrc_basename",
                "$.source.lrcFile",
                "lrcFile must not contain a NUL character.",
            )
        )


def _check_entity_ids(document: dict[str, Any], issues: list[Issue]) -> None:
    collections = {
        "lines": "lineId",
        "blocks": "blockId",
        "phrases": "phraseId",
        "relations": "relationId",
        "grammarNotes": "noteId",
        "uncertainties": "uncertaintyId",
    }
    seen: dict[str, str] = {}
    for collection, id_key in collections.items():
        values = document.get(collection)
        if not isinstance(values, list):
            continue
        for index, entity in enumerate(values):
            if not isinstance(entity, dict):
                continue
            entity_id = entity.get(id_key)
            if not isinstance(entity_id, str):
                continue
            if not ID_PATTERN.fullmatch(entity_id):
                issues.append(
                    Issue(
                        "invalid_id",
                        f"$.{collection}[{index}].{id_key}",
                        "Entity ID is not in canonical form.",
                    )
                )
            if entity_id in seen:
                issues.append(
                    Issue(
                        "duplicate_id",
                        f"$.{collection}[{index}].{id_key}",
                        f"Entity ID already used by {seen[entity_id]}.",
                    )
                )
            else:
                seen[entity_id] = collection


def _check_features_are_namespaced(document: dict[str, Any], issues: list[Issue]) -> None:
    """Reject accidental language-specific top-level entity fields.

    Prose answers stay in meaning/explanation/gloss; classification goes in
    labels. Fields such as ja_particle, object_marker, or tense are allowed
    only as label keys, not ad-hoc schema fields.
    """

    collections = ("blocks", "phrases", "relations", "grammarNotes", "uncertainties")
    for collection in collections:
        values = document.get(collection)
        if not isinstance(values, list):
            continue
        for index, entity in enumerate(values):
            if not isinstance(entity, dict):
                continue
            for key in entity:
                if (
                    "_" in key
                    or "." in key
                    or re.match(r"^(?:ja|ko|zh|en)[A-Z]", key) is not None
                ):
                    issues.append(
                        Issue(
                            "unnamespaced_feature",
                            f"$.{collection}[{index}].{key}",
                            "Language-specific or dotted feature fields must be represented "
                            "as namespaced labels.",
                        )
                    )


def validate_sidecar_semantics(
    document: Any,
    projection: dict[str, Any] | None = None,
    expected_lrc_file: str | None = None,
) -> list[Issue]:
    if not isinstance(document, dict):
        return [Issue("invalid_sidecar", "$", "Sidecar must be a JSON object.")]

    issues: list[Issue] = []
    _check_html_and_paths(document, issues)
    _check_entity_ids(document, issues)
    _check_features_are_namespaced(document, issues)

    source = document.get("source")
    _check_source_filename(source, issues)
    if isinstance(source, dict):
        source_languages = source.get("sourceLanguages")
        if isinstance(source_languages, list):
            if len(set(item for item in source_languages if isinstance(item, str))) != len(
                source_languages
            ):
                issues.append(
                    Issue(
                        "duplicate_language",
                        "$.source.sourceLanguages",
                        "sourceLanguages must not contain duplicates.",
                    )
                )
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
    else:
        source_languages = None

    lines = _as_object_list(document, "lines")
    line_by_id = _unique_by_id(lines, "lineId", "lines", issues)
    source_orders: set[int] = set()
    for index, line in enumerate(lines):
        path = f"$.lines[{index}]"
        source_order = line.get("sourceOrder")
        if isinstance(source_order, int) and not isinstance(source_order, bool):
            if source_order in source_orders:
                issues.append(
                    Issue("duplicate_source_order", path + ".sourceOrder", "Duplicate sourceOrder.")
                )
            source_orders.add(source_order)
        text = line.get("text")
        if not isinstance(text, str) or not text:
            continue
        if isinstance(source_languages, list):
            language = line.get("language")
            if isinstance(language, str) and language not in source_languages:
                issues.append(
                    Issue(
                        "unknown_line_language",
                        path + ".language",
                        "Line language is absent from sourceLanguages.",
                    )
                )
        spans = line.get("languageSpans")
        if isinstance(spans, list):
            previous_end = -1
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
                if (
                    isinstance(source_languages, list)
                    and span.get("language") not in source_languages
                ):
                    issues.append(
                        Issue(
                            "unknown_span_language",
                            span_path + ".language",
                            "Span language is absent from sourceLanguages.",
                        )
                    )

    if projection is not None and isinstance(source, dict):
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
                    "Sidecar lrcProjectionHash does not match the accepted source projection.",
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
        if (
            projection.get("lrcFile") is not None
            and source.get("lrcFile") != projection.get("lrcFile")
        ):
            issues.append(
                Issue(
                    "lrc_file_mismatch",
                    "$.source.lrcFile",
                    "Sidecar lrcFile does not match the source projection.",
                )
            )

        projected = projection.get("lines", [])
        projected_by_id = {
            item["lineId"]: item for item in projected if isinstance(item, dict)
        }
        if len(lines) != len(projected):
            issues.append(
                Issue(
                    "line_coverage",
                    "$.lines",
                    f"Expected {len(projected)} projected source lines, got {len(lines)}.",
                )
            )
        for index, projected_line in enumerate(projected):
            line_id = projected_line["lineId"]
            actual = line_by_id.get(line_id)
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
                if actual.get(field) != projected_line[field]:
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
        for line_id, actual in line_by_id.items():
            if line_id not in projected_by_id:
                issues.append(
                    Issue(
                        "unknown_source_line",
                        f"$.lines[{actual.get('sourceOrder', '?')}]",
                        f"Sidecar line is absent from the source projection: {line_id}",
                    )
                )

    blocks_list = _as_object_list(document, "blocks")
    phrases_list = _as_object_list(document, "phrases")
    relations_list = _as_object_list(document, "relations")
    notes_list = _as_object_list(document, "grammarNotes")
    uncertainties_list = _as_object_list(document, "uncertainties")
    blocks = _unique_by_id(blocks_list, "blockId", "blocks", issues)
    phrases = _unique_by_id(phrases_list, "phraseId", "phrases", issues)
    relations = _unique_by_id(relations_list, "relationId", "relations", issues)
    _unique_by_id(notes_list, "noteId", "grammarNotes", issues)
    _unique_by_id(uncertainties_list, "uncertaintyId", "uncertainties", issues)

    block_line_ids: dict[str, list[str]] = {}
    all_block_line_ids: set[str] = set()
    for index, block in enumerate(blocks_list):
        path = f"$.blocks[{index}]"
        _check_labels(block.get("labels"), path + ".labels", issues)
        line_ids = block.get("lineIds")
        if not isinstance(line_ids, list) or not line_ids:
            issues.append(Issue("empty_block", path + ".lineIds", "Block must reference a line."))
            continue
        block_line_ids[str(block.get("blockId"))] = [
            item for item in line_ids if isinstance(item, str)
        ]
        resolved_lines: list[dict[str, Any]] = []
        block_lines: list[tuple[int, int]] = []
        for line_index, line_id in enumerate(line_ids):
            if not isinstance(line_id, str):
                continue
            if line_id not in line_by_id:
                issues.append(
                    Issue(
                        "unresolved_line",
                        f"{path}.lineIds[{line_index}]",
                        f"Unknown source line: {line_id}",
                    )
                )
            else:
                all_block_line_ids.add(line_id)
                resolved_lines.append(line_by_id[line_id])
                line_time = line_by_id[line_id].get("timeMs")
                order = line_by_id[line_id].get("sourceOrder")
                if (
                    isinstance(line_time, int)
                    and not isinstance(line_time, bool)
                    and isinstance(order, int)
                    and not isinstance(order, bool)
                ):
                    block_lines.append((order, line_time))
        line_order = [order for order, _ in block_lines]
        if line_order != sorted(line_order):
            issues.append(
                Issue(
                    "non_monotonic_block",
                    path + ".lineIds",
                    "Block lineIds do not follow sourceOrder.",
                )
            )
        current_times = [time for _, time in block_lines]
        if current_times != sorted(current_times):
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

    if isinstance(source, dict):
        for line_id in line_by_id:
            if line_id not in all_block_line_ids:
                issues.append(
                    Issue(
                        "uncovered_source_line",
                        "$.blocks",
                        f"Source line is not covered by any block: {line_id}",
                    )
                )

    for index, phrase in enumerate(phrases_list):
        path = f"$.phrases[{index}]"
        _check_labels(phrase.get("labels"), path + ".labels", issues)
        block_ids = phrase.get("blockIds")
        resolved_block_ids: set[str] = set()
        if not isinstance(block_ids, list) or not block_ids:
            issues.append(
                Issue("empty_phrase_blocks", path + ".blockIds", "Phrase must reference a block.")
            )
        else:
            for block_index, block_id in enumerate(block_ids):
                if not isinstance(block_id, str):
                    continue
                if block_id not in blocks:
                    issues.append(
                        Issue(
                            "unresolved_block",
                            f"{path}.blockIds[{block_index}]",
                            f"Unknown block: {block_id}",
                        )
                    )
                else:
                    resolved_block_ids.add(block_id)
            if len(resolved_block_ids) != len(block_ids):
                issues.append(
                    Issue(
                        "duplicate_block_reference",
                        path + ".blockIds",
                        "Phrase blockIds must be unique and resolvable.",
                    )
                )

        segments = phrase.get("sourceSegments")
        segment_texts: list[str] = []
        previous_order = -1
        previous_start = -1
        if not isinstance(segments, list) or not segments:
            issues.append(
                Issue(
                    "empty_phrase_segments",
                    path + ".sourceSegments",
                    "Phrase must reference source text.",
                )
            )
            continue
        for segment_index, segment in enumerate(segments):
            segment_path = f"{path}.sourceSegments[{segment_index}]"
            if not isinstance(segment, dict):
                continue
            line_id = segment.get("lineId")
            line = line_by_id.get(line_id) if isinstance(line_id, str) else None
            if line is None:
                issues.append(
                    Issue(
                        "unresolved_line",
                        segment_path + ".lineId",
                        f"Unknown source line: {line_id}",
                    )
                )
                continue
            source_order = line.get("sourceOrder")
            if isinstance(source_order, int) and not isinstance(source_order, bool):
                if source_order < previous_order:
                    issues.append(
                        Issue(
                            "segment_order",
                            segment_path,
                            "Phrase segments must follow source line order.",
                        )
                    )
                if source_order == previous_order:
                    start_value = segment.get("start")
                    if (
                        isinstance(start_value, int)
                        and not isinstance(start_value, bool)
                        and start_value <= previous_start
                    ):
                        issues.append(
                            Issue(
                                "segment_overlap",
                                segment_path,
                                "Segments on the same line must not overlap or repeat.",
                            )
                        )
                    if isinstance(start_value, int):
                        previous_start = start_value
                else:
                    previous_order = source_order
                    start_value = segment.get("start")
                    previous_start = (
                        start_value
                        if isinstance(start_value, int) and not isinstance(start_value, bool)
                        else -1
                    )

            line_text = line.get("text")
            start = segment.get("start")
            end = segment.get("end")
            segment_text = segment.get("text")
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
            elif not isinstance(segment_text, str) or line_text[start:end] != segment_text:
                issues.append(
                    Issue(
                        "segment_text_mismatch",
                        segment_path + ".text",
                        "Segment text does not equal the exact source substring.",
                    )
                )
            if isinstance(segment_text, str):
                segment_texts.append(segment_text)

            matching_blocks = {
                block_id
                for block_id in resolved_block_ids
                if line_id in block_line_ids.get(block_id, [])
            }
            if not matching_blocks:
                issues.append(
                    Issue(
                        "phrase_block_mismatch",
                        segment_path,
                        "Segment line is not part of any block referenced by the phrase.",
                    )
                )

        surface = phrase.get("surface")
        if isinstance(surface, str) and len(segment_texts) == len(segments):
            if surface != "".join(segment_texts):
                issues.append(
                    Issue(
                        "surface_mismatch",
                        path + ".surface",
                        "Phrase surface must equal the concatenated source segments.",
                    )
                )

    for index, relation in enumerate(relations_list):
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
        if from_id == to_id and isinstance(from_id, str):
            issues.append(
                Issue("self_relation", path, "A relation cannot point to the same phrase.")
            )

    for index, note in enumerate(notes_list):
        path = f"$.grammarNotes[{index}]"
        _check_labels(note.get("labels"), path + ".labels", issues)
        _check_scope(note.get("scope"), path + ".scope", blocks, phrases, issues)

    for index, uncertainty in enumerate(uncertainties_list):
        path = f"$.uncertainties[{index}]"
        _check_scope(uncertainty.get("scope"), path + ".scope", blocks, phrases, issues)

    return issues


def validate_sidecar_document(
    document: Any,
    projection: dict[str, Any] | None = None,
    expected_lrc_file: str | None = None,
    schema_path: Path | None = None,
    require_projection: bool = False,
    learner_language: str | None = None,
) -> list[Issue]:
    schema = load_schema(schema_path)
    issues = validate_schema_instance(document, schema)
    issues.extend(validate_sidecar_semantics(document, projection, expected_lrc_file))
    if require_projection and projection is None:
        issues.append(
            Issue(
                "missing_projection",
                "$.source.lrcProjectionHash",
                "Offline verification requires the frozen source projection.",
            )
        )
    if require_projection and isinstance(document, dict):
        source = document.get("source")
        if not isinstance(source, dict) or not isinstance(
            source.get("lrcProjectionHash"), str
        ):
            issues.append(
                Issue(
                    "missing_projection_hash",
                    "$.source.lrcProjectionHash",
                    "The sidecar must carry a projection hash.",
                )
            )
    if learner_language is not None:
        issues.extend(validate_learner_language(document, learner_language))
    return issues


def learner_language_of(document: Any) -> str | None:
    if not isinstance(document, dict):
        return None
    extensions = document.get("extensions")
    if not isinstance(extensions, dict):
        return None
    value = extensions.get(LEARNER_LANGUAGE_EXTENSION)
    return value.strip().lower() if isinstance(value, str) and value.strip() else None


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


def _learner_text_fields(
    document: dict[str, Any],
) -> Iterable[tuple[str, str, str, str | None]]:
    for collection in ("blocks", "phrases", "relations", "grammarNotes", "uncertainties"):
        items = document.get(collection)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            surface = item.get("surface")
            surface_text = surface if isinstance(surface, str) else None
            for key in ("meaning", "gloss", "explanation", "message", "notes"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    yield f"$.{collection}[{index}].{key}", value, key, surface_text
            alternatives = item.get("alternatives")
            if isinstance(alternatives, list):
                for alternative_index, value in enumerate(alternatives):
                    if isinstance(value, str) and value.strip():
                        yield (
                            f"$.{collection}[{index}].alternatives[{alternative_index}]",
                            value,
                            "alternatives",
                            surface_text,
                        )


def validate_learner_language(
    document: Any, expected_language: str
) -> list[Issue]:
    expected = expected_language.strip().lower()
    if not expected:
        return [Issue("missing_learner_language", "$", "Expected a learner language.")]
    actual = learner_language_of(document)
    if actual is None:
        return [
            Issue(
                "missing_learner_language",
                "$.extensions.learnerLanguage",
                "New sidecars must declare extensions.learnerLanguage.",
            )
        ]
    if actual != expected:
        return [
            Issue(
                "learner_language_mismatch",
                "$.extensions.learnerLanguage",
                f"Expected learner language {expected}, got {actual}.",
            )
        ]

    primary = expected.split("-", 1)[0]
    if primary not in CJK_LEARNER_LANGUAGES:
        return []

    issues: list[Issue] = []
    for path, text, key, surface in _learner_text_fields(document):
        if key == "gloss" and surface is not None and text.strip() == surface.strip():
            continue
        minimum_length = 1 if key == "gloss" else 12
        if (
            len(text) >= minimum_length
            and _is_ascii_prose(text)
            and not _has_cjk(text)
        ):
            issues.append(
                Issue(
                    "learner_text_not_in_target_language",
                    path,
                    f"Learner-facing prose must use {expected}, not English-only text.",
                )
            )
    return issues


def validation_report(issues: Sequence[Issue], **extra: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "ok": not issues,
        "errors": [issue.as_dict() for issue in issues],
    }
    report.update(extra)
    return report


def normalized_sidecar(
    draft: dict[str, Any],
    projection: dict[str, Any],
    lrc_file: str | None = None,
) -> dict[str, Any]:
    """Rebuild source identity fields from the frozen projection.

    Semantic content comes from the draft.  Source file, line text, timeline,
    fingerprints, and IDs cannot be changed by the analyzer.
    """

    draft_lines = _as_object_list(draft, "lines")
    by_id = {
        line["lineId"]: line
        for line in draft_lines
        if isinstance(line.get("lineId"), str)
    }
    normalized_lines: list[dict[str, Any]] = []
    for projected in projection["lines"]:
        line = by_id[projected["lineId"]]
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
    normalized: dict[str, Any] = {
        "schema": SIDECAR_SCHEMA_ID,
        "schemaVersion": SCHEMA_VERSION,
        "source": {
            "lrcFile": lrc_file or projection.get("lrcFile") or source["lrcFile"],
            "songId": projection["songId"],
            "sourceLanguages": sorted(set(source["sourceLanguages"])),
            "lineCount": projection["lineCount"],
            "lrcProjectionHash": projection["lrcProjectionHash"],
        },
        "lines": normalized_lines,
        "blocks": draft["blocks"],
        "phrases": draft["phrases"],
        "relations": draft["relations"],
        "grammarNotes": draft["grammarNotes"],
        "uncertainties": draft["uncertainties"],
    }
    if "extensions" in draft:
        normalized["extensions"] = draft["extensions"]
    return normalized


def sidecar_output_path(lrc_path: Path) -> Path:
    resolved = ensure_local_lrc(lrc_path)
    return resolved.with_suffix(".json")


def _write_bytes_atomic(path: Path, data: bytes) -> None:
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


def build_validation_report(
    issues: Sequence[Issue],
    projection: dict[str, Any] | None = None,
    draft_path: Path | None = None,
) -> dict[str, Any]:
    report = validation_report(
        issues,
        schemaVersion=SCHEMA_VERSION,
        projectionHash=(
            projection.get("lrcProjectionHash") if projection is not None else None
        ),
        draft=str(draft_path.name) if draft_path is not None else None,
    )
    return report


def write_validation_report(
    path: Path,
    issues: Sequence[Issue],
    projection: dict[str, Any] | None = None,
    draft_path: Path | None = None,
) -> None:
    write_json_atomic(
        path,
        build_validation_report(issues, projection=projection, draft_path=draft_path),
        replace=True,
    )


def finalize_sidecar(
    lrc_path: Path,
    projection_path: Path,
    translated_lrc_path: Path,
    manifest_path: Path,
    draft_path: Path,
    isolation_path: Path | None = None,
    workdir: Path | None = None,
    report_path: Path | None = None,
    replace: bool = False,
    learner_language: str | None = None,
) -> tuple[Path, bool]:
    projection = load_projection(projection_path)
    translated_lrc = ensure_local_lrc(translated_lrc_path)
    try:
        manifest = read_json(manifest_path)
    except SidecarError as exc:
        if report_path is not None:
            write_validation_report(
                report_path,
                [Issue(exc.code, exc.path, exc.message)],
                projection,
                draft_path,
            )
        raise
    if workdir is not None:
        workdir_issues = verify_frozen_translation_files(
            projection, translated_lrc, manifest, workdir
        )
        if workdir_issues:
            raise SidecarError(
                workdir_issues[0].code,
                workdir_issues[0].message,
                workdir_issues[0].path,
            )
    _, freeze_issues = verify_frozen_translation(projection, translated_lrc, manifest)
    if freeze_issues:
        if report_path is not None:
            write_validation_report(report_path, freeze_issues, projection, draft_path)
        raise SidecarError(
            freeze_issues[0].code, freeze_issues[0].message, freeze_issues[0].path
        )
    source_report, source_issues = verify_source_matches_projection(
        projection, lrc_path
    )
    if source_issues:
        if report_path is not None:
            write_validation_report(report_path, source_issues, projection, draft_path)
        raise SidecarError(
            source_issues[0].code, source_issues[0].message, source_issues[0].path
        )

    try:
        draft = read_json(draft_path)
    except SidecarError as exc:
        if report_path is not None:
            write_validation_report(
                report_path,
                [Issue(exc.code, exc.path, exc.message)],
                projection,
                draft_path,
            )
        raise
    if isolation_path is None:
        raise SidecarError(
            "missing_isolation_proof",
            "Formal finalization requires an --isolation proof file.",
        )
    try:
        isolation = read_json(isolation_path)
    except SidecarError as exc:
        if report_path is not None:
            write_validation_report(
                report_path,
                [Issue(exc.code, exc.path, exc.message)],
                projection,
                draft_path,
            )
        raise
    isolation_issues = validate_isolation_proof(isolation, projection)
    if isolation_issues:
        if report_path is not None:
            write_validation_report(
                report_path, isolation_issues, projection, draft_path
            )
        raise SidecarError(
            isolation_issues[0].code,
            isolation_issues[0].message,
            isolation_issues[0].path,
        )
    issues = validate_sidecar_document(
        draft,
        projection=projection,
        expected_lrc_file=projection.get("lrcFile"),
        learner_language=learner_language,
    )
    if issues:
        if report_path is not None:
            write_validation_report(report_path, issues, projection, draft_path)
        raise SidecarError(issues[0].code, issues[0].message, issues[0].path)

    try:
        normalized = normalized_sidecar(
            draft,
            projection,
            lrc_file=projection.get("lrcFile", lrc_path.name),
        )
    except (KeyError, TypeError) as exc:
        raise SidecarError(
            "normalization_failed",
            f"The semantic draft is missing canonical source line data: {exc}",
        ) from exc
    normalized_issues = validate_sidecar_document(
        normalized,
        projection=projection,
        expected_lrc_file=projection.get("lrcFile"),
        learner_language=learner_language,
    )
    if normalized_issues:
        if report_path is not None:
            write_validation_report(
                report_path, normalized_issues, projection, draft_path
            )
        raise SidecarError(
            normalized_issues[0].code,
            normalized_issues[0].message,
            normalized_issues[0].path,
        )

    output = sidecar_output_path(lrc_path)
    changed = write_json_atomic(output, normalized, replace=replace)
    if report_path is not None:
        write_validation_report(report_path, [], projection, draft_path)
    return output, changed
