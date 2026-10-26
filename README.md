# make-skills

Reusable, user-invoked Skills maintained outside the SakuraUta application.

## Lyrics Grammar Sidecar

`skills/lyrics-grammar-sidecar` creates a block-level grammar analysis JSON
beside a local LRC. It is a user-triggered desktop/external workflow, not a
SakuraUta runtime feature: generation runs outside the app and needs no
database, `window.okashi`, app API, or model SDK dependency.

Discovery:

- Skill instructions: `skills/lyrics-grammar-sidecar/SKILL.md`
- Semantic draft contract: `skills/lyrics-grammar-sidecar/references/draft-contract.md`
- Sidecar v1 schema: `schema/v1/lyrics-grammar-analysis.schema.json`
- Fixtures: `skills/lyrics-grammar-sidecar/fixtures/`

The translator remains the existing `skills/lyrics-translator` Skill and is
treated as a black box. This new workflow adds only the separate grammar
analysis phase and deterministic validation/finalization.

### Invocation

Run from the `make-skills` repository root:

```powershell
$Skill = "skills\lyrics-grammar-sidecar"
python "$Skill\scripts\project_source.py" "C:\Music\Song.lrc" `
  --song-id "<SakuraUta songId>"

# Translate with skills\lyrics-translator, then freeze the accepted file.
python "$Skill\scripts\verify_translation.py" `
  "C:\Music\Song.grammar-work\source-projection.json" `
  "C:\Music\Song.grammar-work\accepted-translated.lrc" `
  --workdir "C:\Music\Song.grammar-work"

# Record evidence from a fresh, non-forked analysis transcript.
python "$Skill\scripts\record_isolation.py" `
  "C:\Music\Song.grammar-work\source-projection.json" `
  --method codex-exec `
  --child-transcript-id "<analysis-id>" `
  --parent-transcript-id "<parent-id>" `
  --output "C:\Music\Song.grammar-work\isolation-proof.json"

# The isolated analyzer writes semantic-draft.json. Only this writes Song.json.
python "$Skill\scripts\finalize.py" "C:\Music\Song.lrc" `
  --projection "C:\Music\Song.grammar-work\source-projection.json" `
  --workdir "C:\Music\Song.grammar-work"

python "$Skill\scripts\validate_all.py" "C:\Music\Song.lrc" `
  "C:\Music\Song.json" `
  --projection "C:\Music\Song.grammar-work\source-projection.json" `
  --workdir "C:\Music\Song.grammar-work"
```

Only local `.lrc` input is accepted. The generated file is always
`dirname(lrc) / basename(lrc, ".lrc") + ".json"`. Existing differing output is
preserved unless the user explicitly passes `--replace`.

Translation and grammar analysis are distinct acceptance gates. The accepted
bilingual LRC is frozen by SHA-256 before analysis. If a child agent or other
separate-transcript harness is unavailable, formal generation must stop with
an explicit error rather than falling back to same-context inline output.

### Verification

Python 3.10+ standard library only:

```powershell
python skills\lyrics-grammar-sidecar\scripts\selftest.py
git diff --exit-code -- skills/lyrics-translator
```

The suite covers ja/ko/zh/en fixtures, cross-line blocks and phrases,
overlapping blocks, duplicate lines, same-timestamp bilingual rows, Unicode
code-point spans, stale projections, invalid references, HTML-looking text,
translation freeze tampering, schema fail-closed behavior, and deterministic
atomic finalization. `fixtures/golden-vectors.json` pins the App's frozen
lineId, fingerprint, and projection-hash formulas for repeated text,
same-timestamp bilingual input, and emoji/combining-character spans.
