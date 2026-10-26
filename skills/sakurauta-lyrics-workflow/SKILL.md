---
name: sakurauta-lyrics-workflow
description: Create bilingual LRC translations and a SakuraUta grammar-analysis JSON sidecar for a local LRC file. Use when a user wants both translation and cross-line grammar learning data. The parent coordinates two fresh child tasks; translation uses the existing lyrics-translator unchanged, grammar analysis uses lyrics-grammar-sidecar, and only deterministic scripts write the final JSON.
version: 1.1.0
---

# SakuraUta Lyrics Workflow

Orchestrate two independent child tasks. Do not translate or analyse lyrics in
the parent context.

## Inputs

- local source `.lrc`;
- target language;
- SakuraUta `songId` (default: the LRC basename without `.lrc`).

The work directory is `<song>.grammar-work` beside the LRC.

Reject video, subtitle, remote, non-LRC, and symlinked inputs.

## Isolation Gate

A child must be a fresh non-forked sub-agent or a fresh independent CLI
session. Loading a Skill into the parent is not isolation. If neither is
available, stop with `ERROR [isolation-unavailable]`; never fall back to
inline translation or analysis. Shared files are allowed; the frozen hashes
detect changes.

## Phase 1: Translate

1. Create the source projection with the grammar Skill:

```powershell
python <grammar-skill>\scripts\project_source.py "<song.lrc>" `
  --song-id "<songId>"
```

2. Dispatch a fresh translation child. Give it the source LRC, target
   language, and accepted-output path. Tell it to read and follow
   `<skills>\lyrics-translator\SKILL.md` directly. Do not add a translation
   wrapper Skill or restate the translator's quality rules.

3. After the child returns, ask the user to accept the translation. Then
   freeze the accepted bilingual LRC:

```powershell
python <grammar-skill>\scripts\verify_translation.py `
  "<workdir>\source-projection.json" "<accepted.lrc>" `
  --workdir "<workdir>"
```

Do not proceed if freezing fails. The parent owns acceptance; do not modify
the frozen file.

## Phase 2: Analyse

4. Record the real child identity:

```powershell
python <grammar-skill>\scripts\record_isolation.py `
  "<workdir>\source-projection.json" `
  --method native-subagent `
  --child-transcript-id "<actual-id>" `
  --parent-transcript-id "<actual-parent-id>" `
  --output "<workdir>\isolation-proof.json"
```

Use the method and IDs that actually apply. Never invent a child ID.

5. Dispatch a different fresh child. Tell it to read and follow
   `<grammar-skill>\SKILL.md` and its draft contract. It reads the original
   LRC, source projection, and frozen `translated.lrc`, then writes only
   `semantic-draft.json`. It must not edit inputs or write `<song>.json`.

6. If analysis fails, rerun only the analysis child with a fresh transcript.
   Keep the accepted translation frozen.

## Phase 3: Finalize

7. Run the deterministic finalizer:

```powershell
python <grammar-skill>\scripts\finalize.py "<song.lrc>" `
  --projection "<workdir>\source-projection.json" `
  --workdir "<workdir>"
```

8. Validate the result:

```powershell
python <grammar-skill>\scripts\validate_all.py `
  "<song.lrc>" "<song.json>" `
  --projection "<workdir>\source-projection.json" `
  --workdir "<workdir>"
```

Report the final sidecar path and the validation result. If validation fails,
fix only the semantic draft in a fresh analysis child; never rewrite the LRC
or accepted translation to make validation pass.

## Boundaries

- `<skills>` is the directory containing this Skill,
  `lyrics-translator`, and `lyrics-grammar-sidecar`.
- Translation is owned by the unchanged `lyrics-translator`.
- Grammar semantics are owned by the fresh analysis child.
- Schema, projection, freeze, reference, Unicode, isolation, and atomic-write
  checks are owned by `lyrics-grammar-sidecar`.
- Only the finalizer writes the production sibling JSON.
