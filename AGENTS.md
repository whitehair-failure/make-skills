# Markdown Formatting Rules

These rules apply only to Markdown formatting and do not override each Skill's own instructions.

- Keep each natural-language paragraph on one physical source line. Do not hard-wrap prose at fixed 80/100/120-character widths.
- Preserve Markdown semantics and author intent. Do not change prose, links, resource paths, YAML frontmatter, heading levels, lists, tables, blockquotes, code fence languages, or code block contents while formatting.
- After changing any Markdown, run `npm run format:md`, then verify with `npm run check:md`.
- Use the repository's Prettier setup as the canonical formatter. Do not introduce a second Markdown formatter or alternate wrapping rules.
