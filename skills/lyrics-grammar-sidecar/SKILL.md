---
name: lyrics-grammar-sidecar
description: Generate, validate, audit, and finalize a SakuraUta Schema v2 lyrics grammar sidecar from a local LRC, frozen bilingual LRC, deterministic repetition candidates, and an independent semantic-audit child.
version: 2.0.0
---

# Lyrics Grammar Sidecar v2

This Skill is self-contained. Its schemas live under `schema/v2/`, scripts under `scripts/`, and authoring/audit contracts under `references/`. Do not depend on a repository root or developer machine path.

Schema v1 is unsupported. Do not dual-read, migrate, adapt, or fall back.

## Roles and Boundary

The overall workflow uses three fresh child roles:

1. translate with the unchanged `lyrics-translator` Skill;
2. analyze grammar in a fresh grammar child;
3. audit semantics in a different fresh audit child.

The grammar child writes only `semantic-draft.json`. The deterministic candidate tool may write `repetition-candidates.json`; `prepare.py` writes the candidate and its hash.

The audit child writes only `semantic-audit.json`. It cannot edit the candidate or draft.

Only `finalize.py` writes the production sibling `<song>.json`.

## Required Inputs

- original local `.lrc`;
- `<song>.grammar-work/source-projection.json`;
- accepted and frozen `<song>.grammar-work/translated.lrc` plus `translation-manifest.json`;
- target learner language, for example `zh-CN`;
- real non-forked transcript IDs for grammar analysis and semantic audit.

Reject video, subtitle, remote, network-share, non-LRC, and symlinked source inputs.

## 1. Project the Source

```powershell
python scripts\project_source.py "<song.lrc>" `
  --song-id "<SakuraUta songId>"
```

Default output: `<song-dir>/<song>.grammar-work/source-projection.json`.

The projection owns line IDs, source order, timestamps, fingerprints, source text, and the projection hash. Later stages may not rewrite them.

## 2. Freeze the Accepted Translation

The translation is produced by the unchanged `lyrics-translator` Skill on `<song.lrc>`. After the user accepts it:

```powershell
python scripts\verify_translation.py `
  "<workdir>\source-projection.json" `
  "<song.lrc>" `
  --workdir "<workdir>"
```

This writes `translated.lrc` and `translation-manifest.json`. (If an alternate accepted bilingual path was used, pass that instead). A later edit fails closed.

## 3. Fresh Grammar Child

Give the grammar child:

- the original LRC;
- source projection;
- frozen `translated.lrc`;
- requested learner language;
- [references/draft-contract.md](references/draft-contract.md).

The child records the target language:

```json
{
  "extensions": {
    "learnerLanguage": "zh-CN"
  }
}
```

It writes only `semantic-draft.json`. All learner-facing prose uses that language. Stable identifiers may remain English.

After lines and blocks exist, discover exact repetitions:

```powershell
python scripts\discover_repetitions.py `
  "<workdir>\semantic-draft.json" `
  --projection "<workdir>\source-projection.json" `
  --workdir "<workdir>" `
  --learner-language "<learner language>" `
  --replace --in-place
```

Pass `--in-place` to automatically update `repetitionGroups` in the draft, or copy every candidate's `repetitionId` and `occurrences` verbatim into `repetitionGroups`. The grammar child may not invent or alter groups.

Run `scripts\resolve_spans.py` to automatically verify or fix Unicode code-point spans:

```powershell
python scripts\resolve_spans.py `
  "<workdir>\semantic-draft.json" `
  --projection "<workdir>\source-projection.json" `
  --in-place
```

After the child returns and the draft is final, the parent records the actual grammar-child identity:

```powershell
python scripts\record_isolation.py `
  "<workdir>\source-projection.json" `
  --stage grammar-analysis `
  --artifact "<workdir>\semantic-draft.json" `
  --method native-subagent `
  --child-transcript-id "<actual-grammar-child-id>" `
  --parent-transcript-id "<actual-parent-id>" `
  --output "<workdir>\isolation-grammar-analysis.json" `
  --replace
```

Never invent transcript IDs. Inline or forked execution is rejected.

## 4. Freeze the Analysis Candidate

The parent runs the deterministic freeze after recording the grammar proof:

```powershell
python scripts\prepare.py "<song.lrc>" `
  --projection "<workdir>\source-projection.json" `
  --workdir "<workdir>" `
  --learner-language "<learner language>"
