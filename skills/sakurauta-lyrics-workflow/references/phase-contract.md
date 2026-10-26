# Phase Contract

## Translation Phase

The translation child receives the source LRC and target language. It invokes
the existing `lyrics-translator` workflow without modifying that Skill.

It must produce and freeze:

```text
accepted-translated.lrc
translation-manifest.json
translated.lrc
```

The manifest is the source of truth for the accepted translation. A phase
advance requires:

- a valid source projection;
- a manifest whose projection hash matches that projection;
- a `translated.lrc` whose SHA-256 matches the manifest;
- source text/timeline equality with the original LRC.

## Grammar Phase

The grammar child receives only the frozen inputs and draft contract. It
writes:

```text
semantic-draft.json
isolation-proof.json
```

It must not write `translated.lrc`, `translation-manifest.json`,
`source-projection.json`, or the sibling `<song>.json`.

## Finalization Phase

The deterministic finalizer validates the draft and writes the sibling JSON
atomically. It must not be replaced by an Agent reading the JSON and deciding
it looks valid.

## Re-running

- Translation failure: start a new translation child with a fresh work
  directory; do not overwrite a frozen manifest.
- Grammar failure: reuse the frozen translation and source projection, but
  start a new analysis child and replace only the draft/proof.
- Finalization failure: preserve the report, fix only the draft, and rerun the
  finalizer.
