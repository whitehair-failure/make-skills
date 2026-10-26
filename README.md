# make-skills

Reusable, user-invoked Skills maintained outside the SakuraUta application.

## SakuraUta Lyrics Workflow

`skills/sakurauta-lyrics-workflow` is the **main entry point**. It coordinates
two child Skills:

1. `skills/lyrics-translation-handoff` delegates to the unchanged
   `skills/lyrics-translator` and freezes the accepted bilingual LRC.
2. `skills/lyrics-grammar-analysis` analyses the frozen lyrics in a fresh
   transcript and writes only a semantic draft.

The deterministic layer remains
`skills/lyrics-grammar-sidecar`. It owns the JSON Schema, projection, freeze
verification, reference/Unicode validation, isolation proof, and atomic final
write. The main Skill does not reimplement those rules.

### Discovery

- Main workflow: `skills/sakurauta-lyrics-workflow/SKILL.md`
- Translation child: `skills/lyrics-translation-handoff/SKILL.md`
- Grammar child: `skills/lyrics-grammar-analysis/SKILL.md`
- Deterministic generator: `skills/lyrics-grammar-sidecar/SKILL.md`
- Draft contract: `skills/lyrics-grammar-sidecar/references/draft-contract.md`
- Sidecar v1 schema: `schema/v1/lyrics-grammar-analysis.schema.json`
- Fixtures: `skills/lyrics-grammar-sidecar/fixtures/`

The three top-level Skills are separate directories so a Harness can discover
and delegate them independently. Reading a child `SKILL.md` does not create a
separate context.

### Invocation

Run from the `make-skills` repository root:

```powershell
$Main = "skills\sakurauta-lyrics-workflow"
$Work = "C:\Music\Song.grammar-work"

python "$Main\scripts\preflight.py" "C:\Music\Song.lrc" `
  --target-language zh `
  --song-id "<SakuraUta songId>" `
  --workdir $Work

# Dispatch this task to a fresh translation child.
# It reads skills\lyrics-translation-handoff\SKILL.md and uses
# skills\lyrics-translator as a black box.

python "$Main\scripts\check_phase.py" $Work --phase frozen

# Dispatch this task to a different fresh child.
# It reads skills\lyrics-grammar-analysis\SKILL.md and the existing
# draft contract, then writes semantic-draft.json and isolation-proof.json.

python "$Main\scripts\check_phase.py" $Work --phase drafted

# Only the deterministic finalizer writes the production JSON.
python "skills\lyrics-grammar-sidecar\scripts\finalize.py" `
  "C:\Music\Song.lrc" `
  --projection "$Work\source-projection.json" `
  --workdir $Work

python "$Main\scripts\check_phase.py" $Work `
  --phase finalized `
  --sidecar "C:\Music\Song.json"
```

Only local `.lrc` input is accepted. The generated file is always
`dirname(lrc) / basename(lrc, ".lrc") + ".json"`. Existing differing output is
preserved unless the user explicitly passes `--replace`.

### Isolation Contract

- A Skill load is not a child context.
- Use a native non-forked sub-agent or an independent CLI session.
- Pass each child only the phase Skill path and input/output contract.
- A shared workspace is allowed; the frozen translation hash catches writes.
- If no separate context exists, stop with `isolation-unavailable`. Do not run
  translation or grammar inline as a fallback.

### Verification

Python 3.10+ standard library only:

```powershell
python skills\sakurauta-lyrics-workflow\scripts\selftest.py
python skills\lyrics-grammar-sidecar\scripts\selftest.py
git diff --exit-code -- skills/lyrics-translator
```

The generator suite covers ja/ko/zh/en fixtures, cross-line blocks and phrases,
overlapping blocks, duplicate lines, same-timestamp bilingual rows, Unicode
code-point spans, stale projections, invalid references, HTML-looking text,
translation freeze tampering, schema fail-closed behavior, and deterministic
atomic finalization. The orchestration suite covers preflight rejection,
phase checks, frozen translation tampering, child Skill boundaries, and the
no-inline-fallback rule. `fixtures/golden-vectors.json` pins the App's frozen
lineId, fingerprint, and projection-hash formulas.
