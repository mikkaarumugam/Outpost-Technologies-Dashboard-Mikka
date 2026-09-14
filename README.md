# EU Market Entry Radar

One screen answering the first question a GBP-based brand asks before selling into Europe: **where are the online shoppers, how many already buy from abroad, and what do VAT and FX cost to reach them?**

Built for the Outpost application (bonus item: "pull a public dataset or feed and send a one-screen dashboard").

## What it shows

| Panel | Question it answers | Feed |
|---|---|---|
| KPI row | EU27 online-buyer share, cross-border shopper pool, VAT spread, most volatile GBP pair | all four |
| Bubble chart | Demand depth (buys online) vs. openness (buys from foreign sellers), sized by working-age population, coloured by eurozone vs. own currency | Eurostat, World Bank |
| VAT columns | Standard VAT rate per destination, sorted, with the UK rate as reference | ibericode/vat-rates |
| FX table | GBP against each non-euro currency: latest ECB rate, 12-month change, annualised volatility, sparkline | ECB via Frankfurter |
| Ranked table | Top markets by absolute cross-border shoppers, with cost columns beside them | all four |

Hovering any bubble, bar or table row highlights the same country everywhere.

## Sources (all public, no auth, JSON)

- **Eurostat** `isoc_ec_ib20` (% of individuals 16 to 74 who bought online in the last 12 months, 2025) and `isoc_ec_ibos` (% who bought from sellers in other countries in the last 3 months; the series ends in 2023).
- **World Bank** `SP.POP.1564.TO` (population 15 to 64) and `NY.GDP.PCAP.CD` (GDP per capita, USD), 2024.
- **ECB reference rates** via [frankfurter.dev](https://frankfurter.dev), GBP base, trailing 365 days. The page also tries a live refresh of the latest rate on load and falls back to the snapshot silently.
- **Standard VAT rates** from [ibericode/vat-rates](https://github.com/ibericode/vat-rates), a community-maintained file with dated entries (last commit shown in the footer). Norway and Switzerland are entered by hand.

## Method

- Cross-border shoppers = working-age population × share who bought from a foreign seller.
- Online buyers = working-age population × share who bought online in the last 12 months.
- FX volatility = standard deviation of daily log returns × √252.
- The two dashed lines on the bubble chart are the EU27 medians, so a bubble in the top-right quadrant is above-median on both demand and openness.

## Known limits

- Eurostat shares are of 16 to 74 year olds; the World Bank population is 15 to 64. Head counts are a proxy, not a census.
- The cross-border series stopped in 2023.
- VAT is the standard rate only. Reduced rates, OSS and IOSS thresholds and distance-selling rules are not modelled.
- Switzerland has no Eurostat cross-border data, so it appears in the VAT and FX panels only.

## Rebuild

```
python3 build.py
```

Fetches all four feeds, writes `data.json`, and injects it into `index.template.html` to produce a self-contained `index.html`. No dependencies beyond Python 3.

## It updates itself

A GitHub Actions workflow ([.github/workflows/build.yml](.github/workflows/build.yml)) reruns `build.py` every Monday at 06:00 UTC, on every push, and on demand from the Actions tab, then deploys the result to GitHub Pages. The "Built" timestamp in the page header shows when the data was last pulled. On top of that, the page refreshes the latest ECB rate in the browser each time it loads.
