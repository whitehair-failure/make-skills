# make-skills

Reusable, user-invoked Skills maintained outside the SakuraUta application.

## SakuraUta Lyrics Workflow

The user-facing entry point is
`skills/sakurauta-lyrics-workflow/SKILL.md`.

It coordinates two fresh child contexts:

1. **Translation child** — reads the unchanged
   `skills/lyrics-translator/SKILL.md` directly and produces an accepted
   bilingual LRC. There is no translation wrapper Skill.
2. **Grammar child** — reads
   `skills/lyrics-grammar-sidecar/SKILL.md` and its draft contract, then writes
   only `semantic-draft.json`.

The parent freezes the accepted translation before grammar analysis, confirms
the real child transcript identity, and invokes the deterministic finalizer.
Only the finalizer writes the production sibling JSON.

### Discovery

- Main workflow: `skills/sakurauta-lyrics-workflow/SKILL.md`
- Existing translator: `skills/lyrics-translator/SKILL.md`
- Self-contained grammar Skill:
  `skills/lyrics-grammar-sidecar/SKILL.md`
- Grammar schema:
  `skills/lyrics-grammar-sidecar/schema/v1/lyrics-grammar-analysis.schema.json`
- Grammar draft contract:
  `skills/lyrics-grammar-sidecar/references/draft-contract.md`
- Fixtures: `skills/lyrics-grammar-sidecar/fixtures/`

All three Skill directories can be copied together to another machine. The
grammar Skill resolves its schema relative to its own `SKILL.md`; it does not
read a repository-level `schema/` directory.

### Invocation

Set the three directories once:

```powershell
$Skills = "C:\path\to\skills"
$Workflow = "$Skills\sakurauta-lyrics-workflow"
$Grammar = "$Skills\lyrics-grammar-sidecar"
$Work = "C:\Music\Song.grammar-work"
```

Create the source projection:

```powershell
python "$Grammar\scripts\project_source.py" "C:\Music\Song.lrc" `
  --song-id "<SakuraUta songId>"
```

Dispatch a fresh translation child with this instruction:

```text
Read and follow $Skills\lyrics-translator\SKILL.md.
Translate C:\Music\Song.lrc into <target language>.
Write the accepted bilingual LRC to <workdir>\accepted-translated.lrc.
Do not produce grammar JSON.
```

After the user accepts the translation, freeze it:

```powershell
python "$Grammar\scripts\verify_translation.py" `
  "$Work\source-projection.json" "$Work\accepted-translated.lrc" `
  --workdir $Work
```

Record the actual grammar child identity:

```powershell
python "$Grammar\scripts\record_isolation.py" `
  "$Work\source-projection.json" `
  --method native-subagent `
  --child-transcript-id "<actual-child-id>" `
  --parent-transcript-id "<actual-parent-id>" `
  --output "$Work\isolation-proof.json"
```

Dispatch a different fresh grammar child:

```text
Read and follow $Grammar\SKILL.md and $Grammar\references\draft-contract.md.
Read only the original LRC, source projection, and frozen translated.lrc.
Write <workdir>\semantic-draft.json.
Do not modify the translation or write <song>.json.
```

Finalize and verify:

```powershell
python "$Grammar\scripts\finalize.py" "C:\Music\Song.lrc" `
  --projection "$Work\source-projection.json" `
  --workdir $Work

python "$Grammar\scripts\validate_all.py" `
  "C:\Music\Song.lrc" "C:\Music\Song.json" `
  --projection "$Work\source-projection.json" `
  --workdir $Work
```

Only local `.lrc` input is accepted. The generated file is always
`dirname(lrc) / basename(lrc, ".lrc") + ".json"`. Existing differing output is
preserved unless the user explicitly passes `--replace`.

### Isolation Contract

- A Skill load is not a child context.
- Use a native non-forked sub-agent or an independent CLI session.
- Do not add a translation wrapper Skill; the translation child reads
  `lyrics-translator` directly.
- The grammar child reads the frozen translation and source projection but
  must not write the final JSON.
- If no separate context exists, stop with `isolation-unavailable`. Do not run
  translation or grammar inline as a fallback.

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
lineId, fingerprint, and projection-hash formulas.
