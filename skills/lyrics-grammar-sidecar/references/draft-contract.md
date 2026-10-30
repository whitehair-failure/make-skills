# Semantic Draft Contract v2

`semantic-draft.json` is the grammar-analysis child's only authored artifact. It is not a production sidecar. Use `../schema/v2/lyrics-grammar-analysis.schema.json`; v1 is unsupported.

The draft must be based on the original LRC and the accepted, frozen `translated.lrc`. The deterministic pipeline normalizes projected source fields, discovers exact repetition candidates, creates `analysis-candidate.json`, and later requires an independent semantic audit.

## Pipeline Boundary

```text
semantic-draft.json
  -> discover_repetitions.py
  -> copy every `repetitionId` + `occurrences` candidate into the draft
  -> prepare.py
  -> analysis-candidate.json + analysis-candidate.sha256
  -> independent semantic-audit child
  -> semantic-audit.json
  -> grammar/audit isolation proofs
  -> finalize.py
  -> sibling <song>.json
```

The grammar child may not invent, merge, shorten, lengthen, or omit a repetition candidate. If the draft fails preparation, revise the semantic draft in a fresh grammar child; do not edit the frozen translation.

## Top Level

```json
{
  "schema": "sakurauta.lyrics-grammar-analysis",
  "schemaVersion": 2,
  "source": {
    "lrcFile": "Song.lrc",
    "songId": "Artist - Song",
    "sourceLanguages": ["ja"],
    "lineCount": 12,
    "lrcProjectionHash": "sha256:..."
  },
  "lines": [],
  "repetitionGroups": [],
  "meaningUnits": [],
  "blocks": [],
  "phrases": [],
  "relations": [],
  "grammarNotes": [],
  "uncertainties": [],
  "extensions": {
    "learnerLanguage": "zh-CN"
  }
}
```

All listed fields are required. `extensions.learnerLanguage` is required and must use the workflow target language.

Do not add a `meaning.text`, per-line translation field, v1 compatibility field, or any second snapshot of canonical text.

## Source Lines

Copy every line from `source-projection.json`, add `language`, and optionally add `endTimeMs` / `languageSpans`:

```json
{
  "lineId": "l_...",
  "sourceOrder": 0,
  "timeMs": 1000,
  "text": "夜明けの歌を君と歌う",
  "fingerprint": "sha256:...",
  "role": "text",
  "language": "ja",
  "languageSpans": [
    {"start": 0, "end": 10, "language": "ja"}
  ]
}
```

`start` and `end` are Unicode code-point indexes, not UTF-16 units. The preparer rebuilds every projected source field from the frozen projection; a model-side edit fails with `stale_source_projection`.

## Repetition Discovery

Repetition discovery is deterministic and owned by this Skill. After the draft's lines and blocks are complete, run:

```powershell
python scripts\discover_repetitions.py `
  "<workdir>\semantic-draft.json" `
  --projection "<workdir>\source-projection.json" `
  --workdir "<workdir>" `
  --learner-language "<learner language>" `
  --replace
