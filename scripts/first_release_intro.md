In celebration of reaching 100 stars, we've revisited this repo and made some improvements:

### Daily updates now append instead of overwrite

Previously, daily updates were overwriting the git history each day, requiring a `git reset` to pull new changes. This was to prevent the git history from growing, but it's unnecessary.

Now, updates are appended. You can get the latest data with a regular `git pull`.

### DST offset bug fix

The historical dataset that we originally sourced from Kaggle used a local timezone for timestamps, introducing daylight savings time jumps. How this was missed before is beyond me.

The data has been rebuilt with correct UTC timestamps, fixing the DST offsets.

### Data Provenance

Because we backfill missing minutes, there is an ambiguity problem from zero-volume candles.

A zero-volume candle could mean that there were genuinely no trades in that minute (quiet period), or there is just a missing record for that minute (data provider issue), or the exchange was offline (outage or maintenance). All of these "look the same". That makes it difficult to trust the data, or to do the more rigorous data science work, where you may want to filter outages, or just know what a zero-volume candle means.

To that end, we've added a "sidecar" that monitors Bitstamp status/incident pages, where we label the intervals with zero-volume minutes accordingly. This is saved in a separate, sparse CSV file, allowing you to distinguish between "known outages" and "suspected outages". It also gives more confidence in the overall quality of the dataset. See the readme section for more info.

### Monthly releases

This is the first full-history snapshot of the Bitstamp BTC/USD 1-minute candle data. It will get published every month, along with some stats about price changes, and info about outages that month.

___
