# Provenance research notes

Research date: 2026-08-29. Detector `regime-v1`.
Sufficient-liquidity period starts 2013-03-01, making this the start of the review window.

Publication floor: 60 minutes, after corroboration or explicit review.
Duration selects candidates; it does not necessarily publish a public row.

OHLCV cannot distinguish no trades, omitted minutes, fills, or downtime.
Official announced windows often sit a few hours before the observed zero-run
on the same UTC date.

DST on the corrected UTC grid was checked across US and EU transitions. It
creates no missing UTC minutes and no hour-long zero-volume run from a
timestamp discontinuity.

## Corroborated (URL `reference` on the sidecar)

| Observed UTC | Evidence |
| --- | --- |
| 2014-02-02 09:01–11:12 | Official scheduled unavailability 09:00–12:00 GMT+1 |
| 2014-07-11 04:16–06:14 | Official scheduled unavailability 06:00–08:00 CET |
| 2015-01-05 09:13 – 2015-01-09 21:05 | Hot-wallet hack; CoinDesk + Ars Technica |
| 2019-07-25 09:17–10:29 | Official scheduled platform unavailability from 09:00 UTC |
| 2020-04-25 19:58 – 2020-04-26 00:45 | Official downtime report: matching engine 20:00–00:30 UTC |
| 2020-08-26 10:04–11:52 | Official scheduled unavailability from 10:00 UTC |
| 2021-04-14 08:02–09:39 | Official Bitstamp post, 08:00–10:00 UTC maintenance |
| 2021-10-20 08:02–09:04 | Official scheduled system-wide maintenance 08:00–09:00 UTC |
| 2022-01-05 08:02–10:27 | Official scheduled unavailability from 08:00 UTC |
| 2022-05-11 08:04–09:14 | Official scheduled unavailability from 10:00 CET |
| 2022-07-27 08:02–09:09 | Official scheduled unavailability from 08:00 UTC |
| 2023-03-23 10:04–11:54 | Official scheduled unavailability from 10:00 UTC |

## Reviewed, no qualifying evidence (`reviewed_unconfirmed`)

Unexplained silence / possible data-quality, not proved outages.

- 2013-03-02 through 2013-10-27 (21 hour-plus runs): consistent with a young
  book; no contemporaneous outage reports.
- 2014-01-25 08:04–09:39 UTC (1.58h): no trading-outage report found.
- 2022-07-13 10:57–12:17 UTC (1.33h): 14 July post is support-system only.
- 2023-07-03 11:02–13:25 UTC (2.38h): US bank-holiday USD-rail pause, not a
  trading halt.

## Excluded remainders (not published)

Liquid-period runs that overlap a Statuspage window. The remainder stays in
the ledger as `excluded`.

- 2023-06-28 09:02 UTC (52m)
- 2024-04-07 08:01 UTC (67m)
- 2024-12-11 10:03 UTC (53m)
- 2025-03-23 08:43 UTC (84m)

## Official notices without a 60-minute zero-run

These are not written as `scheduled_maintenance` (Statuspage remains that
flag's source) and did not produce a published 60-minute unexplained zero-run.

- 2014-02-11: bitcoin withdrawal suspension (malleability)
- 2014-04-08: Heartbleed precaution
- 2016-07-21: eCheck/credit-card products only
- 2019-10-23, 2020-02-05, 2021-03-24, 2021-09-22, 2021-11-24 (59m, below
  floor), 2022-02-16, 2022-12-07: scheduled unavailability windows
