# Library Doctor development guide

Library Doctor is an optional FeedBack plugin. It must remain installable as a
standalone plugin even if it is later bundled with FeedBack.

## Non-negotiable rules

- Validation is read-only. Never modify, move, extract over, or delete files in
  the user's song library. Spawned validation workers install the package write
  guard before accepting tasks; keep its denial test whenever worker imports or
  validator IO changes.
- A broken package must produce a finding, never abort the library batch.
- Library scans run in the background and support cancellation.
- Use `context["load_sibling"]` for sibling Python modules and
  `context["log"]` for backend logging.
- Register routes only under `/api/plugins/library_doctor/`.
- Do not import FeedBack's private Python modules. Use documented plugin
  context functions instead.
- Keep validation rules named, deterministic, bounded, and directly tested.
- Keep conditional automatic-repair eligibility in `repair_eligibility.py` so
  scan results and the authoritative repair planner use the same predicates.
- Bump the `rules-N` component of `validator.VALIDATOR_VERSION` whenever a
  validation result can change. The scanner uses it to invalidate cached
  reports; schema revision changes are already part of the value.
- Treat missing optional content as coverage information, not an error.
- Never include library paths or song identities in FeedBack support bundles.
- Keep `diagnostics.py` aggregate-only and bounded. Never add raw state files,
  database rows, package IDs, titles, artists, paths, or exception text to its
  support-bundle payload.
- Frontend code is source-served native JavaScript modules with no build step
  or remote assets. Keep `screen.js` as the thin module entry and preserve the
  downward dependency direction documented in `src/` tests.
- Song Tools may read FeedBack's documented `/api/library?provider=local`
  endpoint, but its repair eligibility and writes must remain inside Library
  Doctor's guarded backend. Do not couple Song Tools availability to scan cache.
- Preview repair must validate a complete candidate and use temporary recovery
  during the write. After a validated preview commit, remove that recovery copy
  automatically; chart/tab repairs retain their explicit Undo recovery.

## Verification

```bash
python -m pytest
python -m pip check
python -m pip_audit -r requirements.txt
python -m py_compile validator.py scanner.py library_doctor_scan_worker.py repair.py repair_eligibility.py preview_repair.py batch_repair.py migration.py privacy.py diagnostics.py api_contracts.py mutation_receipts.py routes.py tools/verify_host_contract.py
npm ci
npm run audit:dependencies
npm run check:frontend
npm run lint:frontend
npm run test:frontend
npm run test:browser:list
```

The official schemas are pinned under `schemas/`. Update them together and
record their exact upstream revision in `schemas/UPSTREAM.md`.
The declared minimum host version is not sufficient on its own because several
nightlies share that version text. Preserve `host-contract.json`, its exact
minimum capability commit, and the minimum/latest host-contract CI matrix.
