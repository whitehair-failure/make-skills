---
name: lyrics-translation-handoff
description: Translate a local LRC through the existing lyrics-translator Skill, then freeze the accepted bilingual file for downstream grammar analysis. Use only as the translation child of sakurauta-lyrics-workflow. Does not generate grammar JSON and must not modify lyrics-translator.
version: 1.0.0
---

# Lyrics Translation Handoff

This child owns only the translation phase. It is deliberately narrow so the
main workflow can verify its output before grammar analysis.

## Prerequisite

Read and follow `skills/lyrics-translator/SKILL.md` exactly as it exists. Do
not copy, rewrite, or modify its translation rules.

## Inputs

- local source `.lrc`;
- target language tag;
- work directory;
- source projection path.

The parent must have created the work directory and projection with the
`sakurauta-lyrics-workflow` preflight. If either is missing, stop.

## Workflow

1. Inspect the LRC and target language.
2. Use `lyrics-translator` to create an accepted bilingual LRC. Preserve the
   original timestamps and source rows; insert exactly one target-language row
   below each source row according to the translator's existing rules.
3. Run quality acceptance before freezing. Do not infer grammar or write a
   sidecar while translating.
4. Freeze the accepted file:

```powershell
python ..\lyrics-grammar-sidecar\scripts\verify_translation.py `
  "<workdir>\source-projection.json" `
  "<accepted-translated.lrc>" `
  --workdir "<workdir>"
```

5. Return the exact paths and manifest result to the parent. The parent runs
   the phase check; do not claim success from prose alone.

## Must Not

- modify the original LRC;
- modify `lyrics-translator`;
- write `semantic-draft.json`;
- write the sibling `<song>.json`;
- overwrite an existing frozen manifest;
- continue after a translation freeze error.

## Failure

- missing `.lrc`: reject;
- source text or timeline changed: `stale_source_projection`;
- missing translation rows: `missing_translation`;
- changed after freeze: `translation_changed_after_freeze`.

All failures preserve the source and any existing frozen files.
