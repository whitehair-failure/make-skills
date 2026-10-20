---
name: lyrics-translator
description: Translate song lyrics between Chinese (ZH), Japanese (JA), Korean (KO), and English (EN) in .lrc, .txt, or .md files. Use for bilingual interleaved lyrics, lyric subtitles, LRC translation, and J-pop, K-pop, or C-pop lyric translation. Also use when the user asks to translate a song, convert lyrics to another language, make bilingual subtitles, or create a singable/cover version of a foreign song. Preserves the source line and inserts exactly one target-language line below it.
---

# Lyric Translator — Bilingual Interleaved Lyrics

Translate between all 12 directions across ZH, JA, KO, and EN. Keep instructions in English; do not translate the core prompt or duplicate rules by direction.

## Translation Mode

Detect which mode the user wants. Default to **reading mode** unless the user says "翻唱", "cover version", "singable translation", "翻配", or explicitly requests rhyme/rhythm matching.

| Mode | Priority | When to use |
|:---|:---|:---|
| **Reading** (default) | Semantic integrity > naturalness > singability | Bilingual subtitles, lyric booklets, understanding foreign songs |
| **Cover/Singing** | Singability + rhythm + rhyme > literalness | Creating a performable version in the target language |

In **reading mode**, follow the standard workflow below. In **cover mode**, additionally load `reference/target/{target}.md` rhythm rules as hard constraints and actively pursue natural rhyme patterns (near-rhyme is acceptable; forced rhyme that distorts meaning is not).

## Workflow

1. Detect file mode. For `.lrc`, run this skill's `scripts/lrc_strip.py` to produce a same-directory `.tmp.txt` working file. For `.txt` and `.md`, work on the original file.
2. Detect **primary** source and target languages. Use the user's explicit direction first, then filename hints, then the first five lyric lines: kana → JA; Hangul → KO; CJK without kana/Hangul → ZH; mostly Latin script → EN. Ask only if the target language remains unknown.
   - **Multi-language blocks**: Some songs contain blocks of lyrics in different languages (e.g., several lines in Japanese, then several lines in English, then back to Japanese — distinct from single-line code-mixing). Detection of secondary source languages happens later during Step 1 of `reference/common.md`; see "Multi-Source-Language Detection" there.
3. Handle existing translations:
   - **No existing translations**: proceed normally.
   - **Existing translations in the same target language**: ask the user whether to keep, overwrite, or skip each section. If the user says "重新翻译" or "redo", overwrite all.
   - **Existing translations in a different language**: treat them as non-source lines — pass through unchanged. Insert the new target-language translation below the source line only.
4. Before editing, load all of these files:
   - `reference/common.md`
   - `reference/source/{primary_source}.md`
   - `reference/target/{target}.md`
   - `reference/pair-exceptions.md` only when it has an entry for `{source}→{target}`.
   - If Step 1 of common.md detected secondary source languages, also load each `reference/source/{secondary}.md` at that point (not upfront).
5. Follow the numbered workflow in `reference/common.md`. Read the complete working file before writing; make one editing pass; run this skill's `scripts/lrc_merge.py` only after quality checks pass.

## Rule Selection

Use source references to understand cross-line grammar, ellipsis, and source-specific meaning. Use target references to write natural, concise, singable output. Consult pair exceptions only for genuine bilateral pitfalls; they supplement rather than replace the other two files.

Never load obsolete direction files or invent pair-specific rules that are not required by the lyric at hand.

## Post-Translation

After completing the translation and delivering the file, offer the user a follow-up option:

> Translation complete. To revise specific lines, reply with the line numbers or quote the lines you'd like changed. To polish the entire translation, reply "继续润色" or "refine".

For **line-level revision**: re-read the surrounding 3–5 line context, apply the same source/target/pair rules, and replace only the specified lines.
For **full polish**: re-run Steps 5–6 of common.md (Refine + Quality Check) on the existing output.

## Commands

```powershell
python <skill-root>/scripts/lrc_strip.py <input.lrc> <input.tmp.txt>
python <skill-root>/scripts/lrc_merge.py <input.lrc> <input.tmp.txt>
```
