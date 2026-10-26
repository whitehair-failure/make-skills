# Translation Handoff

The handoff is a deterministic pair:

- `translated.lrc` is the accepted bilingual source;
- `translation-manifest.json` contains the projection hash and the accepted
  file SHA-256.

The grammar child may read `translated.lrc` only after `check_phase.py` has
confirmed both hashes. A translation edit invalidates the phase and stops the
workflow; it is never silently repaired.
