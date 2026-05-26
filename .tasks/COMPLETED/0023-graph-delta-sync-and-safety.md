# Graph Delta Sync And Safety

Status: COMPLETED

## Goal

Move Microsoft Graph selected-folder sync from first-page message listing toward durable per-folder delta sync.

## Completed

- Added per-folder Graph delta query startup using `delta_link` and `next_link` sync state.
- Added conservative sync limit capping and exposed the effective limit in CLI/API/web status.
- Tracked remote `@removed` Graph ids without deleting local archived messages.
- Kept Graph MIME fetches in the existing raw-message pipeline.
- Added unit coverage for first delta import, follow-up removed-message state, and export-from-Graph-import fidelity.

## Verification

- `PYTHONPATH=src python3 -m unittest discover -s tests`
- `npm run build` from `web/`
- Live CNB Archive sync established delta state with no remote mutations.
