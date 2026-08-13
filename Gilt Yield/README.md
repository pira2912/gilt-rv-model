# UK Gilt Yield Curve Relative-Value Strategy — V1

A small, readable research project for three UK gilt curve trades: **2s5s**, **5s10s**,
and **10s30s**. It combines the Bank of England's official daily nominal government
liability curve with the UK DMO's current gilts-in-issue list. V1 creates lagged
signals, runs an equal-DV01 historical backtest, maps current signals to physical
gilts, calculates coupon cashflows and DV01, and produces CSV outputs and charts.

## Open and run in VS Code

1. Open this folder: `/Users/piraveen/Documents/Gilt Yield`
2. VS Code should select `.venv/bin/python` automatically.
3. Open `gilt_rv.py` and press **Run**, or use the terminal:

```bash
.venv/bin/python gilt_rv.py
```

To download the newest Bank of England archive again:

```bash
.venv/bin/python gilt_rv.py --refresh
```

## What V1 does

- Uses real daily fitted nominal zero-coupon gilt yields at 2, 5, 10, and 30 years.
- Defines each slope as long-maturity yield minus short-maturity yield.
- Calculates a 60-day rolling z-score using data lagged by one day.
- Enters at `|z| >= 2.0` and exits at `|z| <= 0.5`.
- Applies a `|z| >= 3.5` stop, a 20-day time stop, and explicit trading costs.
- Normalizes both legs to £1,000 DV01, removing first-order parallel-rate exposure.
- Saves the full signal history, daily P&L, trade blotter, statistics, and charts.
- Selects real conventional non-green gilts nearest 2, 5, 10, and 30 years.
- Calculates curve-implied dirty prices and numerical DV01 from coupon cashflows.
- Produces an equal-DV01 paper execution sheet with ISINs, actions, and notionals.

The assumptions are collected in the `Settings` class near the top of `gilt_rv.py`.
That is the easiest place to experiment without changing the engine.

## Outputs

- `outputs/yield_curves.png` — real historical yields and curve slopes
- `outputs/backtest_equity.png` — cumulative P&L by strategy and portfolio
- `outputs/paper_signals.png` — newest curve and entry-threshold diagnostics
- `outputs/signals.csv` — yields, slopes, and z-scores
- `outputs/daily_pnl.csv` — normalized strategy P&L
- `outputs/trades.csv` — trade-by-trade blotter
- `outputs/performance.csv` — compact portfolio statistics
- `outputs/selected_gilts.csv` — current physical gilt mapping and bond analytics
- `outputs/paper_execution.csv` — current paper actions and hedge notionals
- `data/processed/boe_nominal_spot.csv` — cleaned source dataset

## Important limitation

The historical test remains a **constant-maturity curve backtest** because the DMO no
longer provides a free continuous daily security-price history after July 2017. The
physical-gilt layer is therefore a current paper-execution mapping, not a retroactively
invented bond history. Curve-implied prices are dirty present values, not executable
market quotes; live bid/offer, accrued-interest settlement and financing still need a
licensed price feed before real orders are appropriate.

## Roadmap

1. **V1 robustness:** walk-forward parameter checks, subperiod/regime analysis, and
   stronger validation of costs and missing observations.
2. **Licensed prices:** add executable bid/offer and historical clean-price data, then
   include accrued interest, financing, carry, rolldown, and convexity attribution.
3. **Paper-trade ledger:** persist proposed fills and reconcile them daily without
   sending orders.

## Data source and methodology

Sources: [Bank of England — Yield curves](https://www.bankofengland.co.uk/statistics/yield-curves)
and [UK DMO — Gilt market data](https://www.dmo.gov.uk/data/gilt-market/).
The Bank describes these as fitted government liability curves derived from gilts and
GC repo rates. Published spot rates are continuously compounded annual yields. The Bank
notes that the archive is updated and may be revised.

This project is for research and education, not investment advice.
