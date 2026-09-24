# Lyric Translation — Shared Rules

Applies to all 12 direction pairs (ZH/JA/KO/EN). Use with the selected source-language and target-language references, plus any applicable pair exception.

---

## Quick Reference: 8 Core Rules

When invoked frequently, apply these 8 rules first:

1. **Source lines only**: Do not touch target-language lines, metadata lines, blank lines, or pure sound-effect lines (`♪`/`♫`).
2. **Read entire file first**: The first tool call must be a single `read_file` of the full lyrics file. Assess the song's subject, emotional tone, and narrative POV (first person / third person / duet / multi-voice) before writing anything. **Never interleave reads and writes.**  
   For duets/multi-voice: build a speaker map before translating — identical pronoun forms used by different voices must not be translated the same way.
3. **Pre-read context**: Before translating each line, review 3–5 lines before and after to catch long-sentence splits, chained grammar, and code-mixed passages.
4. **Core before modifier**: Translate the action/conclusion/emotional core first, then decide on modifiers and register.
5. **Singability over literalism**: Translations must be clear, natural, and singable. Single-line length must stay within the cap defined in the target-language file; split at natural breath points rather than cramming onto one line.
6. **Translation priority order** — When rules conflict, apply in this order:
   1. **Semantic integrity** (highest) — block-level meaning must be preserved
   2. **Timeline alignment** — one output line per source line
   3. **Single-line readability** (lowest) — desirable but subordinate to #1 and #2

   → Do **not** force a non-closing clause into a complete standalone sentence to satisfy single-line readability. Front-loading the next line's conclusion is also prohibited.
7. **Consistent key terms**: Use the same translation for choruses, hooks, core imagery, and the song title throughout the entire file. Reuse identical wording for repeated lines.
8. **Final check before output**: Confirm no target-language lines were modified, no metadata was touched, no third line was stacked onto a source line, no plot points were added, and all line lengths are within cap.

---

## Absolute Constraints (Red Lines)

| Constraint | Rule |
|:---|:---|
| **Format Lock** | The agent writes **plain text only** — never insert timestamp tokens into translation lines. LRC structure and timestamps are managed entirely by `lrc_merge.py`. (Plain Mode: same rule — no timestamps exist.) |
| **Metadata Immunity** | Lines beginning with `[ti:` `[ar:` `[al:` `[by:` `[offset:` and embedded production credits (`词：`/`曲：`/`編曲：`/`作詞：`/`Lyrics:`/`Music:` etc.) must never be translated or modified. |
| **No Re-translation** | Do not re-translate or modify lines already in the target language. |
| **One Translation Per Source Line** | Each source line gets exactly one same-timestamp translation line inserted below it. If a translation already exists, overwrite/correct it — never stack a third line. |
| **Filler Line Passthrough** | Pure vocalization filler lines (`Oh oh oh`/`La la la`/`오오오`/`啦啦啦`/`あーあー` etc.) pass through unchanged — no translation inserted. |
| **No Plot Injection** | Paraphrase may expand information already in the source. It must never add plot points, characters, motivations, or identities not present in the original. |
| **Completeness** | Every repeated lyric line must be translated. Do not skip or merge lines. |
| **Line Length Cap** | Single translated lines must not exceed the cap specified in the target-language file. Split at natural breath points when over the limit. |
| **Skip Empty/SFX Lines** | Blank timestamp lines (empty or whitespace content) and pure sound-effect lines (`[01:23.45]♪`) pass through unchanged — no translation inserted. |

---

## Execution Workflow

### Step 0 — Tool Discipline (Mandatory)

**Read before write, always.**

- **LRC Mode**: the working file is the `.tmp.txt` produced by `lrc_strip.py` — call `read_file` on **that file**, not the original `.lrc`.
- **Plain Mode**: call `read_file` on the `.txt` / `.md` file directly.
- Complete all analysis in memory, then write edits in a single pass using `replace_string_in_file` or `edit_file`.
- Do **not** alternate between reads and writes — this causes line-offset drift and translation stacking.
- For files over 200 lines: two `read_file` calls are acceptable, but merge the full analysis in memory before writing.

### Step 1 — Survey and Plan

