---
name: lyrics-translator
description: Translate song lyrics between Chinese (ZH), Japanese (JA), Korean (KO), and English (EN) in .lrc, .txt, or .md files. Use for bilingual interleaved lyrics, lyric subtitles, LRC translation, and J-pop, K-pop, or C-pop lyric translation. Preserves the source line and inserts exactly one target-language line below it.
---

# Lyric Translator — Bilingual Interleaved Lyrics

Translate between all 12 directions across ZH, JA, KO, and EN. Keep instructions in English; do not translate the core prompt or duplicate rules by direction.

## Workflow

1. Detect file mode. For `.lrc`, run this skill’s `scripts/lrc_strip.py` to produce a same-directory `.tmp.txt` working file. For `.txt` and `.md`, work on the original file.
2. Detect source and target languages. Use the user’s explicit direction first, then filename hints, then the first five lyric lines: kana → JA; Hangul → KO; CJK without kana/Hangul → ZH; mostly Latin script → EN. Ask only if the target language remains unknown.
3. Before editing, load all of these files:
   - `reference/common.md`
   - `reference/source/{source}.md`
   - `reference/target/{target}.md`
   - `reference/pair-exceptions.md` only when it has an entry for `{source}→{target}`.
4. Follow the numbered workflow in `reference/common.md`. Read the complete working file before writing; make one editing pass; run this skill’s `scripts/lrc_merge.py` only after quality checks pass.

## Rule Selection

Use source references to understand cross-line grammar, ellipsis, and source-specific meaning. Use target references to write natural, concise, singable output. Consult pair exceptions only for genuine bilateral pitfalls; they supplement rather than replace the other two files.

Never load obsolete direction files or invent pair-specific rules that are not required by the lyric at hand.

## Commands

```powershell
python <skill-root>/scripts/lrc_strip.py <input.lrc> <input.tmp.txt>
python <skill-root>/scripts/lrc_merge.py <input.lrc> <input.tmp.txt>
```
