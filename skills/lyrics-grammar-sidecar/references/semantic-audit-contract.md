# Independent Semantic Audit Contract v1

The semantic-audit child is a fresh non-forked transcript. It must differ from both the parent and the grammar-analysis child. Its only authored artifact is `<workdir>/semantic-audit.json`.

## Inputs

The auditor reads:

- the original local `.lrc`;
- the frozen `<workdir>/translated.lrc`;
- `<workdir>/analysis-candidate.json`;
- `<workdir>/analysis-candidate.sha256`;
- `<workdir>/semantic-draft.json` as read-only supporting context;
- [draft-contract.md](draft-contract.md) for field semantics.

The candidate is the canonical reviewed artifact. The draft is supporting context only and must not be used to override changed candidate content. The candidate must remain the exact canonical UTF-8 JSON bytes emitted by `prepare.py`; duplicate keys, non-finite numbers, and unpaired surrogates are invalid.

The auditor must not edit the draft, candidate, hash, translation, projection, or final sibling JSON.

## What to Audit

For every candidate block and meaning unit:

1. Check that effective block meaning is a faithful synthesis of the frozen translation for the block's source spans.
2. Reject unsupported additions, including new motives, causes, scenes, relationships, intensity words, editorial commentary, or meta-narrative.
3. Check changed and newly analyzed tails. A tail not covered by either a meaning part or required phrase/note relation is a coverage gap.
4. Check that canonical shared fields are not semantically drifted between occurrences. Canonical data must be reused unless an occurrence has an explicit, reasoned override.
5. Check that every note, relation, uncertainty, and override has source evidence in the original LRC and accepted translation.
6. Check that learner-facing prose uses the requested language.

Warnings may be recorded without failing. Any error finding makes the audit fail and blocks finalization.

## Report

```json
{
  "schema": "sakurauta.lyrics-grammar-semantic-audit",
  "auditVersion": 1,
  "candidateSha256": "sha256:...",
  "learnerLanguage": "zh-CN",
  "status": "pass",
  "reviewedBlockIds": ["b_chorus1", "b_chorus2", "b_chorus3"],
  "reviewedMeaningUnitIds": ["mu_chorus_shared", "mu_tail_extra"],
  "findings": []
}
```

`reviewedBlockIds` and `reviewedMeaningUnitIds` must exactly match the IDs in the candidate, without omissions or duplicates. They can be extracted directly from the candidate: `reviewedBlockIds = [b["blockId"] for b in candidate["blocks"]]` and `reviewedMeaningUnitIds = [m["meaningUnitId"] for m in candidate["meaningUnits"]]`.

Finding shape:

```json
{
  "code": "unsupported-addition",
  "severity": "error",
  "path": "$.meaningUnits[3].text",
  "message": "候选新增了冻结译文没有表达的“完全释放”。"
}
```

Stable codes:

- `unsupported-addition`
- `translation-mismatch`
- `shared-drift`
- `coverage-gap`
- `missing-source-evidence`
- `other`

`status: "fail"` requires at least one `error` finding. `status: "pass"` requires no `error` findings.

## Validation and Identity

Before returning, the audit child should self-verify the report:

```powershell
python -X utf8 scripts\validate_audit.py `
  "<workdir>\analysis-candidate.json" `
  --candidate-hash "<workdir>\analysis-candidate.sha256" `
  --learner-language "<learner language>" `
  "<workdir>\semantic-audit.json"
```

On Windows, always invoke Python with `-X utf8` to avoid OEM code page corruption with Japanese and Chinese text.

Then record the audit child's actual transcript identity:

```powershell
python scripts\record_isolation.py `
  "<workdir>\source-projection.json" `
  --stage semantic-audit `
  --artifact "<workdir>\semantic-audit.json" `
  --method native-subagent `
  --child-transcript-id "<actual-audit-child-id>" `
  --parent-transcript-id "<actual-parent-id>" `
  --output "<workdir>\isolation-semantic-audit.json" `
  --replace
```

Do not invent transcript IDs. The grammar proof and audit proof must contain different `childTranscriptId` values. Each proof binds the actual artifact SHA-256. A same-child audit, v1 proof, stale projection hash, changed report, or failed audit must prevent finalization.
