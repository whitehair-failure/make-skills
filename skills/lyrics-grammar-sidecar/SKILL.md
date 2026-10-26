---
name: lyrics-grammar-sidecar
description: Generate and validate a SakuraUta lyrics grammar analysis sidecar for a local LRC file. Use when a user asks for lyric grammar, vocabulary, sentence, or block-level learning annotations beside an existing LRC, especially cross-line Japanese/Korean/Chinese/English lyric analysis. Requires the existing lyrics-translator skill for translation and a separate non-forked analysis transcript; only the deterministic finalizer may write the production JSON.
version: 1.0.0
---

# Lyrics Grammar Sidecar

Create one read-only, block-level grammar analysis JSON beside a local `.lrc`.
The sidecar is for SakuraUta to consume later, but this skill runs entirely
outside SakuraUta and must not import or call application code.

## Non-Negotiable Contract

- Accept only a local `.lrc`; reject `.ass`, `.srt`, and remote tracks.
- Use `lyrics-translator` as a black box for per-line translation. Do not
  create translations in the sidecar and do not copy line translations there.
- The first lexical source line at each rounded-millisecond timestamp is
  analysis input. This also makes bilingual LRC rows safe after translation.
- Every source line must appear exactly once in `lines` and be covered by at
  least one `block`. A line may belong to several overlapping blocks.
- Every block references one or more real `lineId` values.
- Every phrase has non-empty `blockIds`; every source segment resolves to the
  exact Unicode code-point substring of a source line.
- Labels use namespaces such as `lang:ja`, `syntax`, or `discourse`; do not
  add language-specific top-level fields.
- Store learning meaning at block level. The sidecar is not a second line-by-
  line translation file.
- JSON strings are plain text or structured values. No HTML tags, entities,
  comments, absolute paths, or embedded documents.
- Do not silently generate formally in the current context. Formal output
  requires either a non-forked native subagent, a separate `codex exec`
  session, or another harness with a distinct transcript.

## Workflow

All paths below are relative to this skill. The scripts accept only paths the
user explicitly provides.

### 1. Create the Work Area and Projection

```powershell
python scripts/project_source.py "C:\Music\Song.lrc" `
  --song-id "<SakuraUta songId>"
```

Default output:

```text
C:\Music\Song.grammar-work\source-projection.json
```

The temporary `<song>.grammar-work` directory sits beside the LRC but is not
a product artifact and must not be committed. The final sidecar is the only
production file.

Projection v1 defines:

- `lineId` from `LYRICS_GRAMMAR_LINE_ID_VERSION`, songId, timestamp,
  same-timestamp ordinal, role, and NFC source text;
- `fingerprint` from the fingerprint version and NFC source text;
- `lrcProjectionHash` from the projection version, songId, line count, and
  each line's timestamp / ordinal / NFC text.

Translations are intentionally absent from this hash.
The canonical fields match `packages/core/src/lyrics-grammar.ts` exactly:

```json
{
  "songId": "...",
  "lineCount": 2,
  "lrcProjectionHash": "sha256:...",
  "lines": [
    {
      "lineId": "l_...",
      "sourceOrder": 0,
      "timeMs": 1000,
      "text": "...",
      "fingerprint": "sha256:...",
      "role": "text",
      "sameTimestampOrdinal": 0
    }
  ]
}
```

`project_source.py` also writes `schema`, `projectionVersion`, and `lrcFile`
as generator-only work fields. Use `--require-app-projection` to omit them
when comparing with the App's `GrammarSourceProjection`.

### 2. Translate and Accept Through `lyrics-translator`

Use the separate `..\lyrics-translator\SKILL.md` workflow without modifying
it. Produce a bilingual LRC where source and translation alternate at the
same timestamp. Translation quality acceptance happens before any grammar
analysis. All timestamps must remain exactly one per source/translation row.

Freeze the accepted bilingual LRC:

```powershell
python scripts/verify_translation.py `
  "C:\Music\Song.grammar-work\source-projection.json" `
  "C:\Music\Song.grammar-work\accepted-translated.lrc" `
  --workdir "C:\Music\Song.grammar-work"
```

The manifest records both the source projection hash and the exact translated
LRC SHA-256. Editing a translation after this point must fail; it is never
silently regenerated. The accepted file is copied into `<workdir>/translated.lrc`;
the working copy is the frozen analysis input. A later integrity check can use:

```powershell
python scripts/verify_translation.py `
  "C:\Music\Song.grammar-work\source-projection.json" `
  "C:\Music\Song.grammar-work\translated.lrc" `
  --workdir "C:\Music\Song.grammar-work" `
  --verify-frozen
```

### 3. Record Separate-Transcript Evidence

Start a fresh grammar-analysis context. The analyzer reads only:

- `source-projection.json`;
- the frozen `translated.lrc`;
- this skill's schema and validation rules.

Record the harness evidence before producing `semantic-draft.json`:

```powershell
python scripts/record_isolation.py `
  "C:\Music\Song.grammar-work\source-projection.json" `
  --method codex-exec `
  --child-transcript-id "<fresh-analysis-id>" `
  --parent-transcript-id "<calling-session-id>" `
  --output "C:\Music\Song.grammar-work\isolation-proof.json"
