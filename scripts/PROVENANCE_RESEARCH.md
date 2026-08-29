# Provenance research protocol

This document freezes how unexplained zero-volume runs are selected,
researched, and published. Duration and rarity select *candidates*. A public
`suspected_outage` row requires corroboration or explicit human review.

OHLCV cannot distinguish genuine no-trade minutes, upstream omission, a
synthetic fill, or exchange downtime. A published `suspected_outage` is
reviewed unexplained silence, not a proved outage, unless the `reference` is
qualifying URL evidence.

## Interval semantics

- Sidecar intervals are half-open `[start_timestamp, end_timestamp)` in Unix
  seconds, minute-aligned.
- A zero-volume run is a contiguous sequence of `volume == 0` candles. Its
  published bounds are the first zero minute and the first minute after the
  last zero minute.
- Official Statuspage windows use floored start and ceiled end.
- `suspected_outage` bounds come from the observed zero-run, even when
  corroborating text only names a date.

## Detector (selection, not publication)

Frozen parameters live in `scripts/outage_candidates.py` (`DETECTOR_VERSION`)
and `data/provenance/research/model.json`.

1. Stream historical + updates OHLCV through `as_of_timestamp`.
2. Hash the canonical CSV prefix (`timestamp <= as_of`). Later daily appends
   must not change that hash; rewriting a prefix candle must.
3. For each UTC month, compute the 99th percentile of inter-trade gaps
   (minutes between consecutive `volume > 0` candles).
4. A month is liquid-eligible when that p99 is at most 30 minutes.
5. The liquid regime begins at the first month of the first six-month streak
   of liquid-eligible months, and stays liquid afterward. Frozen as
   2013-03-01. That date is the review floor, not a liquidity claim.
6. Thin-regime runs are not published: overnight silence is expected.
7. Liquid-regime runs of **60 minutes or longer** become `pending_review`
   candidates unless wholly covered by `source_gap_filled`,
   `confirmed_outage`, or `scheduled_maintenance`.
8. 30 minutes is the absolute research floor used in sensitivity reports, not
   the publication floor. Sensitivity is reported at 30m, 60m, 2h, 4h, and 12h.

The 60-minute liquid floor is a review threshold so that a healthy liquid
year yields on the order of one unexplained exceedance. It is not a proof of
downtime.

Heuristic `reference` is `zero_volume>=60m`.

## Ledger states

| Status | Public sidecar? | Required fields |
| --- | --- | --- |
| `pending_review` | No | Duration selected the row; no decision yet |
| `reviewed_unconfirmed` | Yes | Reviewer, decision date, notes, heuristic reference |
| `corroborated` | Yes | Reviewer, decision date, notes, qualifying URL(s) |
| `excluded` | No | Overlaps official coverage, or reviewed out |
| `thin_unpublished` | No | Before the liquid review floor |
| `below_floor` | No | Liquid but shorter than 60 minutes |

`reviewed_unconfirmed` means “reviewed unexplained silence,” not proved
downtime.

## Source tiers

**Tier A (one source is enough)**

- Bitstamp Statuspage incident or maintenance record
- Dated Bitstamp notice, blog, support page, or archived official page
- Official Bitstamp account statement with an attributable timestamp

**Tier B (two independent contemporaneous sources)**

- Established newswire or publication with its own reporting
- Credible specialist publication with its own reporting
- Independent contemporaneous monitoring with clear provenance

Syndicated copies, mirrors, and two articles quoting the same unnamed report
count as one source. Archive copies of one page count as one source.

**Tier C (discovery only)**

Forums, Reddit, unsourced aggregators, and retrospective listicles may locate
evidence. They cannot satisfy publication of a URL `reference`.

Deposit, withdrawal, wallet, and unrelated-product incidents are ignored
unless evidence shows BTC/USD trading or its public API was affected.

## Query protocol

For every candidate at or above the research floor:

1. Search the UTC interval and the adjacent calendar dates.
2. Repeat with Europe/Amsterdam local dates for the same instants.
3. Query official Bitstamp properties and web archives of those properties.
4. Run the templates:
   - `"Bitstamp" outage OR offline OR downtime <date>`
   - `"Bitstamp" maintenance OR API OR trading <date>`
   - `"Bitstamp" hack OR "security incident" <month year>`
5. Record every query, provider, execution date, and whether anything
   relevant was reviewed — including searches that returned nothing.

Separately, search year-by-year (event-led) so incidents that did not produce
zero-volume candles remain discoverable.

## Publication

```text
official Bitstamp / Statuspage
  -> confirmed_outage | scheduled_maintenance

liquid-regime zero-run >= 60 minutes
  + qualifying evidence  -> suspected_outage  (corroborated, URL reference)
  + explicit review, no URL  -> suspected_outage  (reviewed_unconfirmed,
                                 zero_volume>=60m)
  + no review yet  -> ledger only (pending_review)

going-forward updater fill
  -> source_gap_filled
```

“No source found” after review means insufficient corroboration, never that
the market was healthy or that the minutes were genuine no-trade candles.

Absence of a covering interval still means “no annotation,” not “proved
healthy.” Statuspage silence before June 2023 is especially uninformative.

## Daily automation

Daily `scripts/provenance.py` refreshes `source_gap_filled`, official
Statuspage intervals, and candle-derived `price_jump` values. It publishes
only ledger-approved suspected rows. It must not discover new duration-only
rows or rewrite `data/provenance/research/`.

Regenerating candidates is a manual command:

```bash
uv run python scripts/outage_candidates.py --apply-decisions
```