```

The tool writes `repetition-candidates.json`. Copy each candidate's `repetitionId` and `occurrences` array verbatim into the draft:

```json
{
  "repetitionId": "rep_...",
  "occurrences": [
    {
      "occurrenceId": "repo_...",
      "blockId": "b_chorus1",
      "sourceSegments": [
        {
          "lineId": "l_line1",
          "start": 0,
          "end": 10,
          "text": "夜明けの歌を君と歌う"
        }
      ]
    }
  ]
}
```

Do not copy the candidate tool's auxiliary `normalizedText` field into the draft.

Discovery rules:

- source line boundaries are removed before comparison;
- the concatenated source span is NFC-normalized as one string; do not normalize each line independently;
- matching is exact and maximal, never fuzzy;
- internal whitespace remains significant;
- only spans of at least 8 Unicode code points are emitted;
- metadata blocks (`discourse:metadata`) are excluded;
- overlapping groups are allowed;
- one group has at most one occurrence per block.

Every candidate must remain present after any later draft edit. If blocks or lines change, rerun discovery and replace the draft's candidate list exactly.

## Blocks and Composed Meaning

Blocks are learning units. Their `meaning` contains ordered references to meaning-unit occurrences, not copied text:

```json
{
  "blockId": "b_chorus3",
  "lineIds": ["l_line20", "l_line21"],
  "startTimeMs": 20000,
  "endTimeMs": 21000,
  "meaning": {
    "parts": [
      {
        "meaningUnitOccurrenceId": "muo_chorus3_shared"
      },
      {
        "meaningUnitOccurrenceId": "muo_chorus3_tail",
        "textBefore": "，"
      }
    ]
  },
  "labels": [],
  "confidence": 0.92
}
```

Effective text is the concatenation of `textBefore + effectiveMeaningText` for each part. Parts must be:

- source ordered;
- non-overlapping;
- within the block;
- sufficient to cover every `lineId` in the block at least once.

`textBefore` is learner-facing connective text, limited to 120 characters. Every changed tail or otherwise uncovered line requires its own meaning-unit occurrence.

## Meaning Units

A meaning unit owns the canonical learner-facing synthesis shared by all of its occurrences:

```json
{
  "meaningUnitId": "mu_chorus_shared",
  "text": "在黎明的歌声中，两人守着同一个约定。",
  "labels": [],
  "confidence": 0.94,
  "occurrences": [
    {
      "occurrenceId": "muo_chorus1_shared",
      "blockId": "b_chorus1",
      "sourceSegments": [
        {
          "lineId": "l_chorus1_line1",
          "start": 0,
          "end": 10,
          "text": "夜明けの歌を君と歌う"
        }
      ]
    },
    {
      "occurrenceId": "muo_chorus3_shared",
      "blockId": "b_chorus3",
      "sourceSegments": [
        {
          "lineId": "l_chorus3_line1",
          "start": 0,
          "end": 10,
          "text": "夜明けの歌を君と歌う"
        }
      ]
    }
  ]
}
```

Each occurrence has one contiguous source span. Cross-line segments must use immediately adjacent source lines; the previous segment ends at its line's end, and the next starts at code point zero.

Canonical `text` must be a faithful synthesis of the frozen translation. Do not invent motives, causes, setting, intensity, editorial judgements, or meta-narrative absent from the accepted translation.

Only an explicit occurrence override may change the effective text:

```json
{
  "occurrenceId": "muo_chorus3_tail",
  "blockId": "b_chorus3",
  "sourceSegments": [
    {
      "lineId": "l_chorus3_tail",
      "start": 0,
      "end": 8,
      "text": "あの日あの場所で"
    }
  ],
  "semanticOverride": {
    "reason": "该处重复后语气转为再次确认。",
    "text": "再次唱响时强调重新出发的决心。"
  }
}
```

The reason is required and is reviewed by the semantic auditor.

## Phrases and Occurrences

A phrase owns canonical surface and grammar semantics. Each occurrence owns one block and one contiguous source span:

```json
{
  "phraseId": "p_dawn_song",
  "kind": "phrase",
  "surface": "夜明けの歌を君と歌う",
  "role": "shared_theme",
  "labels": [
    {"namespace": "lang:ja", "key": "noun_phrase"}
  ],
  "explanation": "把黎明、歌声与同行者连成一个完整画面。",
  "gloss": "与你同唱黎明之歌",
  "confidence": 0.94,
  "occurrences": [
    {
      "occurrenceId": "po_dawn_song_1",
      "blockId": "b_chorus1",
      "sourceSegments": [
        {
          "lineId": "l_chorus1_line1",
          "start": 0,
          "end": 10,
          "text": "夜明けの歌を君と歌う"
        }
      ]
    },
    {
      "occurrenceId": "po_dawn_song_3",
      "blockId": "b_chorus3",
      "sourceSegments": [
        {
          "lineId": "l_chorus3_line1",
          "start": 0,
          "end": 10,
          "text": "夜明けの歌を君と歌う"
        }
      ]
    }
  ]
}
```

- `surface` must be NFC-equal to every occurrence's exact source span.
- A phrase may occur multiple times in one block.
- Canonical prose is shared automatically; never copy it per occurrence.
- A relation points to the canonical `phraseId` and therefore applies to every occurrence.
- An occurrence override may replace `role`, `labels`, `explanation`, `gloss`, `notes`, or `alternatives`, but must include `reason`.

If a phrase occurrence is wholly inside a repetition occurrence, every block in that repetition group must contain a corresponding phrase occurrence at the same canonical offset. Missing mappings fail with `shared-occurrence-incomplete`; wrong mappings fail with `shared-occurrence-mismatch`.

The same shared-occurrence rule applies to meaning-unit occurrences.

## Notes, Relations, and Multi-Block Scope

Relations connect canonical phrases:

```json
{
  "relationId": "r_theme_follow",
  "fromPhraseId": "p_dawn_song",
  "toPhraseId": "p_same_promise",
  "type": "develops_theme",
  "labels": [{"namespace": "syntax", "key": "theme_development"}],
  "explanation": "歌唱的画面落到共同守护的约定上。",
  "confidence": 0.86
}
```

Grammar notes and uncertainties use block arrays:

```json
{
  "scope": {
    "blockIds": ["b_chorus1", "b_chorus3"],
    "phraseId": "p_dawn_song",
    "occurrenceIds": ["po_dawn_song_1", "po_dawn_song_3"]
  }
}
```

- `blockIds` is required, non-empty, and unique.
- Without `phraseId`, the scope is block-only and `occurrenceIds` is forbidden.
- With `phraseId`, every listed block must contain that phrase.
- `occurrenceIds`, when present, narrows the scope to explicit phrase occurrences and all must belong to the listed blocks.

Use one canonical note or relation rather than duplicating it per repeated block.

## Learner Language

All learner-facing prose must use `extensions.learnerLanguage`:

- meaning-unit `text` and override `text`;
- block meaning `textBefore`;
- phrase `gloss`, `explanation`, `notes`, alternatives, and overrides;
- relation/note explanations;
- uncertainty messages and alternatives;
- override reasons.

Stable identifiers such as `role`, `register`, `relation.type`, and label namespace/key may remain English. A gloss is learner-facing prose, not a machine identifier.

No present learner-facing field may be blank or whitespace-only. Omit an optional field instead of emitting an empty string.

## Content Guardrails

- No inline HTML, entities, CDATA, declarations, or processing instructions.
- Reject duplicate JSON keys, non-finite numbers, unpaired UTF-16 surrogates, and invalid UTF-8 before validation.
- No absolute filesystem paths.
- `source.lrcFile` is a visible sibling basename only.
- No per-line translation fields.
- IDs are globally unique.
- Every numeric span refers to source Unicode code points.
- Unknown fields fail closed through the v2 schema.

## Semantic Audit Handoff

After `prepare.py` succeeds, the parent records the grammar child proof and dispatches a different fresh child using [semantic-audit-contract.md](semantic-audit-contract.md). The auditor reviews `analysis-candidate.json` as the canonical artifact, may read this draft as supporting context, and writes only `semantic-audit.json`.
