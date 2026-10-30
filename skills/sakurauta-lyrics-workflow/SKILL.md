---
name: sakurauta-lyrics-workflow
description: Create a bilingual LRC translation and a SakuraUta Schema v2 grammar-analysis sidecar. The parent coordinates three fresh child roles for translation, grammar analysis, and independent semantic audit; deterministic scripts freeze and finalize the contract.
version: 2.0.0
---

# SakuraUta Lyrics Workflow v2

The parent coordinates three independent child roles. It does not translate, invent grammar analysis, or self-audit semantics inline.

1. Translation child: reads `lyrics-translator` directly.
2. Grammar child: authors only the v2 semantic draft. A deterministic tool writes its repetition-candidate list, and the parent freezes the candidate.
3. Semantic-audit child: a different fresh transcript that reviews the frozen candidate and writes only the audit report.

Only the deterministic finalizer writes `<song>.json`.

## Inputs

- local source `.lrc`;
- target learner language, for example `zh-CN`;
- SakuraUta `songId` (default: LRC basename without `.lrc`).

Work directory: `<song>.grammar-work` beside the source.

Reject video, subtitle, remote, non-LRC, network-share, and symlinked inputs.

## Isolation Gate

A child is a fresh non-forked sub-agent or a fresh independent CLI transcript. Loading a Skill into the parent is not isolation.

The grammar and semantic-audit children must have different `childTranscriptId` values. When the harness exposes parent IDs, both children must also differ from the parent. If separate contexts are unavailable, stop with:

```text
ERROR [isolation-unavailable]
```

Never fall back to inline translation, grammar analysis, or self-audit.

## Phase 1: Translate and Freeze

1. Create the source projection:

```powershell
python <grammar-skill>\scripts\project_source.py "<song.lrc>" `
  --song-id "<songId>"
```

2. Dispatch a fresh translation child:

```text
Read and follow <skills>\lyrics-translator\SKILL.md.
Translate <song.lrc> into <target learner language>.
Write the accepted bilingual LRC to <workdir>\accepted-translated.lrc.
Do not produce grammar JSON.
```

Do not add a translation wrapper Skill or restate the translator's quality rules.

3. After the user accepts the translation, freeze it:

```powershell
python <grammar-skill>\scripts\verify_translation.py `
  "<workdir>\source-projection.json" `
  "<workdir>\accepted-translated.lrc" `
  --workdir "<workdir>"
```

The parent owns acceptance. Do not proceed if freezing fails. Do not edit `translated.lrc` after this point.

## Phase 2: Fresh Grammar Analysis

4. Dispatch a grammar child different from the parent:

```text
Read and follow <grammar-skill>\SKILL.md and
<grammar-skill>\references\draft-contract.md.
Read only the original LRC, source projection, and frozen translated.lrc.
Write only <workdir>\semantic-draft.json.
Use learner language <target>.
Run discover_repetitions.py after blocks are complete and copy every candidate
verbatim into repetitionGroups.
Do not write <song>.json and do not alter the frozen translation.
```

5. After the grammar child returns, record the actual grammar-child identity:

```powershell
python <grammar-skill>\scripts\record_isolation.py `
  "<workdir>\source-projection.json" `
  --stage grammar-analysis `
  --artifact "<workdir>\semantic-draft.json" `
  --method native-subagent `
  --child-transcript-id "<actual-grammar-child-id>" `
  --parent-transcript-id "<actual-parent-id>" `
  --output "<workdir>\isolation-grammar-analysis.json" `
  --replace
```

Never invent an ID.

6. Deterministically freeze the candidate:

```powershell
python <grammar-skill>\scripts\prepare.py "<song.lrc>" `
  --projection "<workdir>\source-projection.json" `
  --workdir "<workdir>" `
  --learner-language "<target>"
```

This validates projection/freeze identity, Schema v2, exact repetition completion, canonical source normalization, meaning coverage, and shared occurrence mappings. It writes:

- `analysis-candidate.json`;
- `analysis-candidate.sha256`;
- `repetition-candidates.json`;
- `validation-report.json`.

If preparation fails, rerun grammar analysis in a new fresh transcript. Do not patch the candidate or translation.

## Phase 3: Independent Semantic Audit

7. Dispatch a different fresh child:

```text
Read and follow <grammar-skill>\references\semantic-audit-contract.md.
Audit <workdir>\analysis-candidate.json against the original LRC, frozen
translated.lrc, and candidate SHA-256. The semantic draft is read-only
supporting context; the candidate is the reviewed artifact.
Write only <workdir>\semantic-audit.json.
Do not edit the draft, candidate, hash, translation, or <song>.json.
Use learner language <target>.
```

The audit must review every candidate block and meaning-unit ID. It must reject unsupported additions, translation mismatches, shared semantic drift, coverage gaps, and missing source evidence.

8. Validate the audit report:

```powershell
python <grammar-skill>\scripts\validate_audit.py `
  "<workdir>\analysis-candidate.json" `
  --candidate-hash "<workdir>\analysis-candidate.sha256" `
  --learner-language "<target>" `
  "<workdir>\semantic-audit.json"
```

9. After validating the report, record the actual audit-child identity:

```powershell
python <grammar-skill>\scripts\record_isolation.py `
  "<workdir>\source-projection.json" `
  --stage semantic-audit `
  --artifact "<workdir>\semantic-audit.json" `
  --method native-subagent `
  --child-transcript-id "<actual-audit-child-id>" `
  --parent-transcript-id "<actual-parent-id>" `
  --output "<workdir>\isolation-semantic-audit.json" `
  --replace
```

This ID must differ from `<actual-grammar-child-id>`. A same-child audit is always rejected.

If the audit fails, do not finalize. Rerun analysis and audit in fresh transcripts after resolving the finding, or ask the user to resolve the semantic dispute.

## Phase 4: Finalize and Verify

10. Run the deterministic finalizer:

```powershell
python <grammar-skill>\scripts\finalize.py "<song.lrc>" `
  --projection "<workdir>\source-projection.json" `
  --workdir "<workdir>" `
  --learner-language "<target>"
```

The finalizer requires:

- unchanged source projection and frozen translation;
- draft-bound grammar proof;
- unchanged candidate hash;
- passing audit bound to that candidate;
- audit-bound different-child proof;
- v2 isolation proofs and matching source projection hashes.

Only after every check does it atomically write the sibling JSON.

11. Verify the result and complete chain:

```powershell
python <grammar-skill>\scripts\validate_all.py `
  "<song.lrc>" "<song.json>" `
  --projection "<workdir>\source-projection.json" `
  --workdir "<workdir>" `
  --learner-language "<target>"
```

Report the final path and validation status.

## Boundaries

- `<skills>` contains this Skill, `lyrics-translator`, and `lyrics-grammar-sidecar`.
- Translation remains byte-for-byte owned by `lyrics-translator`.
- Grammar semantics are authored by the fresh grammar child.
- Semantic approval is owned by the different fresh audit child, never the grammar child or parent.
- Deterministic scripts own Schema v2, repetition discovery/completion, source/span validation, hash chaining, isolation verification, and atomic writes.
- No v1 fallback, migration, or dual read exists.
