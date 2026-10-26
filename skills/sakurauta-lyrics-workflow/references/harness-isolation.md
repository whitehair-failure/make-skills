# Harness Isolation Contract

## Facts

- Loading a Skill changes instructions in the current transcript.
- A separate sub-agent can share the same filesystem; it is still a separate
  transcript.
- A forked transcript is not independent for this workflow because it may
  contain the parent's translation or analysis reasoning.
- A Skill file has no built-in authority to spawn an agent. The Harness must
  expose that capability.

## Accepted Evidence

Use one of the following, with a real ID:

```json
{
  "method": "native-subagent",
  "childTranscriptId": "actual-child-id"
}
```

```json
{
  "method": "codex-exec",
  "childTranscriptId": "actual-fresh-session-id"
}
```

`documented-harness` is only acceptable when the Harness has a documented,
separate transcript mechanism with an observable child identity. It is not a
synonym for "the parent read two Skill files".

## Rejection

Reject `inline`, `forked`, empty IDs, parent==child IDs, and missing proofs.
Reject a proof that was produced before the corresponding child actually ran.
The proof is evidence, not a way to bypass the capability gate.
