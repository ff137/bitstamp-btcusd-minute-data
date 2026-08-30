## Publisher changes (first snapshot)

This is the first monthly full-history snapshot of Bitstamp BTC/USD 1-minute
candles.

- Daily updates are ordinary Git commits of the growing updates file. The
  publisher no longer force-pushes a rewritten daily history.
- Repository tooling uses uv instead of Poetry.
- The historical bulk file was rebuilt with correct UTC timestamps, fixing a
  daylight-saving offset in earlier published minutes.
- A sparse provenance sidecar records official Statuspage incidents and
  maintenance, going-forward gap fills, and reviewed zero-volume intervals.

Later monthly releases omit this section. The snapshot assets below are the
joined series through the completed UTC month, not a replacement of the daily
files in the repository.