- [ ] Scan the full file. Label each line as: source lyric / target-language line / metadata / special (blank / SFX / filler / production credit).
- [ ] Determine song subject, emotional tone, and narrative POV (first person, third person, duet, multi-voice).
- [ ] For duets/multi-voice: build a speaker map (who speaks in each section, what pronouns refer to whom).
- [ ] Lock in chorus, hook, and core-imagery translations to use consistently throughout.
- [ ] Flag long-sentence splits, chained grammar, and code-mixed passages. For lines with existing translations, decide: keep, overwrite, or skip (never stack).

### Step 2 — Key Phrase Resolution (In-Memory)

While surveying, collect every phrase whose meaning is not transparent from its characters alone. Resolve each to its true contextual meaning and lock a rendering BEFORE any translation is drafted. This table lives in memory only — never write it to a file, and never interleave extra reads/writes to build it.

**Deep-dive triggers (any phrase matching one of these must enter the table):**

| Trigger | What to check | Example |
|:---|:---|:---|
| Idiom / proverb, truncated or altered | Identify the full underlying proverb; render its intent in this context | 酸いも甘いも… ← 酸いも甘いも噛み分ける |
| Katakana loanword (or other script borrowing) | Render the original foreign word's semantics, never the sound or glyph | ランデブー → a date / tryst |
| Pun / homophone wordplay | If untranslatable, fuse both senses into one rendering | 愛(あい) / 相(あい) overlapping |
| Coined word / title wordplay | Decompose into parts; keep the wordplay visible in the target | 心予報 = heart + weather forecast |
| False-friend kanji (see pair-exceptions) | Verify meaning in the source language, never by glyph shape | 余所見, 心遊ばせ |
| Connotation-sensitive word | Check the candidate rendering's emotional color against the source | 浮つく = giddy/sweet, NOT restless |
| Ambiguous grammatical subject | Resolve what governs what before choosing an imperative or descriptive reading | 心遊ばせ — the heart plays, not the listener |

**In-memory table format (held alongside the block map until the final check):**

| Phrase | Resolved meaning | Locked rendering |
|:---|:---|:---|
| 酸いも甘いもわからないの | truncation of the kotowaza "to know bitter from sweet" → too smitten to tell love's flavors | 连酸甜都分不清 |
| 浮つく | giddy, sweetly fluttery (positive) | 雀跃的 |

Rules:

- Locked renderings are binding for Step 4 (draft) and re-verified in Steps 5 and 6.
- Never invent a matching second half to "balance" a resolved phrase.
- When a phrase cannot be resolved with confidence, prefer its idiomatic meaning over a character-by-character reading, and prefer under-specification over invention.
- Ambiguous subjects that need block context may be back-filled after Step 3 (semantic chunking) completes — update the table before drafting.

### Step 3 — Semantic Chunking (Mandatory)

> **This is a hard gate in the pipeline. Step 4 (translation) must not begin until semantic block construction is complete and the Step 2 key-phrase table has no unresolved entries.**

**Pipeline order:**

```
Parse LRC → Build semantic blocks → Block-level comprehension → Map output back to timeline (one line per source line)
```

**Mandatory block construction (must not be skipped):**

Before writing any translation, construct the internal block map in memory:

```
Block 1: [lines A, B]   — chained (te-form / enjambment / modifier chain)
Block 2: [line C]       — self-contained
Block 3: [lines D, E]   — chained (incomplete predicate / verb-ending stack)
```

→ Every source line must be assigned to exactly one block.
→ **Block construction incomplete → do not proceed to Step 4.**

**Procedure:**

1. Detect source language (JA / EN / ZH / KO) and load block-detection signals from `reference/source/{source}.md`.
2. Scan each source line for incomplete-sentence signals (see table below).
3. Group consecutive semantically connected lines into a **semantic block**. A block may be 1 line (self-contained) or multiple lines (chained/dependent).
4. **Translate at block level**: Derive meaning, pronouns, and register from the full block, then output one translation per source line.
   - Output format is unchanged: each source line gets exactly one same-timestamp translation line.
   - The *meaning* used to write that translation comes from the whole block — not the isolated line.
5. **Allow non-closing expressions**: A non-closing source line may produce a non-closing translation — do **not** force it into a standalone complete sentence.

**Universal incomplete-sentence signals:**

