---
name: lyrics-translator
description: Translate song lyrics across all global languages (with dedicated profiles for Chinese/ZH, Japanese/JA, Korean/KO, English/EN, French/FR, Spanish/ES, German/DE, Italian/IT, Portuguese/PT, Russian/RU, Thai/TH, Vietnamese/VI, Cantonese/YUE, and extensible generic fallback for all others) in .lrc, .txt, or .md files. Use for bilingual interleaved lyrics, lyric subtitles, LRC translation, C-pop, J-pop, K-pop, Western pop, Latin pop, Chanson, and global musical songs. Preserves the source line and inserts exactly one target-language line below it.
version: 1.0.0
---

# Lyric Translator — Bilingual Interleaved Lyrics

Translate across global languages with dedicated profiles and an extensible architecture. Keep instructions in English; do not translate the core prompt or duplicate rules by direction.

## Translation Mode

Detect which mode the user wants. Default to **reading mode** unless the user says "翻唱", "cover version", "singable translation", "翻配", or explicitly requests rhyme/rhythm matching.

| Mode | Priority | When to use |
|:---|:---|:---|
| **Reading** (default) | Semantic integrity > naturalness > singability | Bilingual subtitles, lyric booklets, understanding foreign songs |
| **Cover/Singing** | Singability + rhythm + rhyme > literalness | Creating a performable version in the target language |

In **reading mode**, follow the standard workflow below. In **cover mode**, additionally load the selected target profile's rhythm rules as hard constraints and actively pursue natural rhyme patterns (near-rhyme is acceptable; forced rhyme that distorts meaning is not).

## Workflow

1. Detect file mode. For `.lrc`, run this skill's `scripts/lrc_strip.py` to produce a same-directory `.tmp.txt` working file. For `.txt` and `.md`, work on the original file.
2. Detect **primary** source and target languages. Use the user's explicit direction first, then filename hints, then lyrics inspection:
   - **Kana** (平假名/片假名) → `ja`
   - **Hangul** (한글) → `ko`
   - **Cyrillic** (Русский) → `ru`
   - **Thai script** (ภาษาไทย) → `th`
   - **Vietnamese diacritics** (đ, ơ, ư, ả, ã, ạ, ắ...) → `vi`
   - **CJK ideographs without kana/Hangul**:
     - Contains vernacular Cantonese particles (嘅, 咗, 仲, 唔, 咁, 哋, 乜, 點) or tagged in title/album → `yue`
     - Otherwise → `zh`
   - **Latin script**:
     - German markers (ä, ö, ü, ß) → `de`
     - French markers (é, è, ê, ç, œ, à, ù) → `fr`
     - Spanish markers (ñ, ¿, ¡, á, é, í, ó, ú) → `es`
     - Portuguese markers (ã, õ, ç, ê) → `pt`
     - Italian vocabulary and characteristic vocalic endings → `it`
     - English vocabulary (the, you, I, my, we, and) → `en`
     - Other languages → identify ISO 639-1 code (e.g. `ar`, `hi`, `el`, `nl`, `sv`, `tr`, etc.).
   - Ask only if the target or source language remains ambiguous.
   - **Multi-language blocks**: Some songs contain blocks of lyrics in different languages (e.g., several lines in Japanese, then several lines in English, then back to Japanese). Detection of secondary source languages happens during Step 1 of `reference/common.md`.
3. Handle existing translations:
   - **No existing translations**: proceed normally.
   - **Existing translations in the same target language**: ask the user whether to keep, overwrite, or skip each section. If the user says "重新翻译" or "redo", overwrite all.
   - **Existing translations in a different language**: treat them as non-source lines — pass through unchanged. Insert the new target-language translation below the source line only.
4. Before editing, load reference files dynamically:
   - `reference/common.md`
   - **Source profile**: `reference/source/{source}.md` if it exists; otherwise fall back to `reference/source/generic.md`.
   - **Target profile**: `reference/target/{target}.md` if it exists; otherwise fall back to `reference/target/generic.md`.
   - **Pair exceptions**: `reference/pair-exceptions.md` if an entry matching `{source}→{target}` or their language families exists.
   - If Step 1 of common.md detected secondary source languages, load each `reference/source/{secondary}.md` (or `generic.md`) at that point.
5. Follow the numbered workflow in `reference/common.md`. Read the complete working file before writing; make one editing pass; run this skill's `scripts/lrc_merge.py` only after quality checks pass.

## Extensible Language Support (We are the World)

This skill utilizes an open, modular language architecture:
- **Dedicated Profiles**: High-frequency music languages have hand-crafted profiles in `reference/source/` and `reference/target/`:
  - East Asian: `zh` (Mandarin), `ja` (Japanese), `ko` (Korean), `yue` (Cantonese)
  - Southeast Asian: `th` (Thai), `vi` (Vietnamese)
  - European / Romance: `en` (English), `fr` (French), `es` (Spanish), `it` (Italian), `pt` (Portuguese)
  - Germanic & Slavic: `de` (German), `ru` (Russian)
- **Universal Fallback**: Any other language in the world immediately functions through `reference/source/generic.md` and `reference/target/generic.md`.
- **Zero-Code Expansion**: To add specialized deep-tuning for any new language (e.g. Arabic `ar`, Swedish `sv`, Hindi `hi`), simply create `reference/source/{lang}.md` and `reference/target/{lang}.md` following the existing format. The skill will discover and apply them automatically without changing any code or scripts.

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