```

Preparation verifies:

- source projection still matches the original LRC;
- translation freeze is intact;
- Schema v2 and learner language are valid;
- source fields normalize exactly from the projection;
- every discovered repetition candidate is present and exact;
- meaning parts cover every block line;
- shared phrase/meaning occurrences map at identical canonical offsets.

Outputs:

- `analysis-candidate.json`;
- `analysis-candidate.sha256`;
- `repetition-candidates.json`;
- `validation-report.json`.

If preparation fails, revise the semantic draft in a fresh grammar child. Do not edit the frozen translation or hand-edit the candidate.

## 5. Different Fresh Semantic-Audit Child

Dispatch a fresh child whose transcript differs from the grammar child and parent. Give it the original LRC, frozen translation, candidate, candidate hash, read-only semantic draft for supporting context, and [references/semantic-audit-contract.md](references/semantic-audit-contract.md).

The parent dispatches the child. The audit child writes only `semantic-audit.json`. It must review every candidate block and meaning unit and explicitly report unsupported additions, translation mismatches, shared drift, coverage gaps, and missing evidence.

Validate the report:

```powershell
python scripts\validate_audit.py `
  "<workdir>\analysis-candidate.json" `
  --candidate-hash "<workdir>\analysis-candidate.sha256" `
  --learner-language "<learner language>" `
  "<workdir>\semantic-audit.json"
```

After validation, the parent records the audit-child proof:

```powershell
python scripts\record_isolation.py `
  "<workdir>\source-projection.json" `
  --stage semantic-audit `
  --artifact "<workdir>\semantic-audit.json" `
  --method native-subagent `
  --child-transcript-id "<actual-audit-child-id>" `
  --parent-transcript-id "<actual-parent-id>" `
  --output "<workdir>\isolation-semantic-audit.json" `
  --replace
```

An audit `fail`, error finding, missing reviewed ID, candidate-hash mismatch, same-child self-audit, or v1 proof blocks finalization.

## 6. Finalize

The parent runs the deterministic finalizer:

```powershell
python scripts\finalize.py "<song.lrc>" `
  --projection "<workdir>\source-projection.json" `
  --workdir "<workdir>" `
  --learner-language "<learner language>"
```

The finalizer rechecks the full chain:

```text
source projection
  -> semantic-draft.json
  -> analysis-candidate.json
  -> semantic-audit.json
  -> isolation proofs(stage, artifactSha256)
  -> final <song>.json
```

It atomically writes:

```text
dirname(lrc) / basename(lrc, ".lrc") + ".json"
```

Existing differing output is preserved unless the user explicitly passes `--replace`. An existing Schema v1 production file must be regenerated with `--replace`; there is no v1 read or migration path.

Then validate the production result and chain:

```powershell
python scripts\validate_all.py `
  "<song.lrc>" "<song.json>" `
  --projection "<workdir>\source-projection.json" `
  --workdir "<workdir>" `
  --learner-language "<learner language>"
```

## Batch Operations

The deterministic CLI tools natively support batch execution across multiple songs:

- **Batch Source Projection**:

```powershell
python scripts\project_source.py "<song1.lrc>" "<song2.lrc>" ...
```

Derives `songId` from each file's stem and writes to `<song>.grammar-work\source-projection.json`.

- **Batch Translation Freeze**:

```powershell
python scripts\verify_translation.py --batch "<song1.lrc>" "<song2.lrc>" ...
```

Verifies bilingual LRC content against each song's source projection and writes `translated.lrc` and `translation-manifest.json` in each song's work directory.

- **Batch Candidate Preparation**:

```powershell
python scripts\prepare.py "<song1.lrc>" "<song2.lrc>" ... --learner-language "<learner language>"
```

Validates projections, schemas, repetitions, and mappings across all songs, generating candidates and hashes in their respective work directories.

- **Batch Finalization**:

```powershell
python scripts\finalize.py "<song1.lrc>" "<song2.lrc>" ... --learner-language "<learner language>"
```

Atomically produces sibling `<song>.json` for each song passing full cryptographic and audit chain checks.

- **Batch Complete Validation**:

```powershell
python scripts\validate_all.py --batch "<song1.lrc>" "<song2.lrc>" ... --learner-language "<learner language>"
```

Verifies the production artifacts and complete audit chains across all songs, returning exit code 0 on complete batch success.

## Validation Boundary

Deterministic validation owns:

- Schema v2 and v1 rejection;
- strict UTF-8 JSON with duplicate-key, non-finite-number, and surrogate rejection;
- source identity and projection hashes;
- Unicode code-point spans and contiguous occurrence mapping;
- exact NFC repetition discovery and candidate completion;
- canonical occurrence inheritance and explicit override structure;
- composed meaning coverage;
- references, multi-block scopes, learner language, HTML/path guards;
- candidate/audit/proof hash chain and atomic writes.

It does not decide whether prose is faithful. That is the independent audit child's responsibility. The generation child must never self-approve its own semantic audit.

## Self-Test

Run from any current directory:

```powershell
python scripts\selftest.py
```

The standard-library-only suite generates every prepare -> audit -> finalize chain in a temporary workdir. Its only committed repetition case is the compact `fixtures/repetition` input set (source LRC, accepted translation, semantic draft, and final sidecar golden), so candidate, audit, isolation-proof, and validation-report copies are not duplicated in the repository. Coverage includes exact maximal and changed-tail repetition, three occurrences, overlap, line-break variation, cross-line NFC composition, internal whitespace, fuzzy non-matches, metadata exclusion, missing/wrong shared occurrences, blank overrides, meaning coverage, strict JSON, path and HTML guards, audit pass/fail, proof/hash/canonical-byte tamper, v1 rejection, and atomic finalization.
