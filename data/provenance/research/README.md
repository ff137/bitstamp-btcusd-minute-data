# Provenance research artifacts

Local investigation record for unexplained zero-volume runs. Frozen at
`model.json`'s `as_of_timestamp`. Daily automation must not rewrite this
directory.

| File | Role |
| --- | --- |
| `model.json` | Frozen detector parameters, `as_of_timestamp`, and prefix hash |
| `candidates.csv` | Every ≥30-minute zero-run, with review status |
| `decisions.json` | Explicit review records applied onto regenerated candidates |
| `NOTES.md` | Corroboration decisions and event-led findings |
| `DST_AUDIT.md` | One-time DST and missing-minute assessment |
| `research.jsonl` | Query log, including negative results |
| `notes/` | Per-candidate evidence notes for published rows |

See [scripts/PROVENANCE_RESEARCH.md](../../../scripts/PROVENANCE_RESEARCH.md).
