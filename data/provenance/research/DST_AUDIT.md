# DST and missing-minute assessment

One-time research note. Not a CI check. Date: 2026-08-29.

This compares four artifacts:

- the pre-PR-15 published gzip (`5731839^`, 6,846,600 rows)
- the corrected published gzip from pinned Kaggle v706 (6,847,200 rows)
- [`data/original/source-manifest.json`](../../original/source-manifest.json)
- the bounded Bitstamp windows in `scripts/verify_source.py`

It does not add a DST rule to validation. The dataset already fails closed on
missing or duplicate UTC minutes.

## Three different things

These are not interchangeable:

| Concept | What it is | Where it lives |
| --- | --- | --- |
| Absent timestamp | No row for that Unix minute | A hole in the 60-second grid |
| Present zero-volume candle | A real row whose `volume` is 0 | OHLCV |
| `source_gap_filled` | A row the daily updater synthesized | Provenance sidecar only |

The current historical builder copies Kaggle v706 through 2025-01-07 00:00 UTC.
It does not invent minutes. The daily updater may still synthesize a flat
zero-volume candle when Bitstamp omits a minute; those going-forward fills are
the only `source_gap_filled` rows.

A present `volume == 0` candle can be genuine no-trade, an upstream omitted
minute that someone else already filled, or downtime. OHLCV cannot tell those
apart.

## Corrected grid

Joined historical gzip plus `data/updates/btcusd_bitstamp_1min_latest.csv`
through the research cutoff (2026-08-29 04:19 UTC):

- 7,710,019 consecutive UTC minutes
- 0 missing timestamps, 0 duplicates
- first timestamp 1325376060 (2012-01-01 00:01 UTC)
- 1,312,412 present zero-volume minutes
- 2,065 zero-volume runs of 60 minutes or longer (most of them thin-book 2012)

The published historical slice alone is a complete grid: 6,847,200 rows from
2012-01-01 00:01 UTC through 2025-01-07 00:00 UTC, SHA-256
`1be152060b39327b669cbed236eeb283191fadaf3862f76c1e974be54ceb1a20`, matching
the source manifest. Kaggle v706 itself is also a complete minute grid
(7,710,039 rows, 1325376060..1787978340).

There is therefore no absent UTC minute in the current publication, and no
historical `source_gap_filled` row. Remaining silence is present zero-volume.

## DST transitions

Every US (`America/New_York`) and EU (`Europe/Amsterdam`) spring/fall
transition from 2012 through the current updates file was checked in a
seven-hour window around the instant (3 hours before, 4 hours after):

- 58 overlapping windows on the joined grid, 52 on the historical slice
- each window has the exact expected UTC minute count
- 0 gaps, 0 duplicates inside any window

Two thin-era 2012 zero-volume runs happen to cover a DST instant
(US spring 2012-03-11 07:00 UTC and EU spring 2012-03-25 01:00 UTC). Those
runs are still consecutive present UTC rows with `volume == 0`. They are not
a skipped or repeated Unix hour.

Every liquid-era run of 60 or more zero-volume minutes was checked the same
way: duration matches the half-open bounds, and the minutes are consecutive
UTC timestamps. None of those runs is a spring-forward hole or a fall-back
duplicate.

## What the old gzip actually did

The pre-PR-15 file is also a complete 60-second grid, but it is a different
grid:

- 6,846,600 rows (600 fewer than today)
- first timestamp 1325412060 (2012-01-01 10:01 UTC)
- last timestamp still 1736208000 (2025-01-07 00:00 UTC)
- 0 missing timestamps inside that range

The missing 600 rows are the first ten UTC hours of 2012-01-01, which exist
only in the corrected file. That start-of-file truncation is not a DST hole.

Before 08:01 UTC on 13 September 2024, the old unix labels are not interval-open
UTC. They are the Kaggle candles relocated by the US offset then in force:

- +5 hours in US winter (`America/New_York` EST)
- +4 hours in US summer (`America/New_York` EDT)

About 99.89% of pre-cutover candles reappear in the old file at
`timestamp + 4h` or `timestamp + 5h` with identical OHLC. After 08:01 UTC on
13 September 2024 the labels match the corrected file.

Worked example, January 2015 halt:

| Unix | UTC | Corrected file | Pre-PR-15 file |
| --- | --- | --- | --- |
| 1420449180 | 2015-01-05 09:13 | `volume == 0` (halt starts) | still trading (`volume == 10.05`) |
| 1420467180 | 2015-01-05 14:13 | still `volume == 0` | `volume == 0` (old sidecar start) |

`old[14:13 UTC] == corrected[09:13 UTC]`. The old provenance bounds were five
hours late because they were read off that relabeled clock, not because a
DST transition deleted minutes.

The leftover mismatches under that +4h/+5h map (a few thousand rows) cluster
around US fall-back, where the offset is ambiguous for one local hour. They
are mapping artifacts, not extra holes in either grid.

Older Kaggle versions did have genuine absent timestamps. The community gap
file recorded in the manifest is an exact subset of v706 and is not merged or
filled when building the current historical gzip. Any earlier merge/fill
process is therefore not the cause of present zero-volume rows in this
publication.

Daily `update_data.py` can still synthesize minutes going forward. Those are
the only rows that should ever appear as `source_gap_filled`.

## Strongest supportable conclusion

The current artifact can prove all of the following:

1. DST does not create missing UTC rows on the published grid.
2. DST does not create an hour-long zero-volume run by skipping or repeating
   Unix timestamps.
3. The old four/five-hour clock error moved existing candles onto the wrong
   unix labels and dropped the first ten UTC hours of 2012. It did not punch
   DST holes in the grid.

It cannot prove why a present zero-volume value is zero. That still requires
external evidence, which is why `suspected_outage` is a reviewed or
corroborated annotation rather than a duration rule.
