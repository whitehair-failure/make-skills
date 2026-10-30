# make-skills

Reusable, user-invoked Skills maintained outside the SakuraUta application.

## SakuraUta Lyrics Workflow v2

Entry point:
`skills/sakurauta-lyrics-workflow/SKILL.md`.

The parent coordinates three fresh child roles:

1. **Translation child** reads the unchanged
   `skills/lyrics-translator/SKILL.md` directly and produces an accepted
   bilingual LRC. There is no translation wrapper Skill.
2. **Grammar child** reads `skills/lyrics-grammar-sidecar/SKILL.md` and its v2
   draft contract. It authors only `semantic-draft.json`; deterministic tools
   write the repetition-candidate list and freeze the analysis candidate.
3. **Semantic-audit child** is a different fresh transcript. It reviews
   `analysis-candidate.json` against the original LRC and frozen translation,
   then writes only `semantic-audit.json`.

The parent never self-audits semantics. Only the deterministic finalizer
writes the production sibling JSON.

Schema v1 is not accepted or migrated. Existing v1 files must be regenerated.

### Discovery

- Main workflow: `skills/sakurauta-lyrics-workflow/SKILL.md`
- Existing translator: `skills/lyrics-translator/SKILL.md`
- Grammar Skill: `skills/lyrics-grammar-sidecar/SKILL.md`
- Grammar schema:
  `skills/lyrics-grammar-sidecar/schema/v2/lyrics-grammar-analysis.schema.json`
- Audit schema:
  `skills/lyrics-grammar-sidecar/schema/v2/semantic-audit.schema.json`
- Isolation proof schema:
  `skills/lyrics-grammar-sidecar/schema/v2/isolation-proof.schema.json`
- Repetition candidate schema:
  `skills/lyrics-grammar-sidecar/schema/v2/repetition-candidates.schema.json`
- Draft contract:
  `skills/lyrics-grammar-sidecar/references/draft-contract.md`
- Audit contract:
  `skills/lyrics-grammar-sidecar/references/semantic-audit-contract.md`
- Fixtures: `skills/lyrics-grammar-sidecar/fixtures/`

All three Skill directories can be copied together. The grammar Skill resolves
its schemas relative to its own `SKILL.md`.

### Invocation

Set the paths once:

```powershell
$Skills = "C:\path\to\skills"
$Workflow = "$Skills\sakurauta-lyrics-workflow"
$Grammar = "$Skills\lyrics-grammar-sidecar"
$Work = "C:\Music\Song.grammar-work"
```

#### 1. Source projection

```powershell
python "$Grammar\scripts\project_source.py" "C:\Music\Song.lrc" `
  --song-id "<SakuraUta songId>"
```

The default output is `C:\Music\Song.grammar-work\source-projection.json`.

#### 2. Translation child and freeze

Dispatch a fresh child:

```text
Read and follow $Skills\lyrics-translator\SKILL.md.
Translate C:\Music\Song.lrc into <target language>.
Write the accepted bilingual LRC to <workdir>\accepted-translated.lrc.
Do not produce grammar JSON.
```

After user acceptance:

```powershell
python "$Grammar\scripts\verify_translation.py" `
  "$Work\source-projection.json" "$Work\accepted-translated.lrc" `
  --workdir $Work
```

#### 3. Fresh grammar child

```text
Read and follow $Grammar\SKILL.md and
$Grammar\references\draft-contract.md.
Read only the original LRC, source projection, and frozen translated.lrc.
Write <workdir>\semantic-draft.json.
Run repetition discovery after blocks are complete and copy every candidate
verbatim into repetitionGroups.
Do not edit the translation or write <song>.json.
```

The child may run:

```powershell
python "$Grammar\scripts\discover_repetitions.py" `
  "$Work\semantic-draft.json" `
  --projection "$Work\source-projection.json" `
  --workdir $Work `
  --learner-language "<target language>" `
  --replace
```

After the draft is final, record the actual child ID:

```powershell
python "$Grammar\scripts\record_isolation.py" `
  "$Work\source-projection.json" `
  --stage grammar-analysis `
  --artifact "$Work\semantic-draft.json" `
  --method native-subagent `
  --child-transcript-id "<actual-grammar-child-id>" `
  --parent-transcript-id "<actual-parent-id>" `
  --output "$Work\isolation-grammar-analysis.json" `
  --replace
```

