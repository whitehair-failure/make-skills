# Analysis Inputs

## Canonical Source

The projection is authoritative for:

- `lineId`;
- `sourceOrder`;
- `timeMs`;
- source text;
- fingerprint;
- same-timestamp ordinal.

The analysis must not invent alternate IDs or use database row IDs.

## Frozen Translation

The frozen `translated.lrc` is the accepted bilingual source for meaning.
Its hash and the projection hash are checked by the parent before dispatch and
by the finalizer before writing.

## Draft Contract

The canonical shape and validation rules live at
`skills/lyrics-grammar-sidecar/references/draft-contract.md` and
`skills/lyrics-grammar-sidecar/schema/v1/lyrics-grammar-analysis.schema.json`.
Do not copy them into the child Skill; read the existing files.
