---
name: lyrics-grammar-analysis
description: Produce a language-neutral SakuraUta semantic-draft JSON from frozen LRC translation inputs in an independent transcript. Use only as the grammar-analysis child of sakurauta-lyrics-workflow. Writes a draft and isolation evidence only; deterministic scripts own final validation and sidecar writing.
version: 1.0.0
---

# Lyrics Grammar Analysis

This child performs semantic analysis. It does not translate, freeze, or write
the production JSON.

## Inputs

- the original local `.lrc`;
- `source-projection.json`;
- the frozen `translated.lrc`;
- `<generator>/references/draft-contract.md`;
- an actual separate-transcript identity.

Read only the original LRC, projection, frozen translation, and the draft
contract. Do not read the parent's translation reasoning or any prewritten
semantic draft.

## Analysis Workflow

1. Confirm the original LRC source text, physical order, timestamps, and
   same-timestamp first source row agree with the projection. If not, stop.
2. Read the frozen translation only as the accepted meaning reference. It is
   not a second source line identity and must not be copied into the sidecar.
3. Determine language mix and source-line language spans from the actual
   lyrics. Use namespaced labels such as `lang:ja`, `syntax`, or `discourse`;
   do not add language-specific top-level fields.
4. Build cross-line blocks. A line may belong to multiple overlapping blocks;
   do not force a single segmentation.
5. Build phrases, clauses, and discourse units. Phrase source segments must be
   exact Unicode code-point slices of the canonical source lines and may span
   lines.
6. Add relations, grammar notes, block-level learning meanings, and
   uncertainty. Keep translations at the LRC level; the sidecar contains only
   whole-block meaning.
7. Write `semantic-draft.json` in the work directory using the draft contract.
   The finalizer will regenerate canonical identity fields.
8. Record a real separate-transcript proof:

```powershell
python ..\lyrics-grammar-sidecar\scripts\record_isolation.py `
  "<workdir>\source-projection.json" `
  --method native-subagent `
  --child-transcript-id "<actual-child-id>" `
  --parent-transcript-id "<parent-id>" `
  --output "<workdir>\isolation-proof.json"
```

Use the real method and IDs. Never invent an ID to pass the gate.

## Output Boundary

Allowed output:

```text
semantic-draft.json
isolation-proof.json
```

Never write:

- the original or frozen LRC;
- the language translator's files;
- `translation-manifest.json`;
- `source-projection.json`;
- `<song>.json`;
- a second line-by-line translation.

The parent and finalizer own the rest.

## Language Neutrality

The same structure serves Japanese, Korean, Chinese, English, and future
languages. Language-specific grammar belongs in `labels` or a documented
extension, not in new universal top-level fields. If a concept is uncertain,
record it in `uncertainties` rather than pretending it is a universal rule.
