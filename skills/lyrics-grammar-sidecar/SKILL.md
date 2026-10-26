---
name: lyrics-grammar-sidecar
description: Generate, validate, and finalize a SakuraUta lyrics grammar analysis JSON sidecar for a local LRC file. Use as the grammar-analysis child of sakurauta-lyrics-workflow, or standalone when a frozen bilingual LRC and a semantic draft must be validated. Contains its own schema, deterministic scripts, contracts, and fixtures.
version: 1.1.0
---

# Lyrics Grammar Sidecar

This Skill is self-contained: its schema is
`schema/v1/lyrics-grammar-analysis.schema.json` and its scripts live under
`scripts/`. Do not depend on the repository root or absolute developer paths.

## Grammar Child Workflow

The parent or user must provide:

- the original local `.lrc`;
- `<song>.grammar-work/source-projection.json`;
- the frozen `<song>.grammar-work/translated.lrc`;
- a real separate-transcript identity.

Read [references/draft-contract.md](references/draft-contract.md) and write
only `<workdir>/semantic-draft.json`. Do not copy per-line translations into
the draft. Do not write the sibling `<song>.json`.

Record the proof:

```powershell
python scripts\record_isolation.py `
  "<workdir>\source-projection.json" `
  --method native-subagent `
  --child-transcript-id "<actual-child-id>" `
  --parent-transcript-id "<actual-parent-id>" `
  --output "<workdir>\isolation-proof.json"
```

The method and IDs must reflect the real Harness. Inline or forked analysis is
rejected.

## Projection

For a new song, create the canonical source projection:

```powershell
python scripts\project_source.py "<song.lrc>" `
  --song-id "<songId>"
```

The default output is `<song>.grammar-work/source-projection.json`. The
projection preserves physical source order, rounded milliseconds, the first
source row at each timestamp, NFC text, and content-derived line IDs.

## Translation Handoff

The translation is completed by the unchanged `lyrics-translator` Skill. After
the user accepts it, freeze it:

```powershell
python scripts\verify_translation.py `
  "<workdir>\source-projection.json" `
  "<accepted-translated.lrc>" `
  --workdir "<workdir>"
```

The freeze writes `translated.lrc` and `translation-manifest.json`. The
manifest hash is checked before analysis and finalization, so a later edit
cannot silently change accepted translation.

## Finalize and Validate

Only the deterministic finalizer writes the production sidecar:

```powershell
python scripts\finalize.py "<song.lrc>" `
  --projection "<workdir>\source-projection.json" `
  --workdir "<workdir>"
```

Then run the full integrity check:

```powershell
python scripts\validate_all.py `
  "<song.lrc>" "<song.json>" `
  --projection "<workdir>\source-projection.json" `
  --workdir "<workdir>"
```

The output is always `dirname(lrc) / basename(lrc, ".lrc") + ".json"`.
Existing different output is preserved unless the user explicitly asks for
`--replace`.

## Validation Boundary

The Skill validates deterministic structure: schema, source identity,
timestamps, fingerprints, coverage, cross-references, Unicode code-point
spans, labels, HTML-like text, frozen hashes, isolation evidence, and atomic
writes. It never attempts to decide whether a semantic explanation is good.

Language-specific grammar remains in namespaced labels and prose; the schema
does not require a Japanese-specific field set.

## Self-Test

Run from any current directory:

```powershell
python scripts\selftest.py
```

The test resolves the schema relative to this Skill and uses only temporary
fixtures. It must pass when the three related Skill directories are copied to
a different location:

```text
sakurauta-lyrics-workflow/
lyrics-translator/
lyrics-grammar-sidecar/
```
