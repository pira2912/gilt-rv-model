# uk gilt yield curve model

this is my fixed-income research project for finding unusual moves in the uk
gilt curve.

the model uses the bank of england's daily fitted nominal curve at 2y, 5y, 10y and
30y. it calculates curve spreads, rolling z-scores and a ranked list of the biggest
outliers. matplotlib then makes the interesting bits easy to look at.

the point is to find something unusual and
then ask what was going on that might've caused that irregularity.

## run it

```bash
.venv/bin/python gilt_rv.py
```

to download the source archives again:

```bash
.venv/bin/python gilt_rv.py --refresh
```

the model uses pandas, numpy, matplotlib and openpyxl. the cached raw zip files are
ignored by git because they can be downloaded again from the bank of england.

## what it does

- calculates `2s5s = y5 - y2`, `5s10s = y10 - y5` and `10s30s = y30 - y10`
- calculates a 60-observation rolling z-score using the previous day's history
- Ranks the top 10 largest absolute z-scores across the three spreads
- saves the full series and the ranked outlier table
- draws the curve, the spreads and a bar chart of the top 10 irregularities

the z-score is just a way of saying “this is a long way relative to its recent range”. it
does not say that the spread should go back, and it definitely does not say what to
buy.

## a few of the biggest irregularities

the current run covers 1979 to august 2026. the exact top 10 are saved in
`irregularities.csv`. these are the ones i found easiest to connect to a
real macro story:

| date | section | spread | z-score | what was probably going on |
|---|---|---:|---:|---|
| 31 mar 1982 | 5s10s | 116.3bp | +9.25 | early-1980s inflation and tight monetary policy left the middle and long end behaving very differently. |
| 26 sep 1997 | 2s5s | -39.6bp | -7.58 | the asian financial crisis was a global risk repricing. the front end and belly did not move together in the usual way. |
| 19 mar 2020 | 2s5s | 27.4bp | +6.27 | covid and the “dash for cash” caused serious liquidity stress in gilts, even while the bank rate was being cut. |
| 28 sep 2022 | 10s30s | -33.0bp | -5.84 | the mini-budget shock and forced ldi selling hit long-dated gilts especially hard. |
| 23 jun 2023 | 2s5s | -61.9bp | -5.16 | the inflation and bank rate repricing cycle pushed the short end well above the belly. |
| 9 apr 2025 | 5s10s | 68.2bp | +4.97 | a global risk-off move and tariff-related repricing pushed the spread outside its recent range. |

the event links are there to give the chart some context, not to pretend this is a
causal event study. the model finds the odd move. the economic explanation still
needs a human reading the history.

- [march 2020 policy response](https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/2020/monetary-policy-summary-for-the-special-monetary-policy-committee-meeting-on-19-march-2020)
- [2022 gilt-market case study](https://www.bankofengland.co.uk/quarterly-bulletin/2023/2023/financial-stability-gilt-buy-sell-tools-a-gilt-market-case-study)
- [anatomy of the 2022 gilt-market crisis](https://www.bankofengland.co.uk/working-paper/2023/an-anatomy-of-the-2022-gilt-market-crisis)
- [bank of england note on 16 september 1992](https://www.bankofengland.co.uk/boeapps/database/fromshowcolumns.asp?C=13T&CSVF=TT&DAT=RNG&FD=1&FM=Jan&FY=2015&Filter=N&FromSeries=1&TD=10&TM=Sep&TY=2025&ToSeries=50&Travel=NIxSUx&html.x=111&html.y=14)

## files

```text
gilt_rv.py                 curve loading, spreads, z-scores and charts
test_gilt_rv.py            small check for the feature calculations
boe_nominal_spot.csv       cleaned bank of england curve data
signals.csv                all yields, spreads and z-scores
irregularities.csv         top 10 outliers
yield_curves.png           curve and spread history
top_irregularities.png
                           bar chart of the top 10
```

run the check with:

```bash
.venv/bin/python -m unittest test_gilt_rv.py
```

## v1 limitation

the historical data is made of constant-maturity fitted curve points. it does not
follow one named gilt through time. this means the output is a curve model, not a
security-level price or execution history.

the spreads are useful for spotting dislocations, but the model does not include
bond cashflows, accrued interest, repo, bid-offer, or point-in-time security
selection. that is fine for this version, but I'd like to improve

## sources

- [bank of england yield curves](https://www.bankofengland.co.uk/statistics/yield-curves)
- [uk debt management office gilt market data](https://www.dmo.gov.uk/data/gilt-market/)
