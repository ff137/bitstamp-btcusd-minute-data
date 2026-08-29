# Provenance research notes

Research date: 2026-08-29. Detector `regime-v1`. Liquid-regime *review* floor:
2013-03-01. Publication floor: 60 minutes, after corroboration or explicit
human review. Duration selects candidates; it does not publish a public row.

OHLCV cannot distinguish no trades, omitted minutes, fills, or downtime.
Official announced windows often sit a few hours before the observed zero-run
on the same UTC date. Published bounds stay on the observed run.

2013-03-01 is the start of the review window, not a claim that the book was
already liquid. Hour-plus 2013 silences were reviewed as unexplained thin-book
silence.

See [DST_AUDIT.md](DST_AUDIT.md) for the one-time DST and missing-minute
assessment.

## Corroborated (URL `reference` on the sidecar)

| Candidate | Observed UTC | Evidence |
| --- | --- | --- |
| zv-1391331660-1391339520 | 2014-02-02 09:01–11:12 | Official scheduled unavailability 09:00–12:00 GMT+1 |
| zv-1405052160-1405059240 | 2014-07-11 04:16–06:14 | Official scheduled unavailability 06:00–08:00 CET |
| zv-1420449180-1420837500 | 2015-01-05 09:13 – 2015-01-09 21:05 | Hot-wallet hack; CoinDesk + Ars Technica independent reports of the service suspension |
| zv-1564046220-1564050540 | 2019-07-25 09:17–10:29 | Official scheduled platform unavailability from 09:00 UTC |
| zv-1587844680-1587861900 | 2020-04-25 19:58 – 2020-04-26 00:45 | Official downtime report: matching engine 20:00 UTC–00:30 UTC |
| zv-1598436240-1598442720 | 2020-08-26 10:04–11:52 | Official scheduled unavailability from 10:00 UTC, up to 2 hours |
| zv-1618387320-1618393140 | 2021-04-14 08:02–09:39 | Official Bitstamp post describing 08:00–10:00 UTC maintenance |
| zv-1634716920-1634720640 | 2021-10-20 08:02–09:04 | Official scheduled system-wide maintenance 08:00–09:00 UTC |
| zv-1641369720-1641378420 | 2022-01-05 08:02–10:27 | Official scheduled unavailability from 08:00 UTC, ~2 hours |
| zv-1652256240-1652260440 | 2022-05-11 08:04–09:14 | Official scheduled unavailability from 10:00 CET, ~1 hour |
| zv-1658908920-1658912940 | 2022-07-27 08:02–09:09 | Official scheduled unavailability from 08:00 UTC, ~1 hour |
| zv-1679565840-1679572440 | 2023-03-23 10:04–11:54 | Official scheduled unavailability from 10:00 UTC, ~1 hour |

## Reviewed, no qualifying evidence (`reviewed_unconfirmed`)

These stay in the sidecar as unexplained silence / possible data-quality, not
as proved outages. Each has a reviewer, decision date, and note.

- 2013 liquid-regime hour-plus runs (21 rows, 2013-03-02 through 2013-10-27):
  still consistent with a young book; no outage reports found. Shared note:
  `notes/2013-transition.md`.
- 2014-01-25 08:04–09:39 UTC (1.58h): no official or independent
  contemporaneous trading-outage report found.
- 2022-07-13 10:57–12:17 UTC (1.33h): 14 July 2022 Bitstamp post is
  support-system only, not trading.
- 2023-07-03 11:02–13:25 UTC (2.38h): 3 July 2023 post is a US bank-holiday
  USD-rail pause, not a trading halt.

## Excluded remainders (not published)

These liquid-regime runs overlap an official Statuspage window. The remainder
stays in the ledger as `excluded` and is not written as `suspected_outage`.

- 2023-06-28 09:02 UTC (52m remainder of scheduled maintenance)
- 2024-04-07 08:01 UTC (67m remainder of a Statuspage window)
- 2024-12-11 10:03 UTC (53m remainder)
- 2025-03-23 08:43 UTC (84m remainder)

## Event-led (no 60-minute zero-run in the sidecar)

These official Bitstamp notices describe market-relevant downtime but did not
produce a published 60-minute unexplained zero-run. They stay in this ledger
and are not written as `scheduled_maintenance` (Statuspage remains the source
for that flag).

- 2014-02-11: bitcoin withdrawal suspension (transaction malleability). Withdrawals, not necessarily BTC/USD prints.
- 2014-04-08: Heartbleed precaution, logins/withdrawals disabled.
- 2016-07-21: eCheck/credit-card products only.
- 2019-10-23: scheduled platform unavailability from 09:00 UTC.
- 2020-02-05: scheduled BTC/USD and BTC/EUR disruption from 09:00 UTC.
- 2021-03-24: scheduled matching halt ~1 hour from 10:00 UTC.
- 2021-09-22: scheduled unavailability ~1 hour from 10:00 UTC.
- 2021-11-24: scheduled unavailability ~1 hour from 09:00 UTC (zero-run 59 minutes, below publication floor).
- 2022-02-16: scheduled unavailability ~1.5 hours from 09:00 UTC.
- 2022-12-07: scheduled unavailability ~1 hour from 08:00 UTC.

## Queries

See [research.jsonl](research.jsonl). Negative results are recorded there.
Candidate IDs in that log were remapped onto the corrected UTC bounds.