| Signal type | Description |
|:---|:---|
| No predicate closure | Line ends without a terminating verb or complete predicate |
| Modifier without head | Line is a modifier whose head noun/verb is on the next line |
| Connective dependency | Line begins with a connective that requires the previous line |
| Unresolved reference | Pronoun or demonstrative on this line resolves only on the next line |

---

### Semantic Integrity Guard

If **any** of the following is detected, per-line independent translation is **prohibited**:

| Trigger | Condition |
|:---|:---|
| Consecutive incomplete lines | Two or more consecutive lines with no terminal predicate |
| Consecutive modifier chains | Two or more consecutive lines ending with te-form / `〜ように` / `〜ながら` / enjambment / verb-ending stack |
| Cross-line semantic dependency | Current line's meaning requires the next line to resolve |

→ ❌ **Independent per-line translation is forbidden**
→ ✅ **Merge into one semantic block, then translate**

Violating this guard is a critical translation error.

**Wrong (violates Semantic Integrity Guard):**

```lrc
[00:01]沈むように       →  像沉入水中一般
[00:02]溶けてゆくように  →  像要融化消失那样
```

*Both lines forced into standalone similes — redundant, stilted, and a semantic integrity violation.*

**Correct (block-level comprehension, then per-line output):**

```lrc
[00:01]沈むように       →  如同在沉沦
[00:02]溶けてゆくように  →  又如同在消融
```

> Language-specific block-detection signals (JA te-form chains, EN enjambment, ZH poetic cuts, KO verb-ending stacks) are defined in `reference/source/{source}.md`.

### Step 4 — Translate (First Draft)

- [ ] Process each source line: insert one same-timestamp translation line directly below.
- [ ] Default to short and direct; avoid stacked modifiers and added plot points.
- [ ] Split lines at natural breath points; keep each line self-contained. Do not front-load the next line's conclusion.
- [ ] Code-mixed lines: preserve the source line as-is; translate the overall meaning naturally in the target line.
- [ ] Filler vocalization lines / pure `♪` lines → pass through unchanged.

**Translation behavior constraints:**

- ❌ Avoid over-explanation, semantic completion, and literary expansion beyond what the source contains.
- ❌ Do not add cause-and-effect structure where the source only implies it.
- ❌ Do not transform a non-closing expression into a conclusion sentence.
- ✅ Prioritize: direct expression, action-driven phrasing, singability.

### Step 5 — Refine (Critique Pass)

Re-read the draft as a critic, not as its author. For each semantic block, run all three diagnostic dimensions before accepting any line:

| Dimension | Check for | Failure example (JA → ZH) |
|:---|:---|:---|
| **Accuracy** | Invented additions; dropped meaning; wrong subject; idiom resolved literally | きっと君の前では迷子 → 迷路的小孩 ("小孩/child" invented; 迷子 here means lost, with no noun) |
| **Native voice** | Stiff or bookish register; translationese — apply the checklist in `reference/target/{target}.md` | 冷静沈着では どう頑張ったって無い → 无论怎样努力也无济于事 (bookish) — natural: 冷静沉着什么的 无论怎么努力都做不到 |
| **Connotation** | Emotional color of the rendering must match the source | 浮つく → 浮躁 (negative restlessness; source is sweet giddiness) → 雀跃 |

- [ ] Every locked rendering from the Step 2 table was used, unchanged.
- [ ] Chorus / hook / core-imagery translations unified across the whole file.
- [ ] Verify narrative POV and pronoun consistency throughout.
- [ ] Verify culture-specific terms (proper nouns, cultural references) are translated by meaning, not transliterated, where appropriate.
- [ ] **Over-correction guard**: revise only lines with a diagnosed defect. Do not flatten vivid, correct lines into literalness out of caution.

### Step 6 — Quality Check

- [ ] Source and translation lines strictly alternate.
- [ ] No missing lines, no modified metadata, no modified target-language lines, no stacked third lines.
- [ ] No timestamp tokens in any translation line.
- [ ] All Step 2 locked key-phrase renderings appear consistently (title, hooks, choruses).
- [ ] **Spot-check**: Read one line aloud from each of intro / verse / chorus. Confirm: meaning is clear, singable in one breath, contains no plot points not in the source.