Freeze the candidate:

```powershell
python "$Grammar\scripts\prepare.py" "C:\Music\Song.lrc" `
  --projection "$Work\source-projection.json" `
  --workdir $Work `
  --learner-language "<target language>"
```

This writes:

- `analysis-candidate.json`;
- `analysis-candidate.sha256`;
- `repetition-candidates.json`;
- `validation-report.json`.

#### 4. Independent semantic-audit child

Dispatch a different fresh transcript:

```text
Read and follow $Grammar\references\semantic-audit-contract.md.
Audit <workdir>\analysis-candidate.json against the original LRC, frozen
translated.lrc, and its SHA-256. The semantic draft is read-only supporting
context; the candidate is the reviewed artifact.
Write only <workdir>\semantic-audit.json.
Do not edit the candidate, draft, translation, or <song>.json.
```

Validate the report:

```powershell
python "$Grammar\scripts\validate_audit.py" `
  "$Work\analysis-candidate.json" `
  --candidate-hash "$Work\analysis-candidate.sha256" `
  --learner-language "<target language>" `
  "$Work\semantic-audit.json"
```

Record the audit-child proof:

```powershell
python "$Grammar\scripts\record_isolation.py" `
  "$Work\source-projection.json" `
  --stage semantic-audit `
  --artifact "$Work\semantic-audit.json" `
  --method native-subagent `
  --child-transcript-id "<actual-audit-child-id>" `
  --parent-transcript-id "<actual-parent-id>" `
  --output "$Work\isolation-semantic-audit.json" `
  --replace
```

The grammar and audit child IDs must differ.

#### 5. Finalize and verify

```powershell
python "$Grammar\scripts\finalize.py" "C:\Music\Song.lrc" `
  --projection "$Work\source-projection.json" `
  --workdir $Work `
  --learner-language "<target language>"

python "$Grammar\scripts\validate_all.py" `
  "C:\Music\Song.lrc" "C:\Music\Song.json" `
  --projection "$Work\source-projection.json" `
  --workdir $Work `
  --learner-language "<target language>"
```

The generated file is always
`dirname(lrc) / basename(lrc, ".lrc") + ".json"`. Existing differing output is
preserved unless the user explicitly passes `--replace`.
An existing Schema v1 production file must be regenerated with `--replace`;
it is never read as compatible input.

### Hash and Isolation Chain

```text
source projection
  -> semantic-draft.json
  -> analysis-candidate.json
  -> semantic-audit.json
  -> isolation proofs(stage, artifactSha256)
  -> final <song>.json
```

The grammar proof binds the draft. The audit report binds the candidate hash.
The audit proof binds the report. The finalizer rechecks every link before the
atomic write.

### Language Contract

Both grammar and audit children receive the same target language. All
learner-facing prose must use it. New v2 files record it in
`extensions.learnerLanguage`; this field is required. Stable machine
identifiers may remain English.

### Verification

Python 3.10+ standard library only:

```powershell
python skills\lyrics-grammar-sidecar\scripts\selftest.py
git diff --exit-code -- skills/lyrics-translator
```

The suite covers ja/ko/zh/en and Unicode fixtures, Schema v2 and v1 rejection,
exact maximal NFC repetition, line-boundary variation, cross-line NFC
composition, internal whitespace, short/similar non-matches, metadata
exclusion, overlapping and three-occurrence groups, changed tails, canonical
phrase/meaning inheritance, explicit overrides, meaning coverage, strict JSON,
path/HTML guards, independent audit pass/fail, proof/hash/canonical-byte
tamper, and atomic finalization.

`fixtures/golden-vectors.json` pins line identity, Unicode spans, and
cross-line NFC composition; the compact `fixtures/repetition` case pins the
three-occurrence v2 shape for SakuraUta's cross-repository checks. Its full
candidate, audit, isolation, and finalize artifacts are generated in temporary
workdirs instead of being committed. The fixture's `.gitattributes` keeps the
JSON/LRC golden bytes stable across Git checkouts.
