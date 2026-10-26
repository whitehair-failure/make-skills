# Semantic Draft Contract v1

`semantic-draft.json` is input to the deterministic finalizer, not a
production artifact. Use the schema at
`../schema/v1/lyrics-grammar-analysis.schema.json`.
Final-sidecar source fields mirror `packages/core/src/lyrics-grammar.ts`:
the App validates `source.songId`, `source.lineCount`, and
`source.lrcProjectionHash` against its canonical LRC projection.

## Top Level

```json
{
  "schema": "sakurauta.lyrics-grammar-analysis",
  "schemaVersion": 1,
  "source": {
    "lrcFile": "Song.lrc",
    "songId": "Artist - Song",
    "sourceLanguages": ["ja", "en"],
    "lineCount": 4,
    "lrcProjectionHash": "sha256:..."
  },
  "lines": [],
  "blocks": [],
  "phrases": [],
  "relations": [],
  "grammarNotes": [],
  "uncertainties": []
}
```

`extensions` is optional and free-form JSON. The finalizer rejects
translation-bearing fields by name and HTML-looking text anywhere in the
document.

## Source Lines

Copy each projected line, and add:

```json
{
  "lineId": "l_...",
  "sourceOrder": 0,
  "timeMs": 1000,
  "text": "君がくれた",
  "fingerprint": "sha256:...",
  "role": "text",
  "language": "ja",
  "languageSpans": [
    {"start": 0, "end": 5, "language": "ja"}
  ]
}
```

`start` and `end` are Unicode code-point indexes, not UTF-16 units.
The finalizer replaces each projected source field with its canonical copy,
so model-side edits to source text, timestamps, IDs, or fingerprints fail
rather than pass silently.

## Blocks

Blocks are learning units, not necessarily the translator's internal
chunking.

```json
{
  "blockId": "b_promise",
  "lineIds": ["l_line1", "l_line2", "l_line3"],
  "startTimeMs": 1000,
  "endTimeMs": 5000,
  "meaning": "你送给我的小小约定，我至今仍记得。",
  "register": "reflective",
  "labels": [
    {"namespace": "discourse", "key": "recollection"}
  ],
  "confidence": 0.94
}
```

- `lineIds` must be non-empty and unique.
- Every referenced line's `timeMs` must lie in the block interval.
- Overlapping blocks are allowed.
- Every source line must belong to at least one block.
- `meaning` is a whole-block learning gloss, never a line translation.

## Phrases

```json
{
  "phraseId": "p_promise",
  "blockIds": ["b_promise"],
  "kind": "phrase",
  "sourceSegments": [
    {
      "lineId": "l_line1",
      "start": 0,
      "end": 5,
      "text": "君がくれた"
    },
    {
      "lineId": "l_line2",
      "start": 0,
      "end": 5,
      "text": "小さな約束"
    }
  ],
  "surface": "君がくれた小さな約束",
  "role": "object_noun_phrase",
  "labels": [
    {"namespace": "lang:ja", "key": "relative_clause"}
  ],
  "explanation": "The first line modifies the noun phrase beginning on the next line.",
  "gloss": "the little promise you gave me",
  "confidence": 0.9
}
```

A phrase may cross 2-3 lines. `sourceSegments` must follow source order.
Segments on the same line may appear multiple times only at strictly
increasing, non-overlapping ranges. Each segment's `text` must equal the
exact source substring. `surface` is the concatenation of segments. Every
segment line must belong to at least one block named in `blockIds`.

## Relations

```json
{
  "relationId": "r_object",
  "fromPhraseId": "p_remember",
  "toPhraseId": "p_promise",
  "type": "governs",
  "labels": [
    {"namespace": "syntax", "key": "predicate_object"}
  ],
  "explanation": "The predicate governs the preceding noun phrase.",
  "confidence": 0.86
}
```

Both phrase references must resolve; self-relations are rejected.

## Grammar Notes and Uncertainty

Both use a scope:

```json
{
  "noteId": "g_te_iru",
  "scope": {
    "blockId": "b_promise",
    "phraseId": "p_remember"
  },
  "explanation": "The continuative aspect marks an ongoing state.",
  "labels": [
    {"namespace": "lang:ja", "key": "te_iru_continuative"}
  ],
  "confidence": 0.86
}
```

`blockId` is mandatory. `phraseId` is optional, but when present the phrase
must be a member of that block.

## Labels and Language

- `namespace` is lowercase, at least two characters, and may use `:`, e.g.
  `lang:ja`, `syntax`, `discourse`, `register`.
- `key` is plain text, at most 64 characters.
- Source language codes use lowercase primary tags with optional subtags,
  e.g. `ja`, `ko`, `zh`, `en`, `zh-Hant`.
- Do not create Japanese-only top-level fields. Put language-specific terms
  under namespaced labels and structured prose fields.

## Content Rules

- No `<tag>`, `&entity;`, `<!DOCTYPE ...>`, CDATA, or processing-instruction
  syntax in any string.
- No absolute filesystem paths.
- `source.lrcFile` is a sibling basename only, never a directory path.
- Every string is UTF-8 and may contain emoji or combining characters.
- The sidecar has no per-line translation fields. Translation remains in the
  accepted bilingual LRC.
