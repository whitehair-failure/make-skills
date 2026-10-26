---
name: sakurauta-lyrics-workflow
description: SakuraUta's user-triggered workflow for translating a local LRC file and generating a read-only lyrics grammar sidecar. Use when a user wants bilingual lyrics plus cross-line grammar, phrase, relation, and learning annotations for SakuraUta. Coordinates two independent child Skills; translation is frozen before grammar analysis, and no inline fallback is allowed.
version: 1.0.0
---

# SakuraUta Lyrics Workflow

This is the user-facing entry point for:

1. translating a local `.lrc` through the unchanged `lyrics-translator`;
2. analysing the frozen bilingual lyrics in a separate transcript;
3. validating the draft and writing the sibling grammar sidecar with the
   deterministic generator.

The main Skill coordinates. It does not translate, infer grammar, or write the
production JSON itself.

## Skill Roots

Resolve these paths from the `skills/` directory containing this file:

```text
translation child: skills/lyrics-translation-handoff/SKILL.md
grammar child:     skills/lyrics-grammar-analysis/SKILL.md
translator:        skills/lyrics-translator/SKILL.md
generator:         skills/lyrics-grammar-sidecar/SKILL.md
```

Read a child Skill before giving its task to a child, but never treat reading a
file as proof of a separate context.

## Inputs

- `lrcPath`: an absolute path to a local `.lrc` file.
- `targetLanguage`: a primary language tag such as `zh`, `ja`, `ko`, or `en`.
- `workdir` (optional): `<song>.grammar-work` beside the LRC.
- `songId`: the SakuraUta identity string. If omitted, use the LRC basename
  without its extension; the deterministic projection must use the same value.

Supported input is only a local LRC. Reject videos, subtitles, remote tracks,
non-LRC files, and symlinked input paths.

## Mandatory Isolation Gate

Before doing any work, establish one of these capabilities:

1. **Native sub-agent** — a fresh, non-forked child transcript from the
   Harness.
2. **Independent CLI session** — a fresh session with a real transcript ID,
   only if the user explicitly asks for that path.

The parent sends each child a self-contained prompt: the child Skill path,
input paths, target language, and required output paths. It must not send the
parent's hidden reasoning or a ready-made analysis.

If neither capability exists, stop:

```text
ERROR [isolation-unavailable]
No independent translation/analysis context is available.
```

Do not run translation or grammar inline as a fallback. Do not create an
isolation proof with a made-up ID. Reading another `SKILL.md` in the parent
does not satisfy this gate.

Shared filesystem access is allowed. The frozen hashes prevent a child from
silently changing the accepted translation; the separate transcript is what
prevents the child from inheriting the parent's reasoning.

## Workflow

### 0. Preflight

Run:

```powershell
python scripts/preflight.py "C:\Music\Song.lrc" `
  --target-language zh `
  --song-id "Artist - Song"
```

For a non-default work directory, pass `--workdir`. The script creates or
checks `<song>.grammar-work` and writes `workflow-status.json`.

### 1. Translate in a Child

Give a fresh translation child:

- `skills/lyrics-translation-handoff/SKILL.md`;
- the source LRC path;
- the target language;
- the work directory;
- the required acceptance manifest path.

The child uses `lyrics-translator` as-is and freezes the accepted bilingual
LRC. It must not write grammar JSON.

After it returns, verify the phase:

```powershell
python scripts/check_phase.py "C:\Music\Song.grammar-work" `
  --phase frozen
```

Do not continue if the manifest is missing, the translation was modified, or
the source projection changed.

### 2. Analyse in a Different Child

Give a fresh grammar child:

- `skills/lyrics-grammar-analysis/SKILL.md`;
- the original LRC, the source projection, and the frozen `translated.lrc`;
- `skills/lyrics-grammar-sidecar/references/draft-contract.md`;
- the required output `semantic-draft.json` and an isolation-proof path.

The grammar child writes only `semantic-draft.json` and the proof. It must not
modify the frozen LRC, manifest, projection, or final sibling JSON.

After it returns:

```powershell
python scripts/check_phase.py "C:\Music\Song.grammar-work" `
  --phase drafted
```

### 3. Finalize Deterministically

Only the existing finalizer may write the production sidecar:

```powershell
python ..\lyrics-grammar-sidecar\scripts\finalize.py `
  "C:\Music\Song.lrc" `
  --projection "C:\Music\Song.grammar-work\source-projection.json" `
  --workdir "C:\Music\Song.grammar-work"
```

Then record and verify the final phase:

```powershell
python scripts/check_phase.py "C:\Music\Song.grammar-work" `
  --phase finalized `
  --sidecar "C:\Music\Song.json"
```

If validation fails, fix only `semantic-draft.json` in a fresh analysis
transcript and rerun the finalizer. Never rewrite the frozen translation or
source LRC to make the draft pass.

## Output

The work directory contains the auditable intermediate files:

```text
<song>.grammar-work/
  workflow-status.json
  source-projection.json
  accepted-translated.lrc
  translation-manifest.json
  translated.lrc
  isolation-proof.json
  semantic-draft.json
  validation-report.json
```

The only production artifact is the sibling `<song>.json`. Never commit the
work directory or copy temporary fixtures into a real music library.

## Failure Rules

| Failure | Required behavior |
| --- | --- |
| Non-LRC, subtitle, video, remote, or symlink input | reject in preflight |
| Missing target language or songId | reject before child dispatch |
| No independent context | `isolation-unavailable`; stop |
| Translation missing or frozen to the wrong source | do not start grammar |
| Frozen translation changed | stop; do not rewrite it |
| Isolation proof missing or forked | stop; no finalizer |
| Invalid draft | preserve the draft/report; fix draft only |
| Existing different sidecar | preserve it unless the user explicitly requests replacement |

## Verification

Run:

```powershell
python scripts\selftest.py
python ..\lyrics-grammar-sidecar\scripts\selftest.py
git diff --exit-code -- ..\lyrics-translator
```

The orchestration selftest must be environment-independent. Real Harness
evidence must use actual child transcript IDs, not fixtures that merely look
like IDs.
