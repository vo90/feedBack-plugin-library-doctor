# Library Doctor development guide

Library Doctor is an optional FeedBack plugin. It must remain installable as a
standalone plugin even if it is later bundled with FeedBack.

## Non-negotiable rules

- Validation is read-only. Never modify, move, extract over, or delete files in
  the user's song library.
- A broken package must produce a finding, never abort the library batch.
- Library scans run in the background and support cancellation.
- Use `context["load_sibling"]` for sibling Python modules and
  `context["log"]` for backend logging.
- Register routes only under `/api/plugins/library_health/`.
- Do not import FeedBack's private Python modules. Use documented plugin
  context functions instead.
- Keep validation rules named, deterministic, bounded, and directly tested.
- Bump the `rules-N` component of `validator.VALIDATOR_VERSION` whenever a
  validation result can change. The scanner uses it to invalidate cached
  reports; schema revision changes are already part of the value.
- Treat missing optional content as coverage information, not an error.
- Never include library paths or song identities in FeedBack support bundles.
- Frontend code is vanilla JavaScript with no build step or remote assets.

## Verification

```bash
python -m pytest
python -m py_compile validator.py scanner.py routes.py
node --check screen.js
```

The official schemas are pinned under `schemas/`. Update them together and
record their exact upstream revision in `schemas/UPSTREAM.md`.