### Step 7 — Post-Processing (LRC Mode only)

After the quality check passes:

1. Run `lrc_merge.py` to inject translations back into the original `.lrc`:
   ```
   python <skill-root>/scripts/lrc_merge.py <original.lrc> <input.tmp.txt>
   ```
2. Confirm the script reports success and the temp file is deleted.
3. Spot-check the output `.lrc`: open it and verify a few source/translation pairs have correct matching timestamps and are properly interleaved.

---

## Paraphrase vs. Over-Interpretation

Paraphrase is natural expansion of **information already in the source**. Over-interpretation **adds new plot, identity, scene, or motivation** not present in the original.

| Judgment | Example |
|:---|:---|
| ✓ **Acceptable paraphrase** — extends implied emotion naturally | `want to disappear` → `想消失不见` |
| ✗ **Over-interpretation** — adds specifics not in the source | `想消失在失恋的痛苦里` ("heartbreak" is invented) |
| ✓ **Acceptable** — reorders into natural target-language expression | Idiom or colloquial naturalization |
| ✗ **Prohibited** — expands poetic ellipsis into prose annotation | `running in the rain` → `不顾一切地向前冲，忘记所有伤痛` (invented motivation) |
| ✗ **Prohibited** — invents a second half to force a parallel couplet | すいも甘いもわからないの → `尝不出酸甜 摸不清好坏` ("好坏/good vs bad" is invented; the source idiom is only about sour and sweet) |
| ✗ **Prohibited** — adds a head noun the source does not contain | きっと君の前では迷子 → `迷路的小孩` ("小孩" invented; 迷子 needs no noun) |

Use `reference/source/{source}.md`, `reference/target/{target}.md`, and applicable entries in `reference/pair-exceptions.md` for concrete language guidance.

---

## Special Line Handling

| Line Type | Detection Criterion | Action |
|:---:|:---|:---:|
| Blank timestamp line | `[01:23.45]` with empty or whitespace content | Pass through, no translation |
| Pure SFX / interlude marker | `[01:23.45]♪` or `[01:23.45]♫` | Pass through, no translation |
| LRC metadata tag | Starts with `[ti:` `[ar:` `[al:` `[by:` `[offset:` | Never translate or modify |
| Embedded production credit | Contains `词：` `曲：` `編曲：` `作詞：` `作曲：` `Lyrics:` `Music:` etc. | Never translate or modify |
| Already-target-language line | Entire line is in target language | Pass through, no translation |
| Already-English line (non-EN source) | Entire line is English (e.g. `Yeah yeah`, `Oh baby`) | Pass through, no translation |
| Filler vocalization | `오오오` / `あーあー` / `Oh oh oh` / `La la la` / `啦啦啦` etc. | Pass through, no translation |
| Code-mixed line | Source language mixed with other languages within the line | Preserve source line; translate overall meaning naturally |
| Ellipsis pause line | `…` denoting trailing-off or silence | Preserve `…` in translation, or carry the pause feeling into register |

---

## Format Reference

### LRC Mode — what the agent edits (`.tmp.txt` working file)

**Input `.tmp.txt`** (after `lrc_strip.py`):
```
Song Title - Artist
词：Lyricist
Source lyric line one
Source lyric line two
Existing target-language line
♪

```

**Correct output `.tmp.txt`** (after agent translation):
```
Song Title - Artist
词：Lyricist
Source lyric line one
Translated line one
Source lyric line two
Translated line two
Existing target-language line
♪

```

**Final `.lrc`** (after `lrc_merge.py`):
```lrc
[ti:Song Title]
[ar:Artist]
[00:15.19]Source lyric line one
[00:15.19]Translated line one
[00:18.50]Source lyric line two
[00:18.50]Translated line two
[01:22.40]Existing target-language line
[00:20.00]♪
[00:25.00]
```

### Plain Mode — what the agent edits (`.txt` / `.md` directly)

**Input:**
```
Song Title - Artist
词：Lyricist
Source lyric line one
Source lyric line two
♪
```

**Correct output:**
```
Song Title - Artist
词：Lyricist
Source lyric line one
Translated line one
Source lyric line two
Translated line two
♪
```

> `♪` lines, blank lines, already-target-language lines, and metadata lines pass through unchanged — no translation inserted.