```

Allowed methods are `native-subagent`, `codex-exec`, and
`documented-harness`. Use the actual harness ID. If no separate transcript
exists, stop with an explicit unsupported result. Do not create a proof by
inventing an ID and do not run translation and analysis inline as a
substitute. Filesystem separation is best effort; transcript separation and
the translation freeze are mandatory.

### 4. Produce Only a Semantic Draft

The fresh analyzer writes `semantic-draft.json` in the work directory. It may
copy canonical line identity fields from the projection, but those fields are
rechecked and regenerated by the finalizer. It must not:

- modify `translated.lrc`, its manifest, or the projection;
- write `analysis.json` or the final sibling `.json`;
- invent a second per-line translation;
- use inline HTML.

Read `references/draft-contract.md` for the entity shapes and worked examples.

### 5. Finalize Deterministically

Only the deterministic finalizer writes the production sidecar:

```powershell
python scripts/finalize.py "C:\Music\Song.lrc" `
  --projection "C:\Music\Song.grammar-work\source-projection.json" `
  --workdir "C:\Music\Song.grammar-work"
```

It validates the source projection, translation freeze, isolation proof,
schema subset, line coverage, Unicode ranges, labels, and all cross-
references before atomically writing:

```text
C:\Music\Song.json
```

An existing different sidecar is preserved unless the user explicitly asks
for replacement, in which case add `--replace`. Failed validation preserves
the draft and writes `validation-report.json`.

### 6. Verify Without SakuraUta

Validate a candidate:

```powershell
python scripts/validate.py "C:\Music\Song.json" `
  --projection "C:\Music\Song.grammar-work\source-projection.json" `
  --expected-lrc-name "Song.lrc"
```

Run the complete source/translation/sidecar integrity check:

```powershell
python scripts/validate_all.py "C:\Music\Song.lrc" "C:\Music\Song.json" `
  --projection "C:\Music\Song.grammar-work\source-projection.json" `
  --workdir "C:\Music\Song.grammar-work"
```

Neither command reads the SakuraUta database, calls `window.okashi`, or
accesses the network. Only Python's standard library is required.

## Validation Rules the Scripts Enforce

- Input is UTF-8 `.lrc`, parsed with the App's one-leading-timestamp grammar:
  `[MM:SS]`, `[MM:SS.x]`, `[MM:SS.xx]`, or `[MM:SS.xxx]`.
- Credit lines are excluded as in the App parser. Rows with multiple
  timestamps are not projected as separate source lines.
- Only the first lexical source row at a timestamp enters the canonical
  projection; duplicate lyrics at different timestamps get distinct IDs.
- `lineCount`, source order, timestamp, text, fingerprint, and projection
  hash must match the frozen projection.
- Block time ranges contain every referenced line and never end before they
  start. Every source line has block coverage.
- Phrase segments follow source order, do not overlap on one line, stay
  within Python string index ranges (Unicode code points), and exactly equal
  the code-point slice of the source line. `surface` equals concatenated
  segment text.
- Every phrase block and segment line association resolves; every relation
  references existing phrases; grammar-note and uncertainty scopes resolve
  and agree with phrase block membership.
- IDs are unique across all entity collections.
- Source languages and label namespaces are normalized, not hard-coded to
  Japanese.
- Unknown schema keywords fail closed. The bundled validator intentionally
  supports the closed JSON Schema subset used by v1.

## Failure Behavior

| Failure | Required result |
| --- | --- |
| Non-LRC or remote input | reject before projection |
| Translation changed after freeze | `translation_changed_after_freeze` |
| Source text or timeline changed | `stale_source_projection` / `stale_projection_hash` |
| No separate transcript proof | `missing_isolation_proof` |
| Inline or forked transcript | `inline_generation` |
| Invalid schema or unknown keyword | fail closed; preserve draft |
| Invalid line/block/phrase/note link | fail; preserve draft |
| Invalid Unicode span or surface | fail; preserve draft |
| Existing differing sidecar | refuse unless `--replace` |

## Fixtures and Tests

`fixtures/ja`, `fixtures/ko`, `fixtures/zh`, `fixtures/en`, and
`fixtures/edge` contain bilingual LRCs, canonical projections, and valid
sidecars. The Japanese fixture demonstrates a cross-line phrase, overlapping
blocks, repeated lyrics, same-timestamp source/translation rows, and a
grammar relation. `fixtures/edge` demonstrates emoji and combining-character
code-point spans. `fixtures/stale` changes source text; `fixtures/invalid`
covers invalid coverage/references and HTML-looking text.
`fixtures/golden-vectors.json` pins the App's exact lineId, fingerprint, and
projection-hash values for repeated text, a same-timestamp bilingual pair,
and emoji/combining-character code-point spans.

Run all checks:

```powershell
python scripts/selftest.py
```

## Output Boundary

The final artifact is `<song>.json` in the same directory as `<song>.lrc`.
Never write to the SakuraUta database, invoke its HTTP API, or add a runtime
dependency on the desktop application. Never modify the existing
`lyrics-translator` skill.
