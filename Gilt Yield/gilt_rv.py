"""UK gilt curve relative-value research and paper-execution model.

The model is deliberately kept in one readable file.  It trades changes in three
constant-maturity curve slopes (2s5s, 5s10s and 10s30s) when their lagged rolling
z-scores look unusually steep or flat.

V1 keeps the honest constant-maturity historical backtest and adds a practical
paper-execution layer: real conventional gilts from the UK DMO, coupon cashflows,
curve-implied dirty prices, numerical DV01, and equal-DV01 hedge notionals.

This is research software, not investment advice or an order-routing system.
"""

from __future__ import annotations

import argparse
import io
import unicodedata
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# Keeping paths relative to this file makes the project work from VS Code, a shell,
# or any other current working directory.
ROOT = Path(__file__).resolve().parent
RAW_ZIP = ROOT / "data/raw/boe_nominal_daily.zip"
LATEST_ZIP = ROOT / "data/raw/boe_nominal_latest.zip"
DMO_XML = ROOT / "data/raw/dmo_gilts_in_issue.xml"
CURVE_CSV = ROOT / "data/processed/boe_nominal_spot.csv"
OUTPUT_DIR = ROOT / "outputs"

# Official Bank of England daily government liability nominal curve archive.
# The Bank publishes this as Excel workbooks inside one ZIP file, not through an API.
BOE_URL = (
    "https://www.bankofengland.co.uk/-/media/boe/files/statistics/"
    "yield-curves/glcnominalddata.zip"
)
BOE_LATEST_URL = (
    "https://www.bankofengland.co.uk/-/media/boe/files/statistics/"
    "yield-curves/latest-yield-curve-data.zip"
)
DMO_URL = "https://dmo.gov.uk/data/XmlDataReport?reportCode=D1A"

# These are the constant maturities used by the three adjacent curve trades.
TENORS = (2.0, 5.0, 10.0, 30.0)
PAIRS = {"2s5s": (2.0, 5.0), "5s10s": (5.0, 10.0), "10s30s": (10.0, 30.0)}


@dataclass(frozen=True)
class Settings:
    """All strategy assumptions in one place so experiments remain transparent."""

    lookback: int = 60
    entry_z: float = 2.0
    exit_z: float = 0.5
    stop_z: float = 3.5
    max_holding_days: int = 20
    dv01_per_leg: float = 1_000.0  # £ P&L for each 1 bp move in a leg.
    cost_bps_per_leg: float = 0.10  # Cost measured in yield-bp per leg per trade side.


def _download(url: str, destination: Path) -> None:
    """Download one public source file without adding another HTTP dependency."""

    print(f"Downloading {destination.name} …")
    # A named User-Agent avoids overly generic requests being rejected by some CDNs.
    request = urllib.request.Request(url, headers={"User-Agent": "gilt-rv-research/2.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        destination.write_bytes(response.read())


def download_data(force: bool = False) -> list[Path]:
    """Cache the curve history, current month, and current DMO security list."""

    RAW_ZIP.parent.mkdir(parents=True, exist_ok=True)
    sources = [(BOE_URL, RAW_ZIP), (BOE_LATEST_URL, LATEST_ZIP), (DMO_URL, DMO_XML)]
    for url, path in sources:
        if force or not path.exists():
            _download(url, path)
    return [RAW_ZIP, LATEST_ZIP]


def _read_spot_sheet(workbook: bytes) -> pd.DataFrame:
    """Read selected maturities from one Bank of England Excel workbook.

    The workbook is presentation-oriented: row 4 contains maturity labels and row 6
    begins the observations.  Reading without a header makes that layout explicit.
    """

    # The Bank renamed this tab after 2004 ("nominal spot curve" became "spot
    # curve").  Matching the descriptive suffix keeps one parser valid for the
    # full archive without hard-coding calendar-specific rules.
    source = io.BytesIO(workbook)
    excel = pd.ExcelFile(source)
    sheet = next(name for name in excel.sheet_names if name.endswith("spot curve"))
    raw = pd.read_excel(excel, sheet_name=sheet, header=None)
    maturities = pd.to_numeric(raw.iloc[3, 1:], errors="coerce")

    # Locate the exact maturity columns rather than relying on fixed Excel letters.
    result = pd.DataFrame({"date": pd.to_datetime(raw.iloc[5:, 0], errors="coerce")})
    for maturity in TENORS:
        matches = np.flatnonzero(np.isclose(maturities, maturity))
        # Long maturities were not published in every early workbook.  Preserve
        # those dates with a missing value; the combined backtest will begin once
        # all four tenors genuinely exist instead of inventing an extrapolation.
        result[f"y{int(maturity)}"] = (
            pd.to_numeric(raw.iloc[5:, 1 + matches[0]], errors="coerce")
            if len(matches)
            else np.nan
        )
    return result.dropna(subset=["date"])


def load_curves(refresh: bool = False) -> pd.DataFrame:
    """Return one clean daily dataset assembled from every archive workbook."""

    archives = download_data(force=refresh)
    frames: list[pd.DataFrame] = []
    for archive in archives:
        with zipfile.ZipFile(archive) as files:
            # The latest package also contains real, inflation, and OIS files.  Only
            # the nominal workbook belongs in this model.
            names = [n for n in files.namelist() if "Nominal" in n and n.endswith(".xlsx")]
            for name in sorted(names):
                print(f"Reading {name}")
                frames.append(_read_spot_sheet(files.read(name)))

    curves = (
        pd.concat(frames)
        .drop_duplicates("date", keep="last")
        .sort_values("date")
        .set_index("date")
        .dropna(how="all")
    )
    CURVE_CSV.parent.mkdir(parents=True, exist_ok=True)
    curves.to_csv(CURVE_CSV, float_format="%.6f")
    return curves


def add_signals(curves: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    """Calculate slopes and strictly lagged z-scores without look-ahead bias."""

    data = curves.copy()
    for name, (short, long) in PAIRS.items():
        spread = (data[f"y{int(long)}"] - data[f"y{int(short)}"]) * 100.0
        history = spread.shift(1)  # Today's signal may only use yesterday's history.
        mean = history.rolling(settings.lookback, min_periods=settings.lookback).mean()
        std = history.rolling(settings.lookback, min_periods=settings.lookback).std()
        data[f"{name}_spread"] = spread
        data[f"{name}_z"] = (spread - mean) / std.replace(0, np.nan)
    return data


def backtest_pair(data: pd.DataFrame, pair: str, settings: Settings) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run one transparent state-machine backtest for one curve slope.

    direction +1 is a steepener and profits when the long yield rises relative to the
    short yield.  direction -1 is a flattener.  P&L is normalized to equal DV01 on
    both legs, which removes first-order exposure to a parallel curve shift.
    """

    spread = data[f"{pair}_spread"]
    zscore = data[f"{pair}_z"]
    daily_change = spread.diff()
    position = pd.Series(0, index=data.index, dtype=int)
    pnl = pd.Series(0.0, index=data.index)
    trades: list[dict] = []

    direction = 0
    entry_date = None
    entry_z = np.nan
    holding_days = 0
    trade_pnl = 0.0
    one_side_cost = 2 * settings.cost_bps_per_leg * settings.dv01_per_leg

    for i, date in enumerate(data.index):
        z = zscore.iloc[i]
        exited_today = False

        # An open trade earns today's close-to-close move.  Entry signals are acted
        # on at today's close, so a new trade cannot earn the move that created it.
        if direction and i > 0:
            day_pnl = direction * daily_change.iloc[i] * settings.dv01_per_leg
            pnl.iloc[i] += day_pnl
            trade_pnl += day_pnl
            holding_days += 1

            exit_reason = None
            if abs(z) <= settings.exit_z:
                exit_reason = "mean_reversion"
            elif abs(z) >= settings.stop_z:
                exit_reason = "z_stop"
            elif holding_days >= settings.max_holding_days:
                exit_reason = "time_stop"

            if exit_reason:
                pnl.iloc[i] -= one_side_cost
                trade_pnl -= one_side_cost
                trades.append(
                    {
                        "pair": pair,
                        "entry_date": entry_date,
                        "exit_date": date,
                        "direction": "steepener" if direction == 1 else "flattener",
                        "entry_z": entry_z,
                        "exit_z": z,
                        "holding_days": holding_days,
                        "pnl_gbp": trade_pnl,
                        "exit_reason": exit_reason,
                    }
                )
                direction, entry_date, holding_days, trade_pnl = 0, None, 0, 0.0
                exited_today = True

        # Enter only when flat.  A high positive z-score is unusually steep, so the
        # mean-reversion position is a flattener; a negative z-score is a steepener.
        if not exited_today and direction == 0 and pd.notna(z) and abs(z) >= settings.entry_z:
            direction = -1 if z > 0 else 1
            entry_date, entry_z = date, z
            pnl.iloc[i] -= one_side_cost
            trade_pnl = -one_side_cost

        position.iloc[i] = direction

    daily = pd.DataFrame({"position": position, "pnl_gbp": pnl})
    return daily, pd.DataFrame(trades)


def performance(daily: pd.DataFrame, trades: pd.DataFrame) -> pd.Series:
    """Calculate compact, interpretable research statistics."""

    pnl = daily["pnl_gbp"]
    volatility = pnl.std()
    sharpe = np.sqrt(252) * pnl.mean() / volatility if volatility else np.nan
    equity = pnl.cumsum()
    drawdown = equity - equity.cummax()
    return pd.Series(
        {
            "start": daily.index.min().date(),
            "end": daily.index.max().date(),
            "trades": len(trades),
            "win_rate": (trades["pnl_gbp"] > 0).mean() if len(trades) else np.nan,
            "total_pnl_gbp": pnl.sum(),
            "sharpe_daily_pnl": sharpe,
            "max_drawdown_gbp": drawdown.min(),
        }
    )


def _coupon_from_name(name: str) -> float:
    """Turn DMO labels such as '4 3/8%' and '3¾%' into numeric coupons."""

    text = name.split("%", 1)[0].strip()
    if "/" in text:
        pieces = text.split()
        numerator, denominator = pieces[-1].split("/")
        return sum(map(float, pieces[:-1])) + float(numerator) / float(denominator)

    # Several DMO names use Unicode fraction characters without a separating space.
    whole = "".join(character for character in text if character.isdigit() or character == ".")
    fraction = sum(
        unicodedata.numeric(character)
        for character in text
        if not character.isdigit() and character not in ". "
    )
    return float(whole or 0) + fraction


def load_gilts() -> tuple[date, pd.DataFrame]:
    """Read the official DMO list and retain conventional, non-green gilts."""

    root = ElementTree.parse(DMO_XML).getroot()
    records = []
    for item in root:
        attributes = item.attrib
        name = attributes["INSTRUMENT_NAME"]
        if attributes["INSTRUMENT_TYPE"].strip() != "Conventional" or "Green" in name:
            continue
        records.append(
            {
                "name": name,
                "isin": attributes["ISIN_CODE"],
                "coupon_pct": _coupon_from_name(name),
                "maturity": pd.Timestamp(attributes["REDEMPTION_DATE"]),
                "amount_in_issue_gbp_mn": float(attributes["TOTAL_AMOUNT_IN_ISSUE"]),
                "as_of": pd.Timestamp(attributes["CLOSE_OF_BUSINESS_DATE"]),
            }
        )
    gilts = pd.DataFrame(records).sort_values("maturity").reset_index(drop=True)
    return gilts["as_of"].max().date(), gilts


def bond_price_and_dv01(
    settlement: date, maturity: date, coupon_pct: float, yield_pct: float
) -> tuple[float, float]:
    """Return curve-implied dirty price and DV01 per £100 nominal.

    Gilts pay coupons semi-annually.  We discount every remaining contractual cash
    flow using the Bank curve's continuously compounded yield.  Numerical DV01 is
    the centred price change for a one-basis-point yield move.
    """

    settlement_ts, maturity_ts = pd.Timestamp(settlement), pd.Timestamp(maturity)
    payment_dates = []
    payment = maturity_ts
    while payment > settlement_ts:
        payment_dates.append(payment)
        payment -= pd.DateOffset(months=6)

    def present_value(rate_pct: float) -> float:
        value = 0.0
        for payment_date in payment_dates:
            years = (payment_date - settlement_ts).days / 365.25
            cashflow = coupon_pct / 2 + (100.0 if payment_date == maturity_ts else 0.0)
            value += cashflow * np.exp(-(rate_pct / 100) * years)
        return value

    price = present_value(yield_pct)
    dv01 = (present_value(yield_pct - 0.01) - present_value(yield_pct + 0.01)) / 2
    return price, dv01


def paper_execution(data: pd.DataFrame, settings: Settings) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Map the newest curve signals to real gilts and equal-DV01 paper notionals."""

    dmo_date, gilts = load_gilts()
    curve_date = data.dropna(subset=[f"y{int(t)}" for t in TENORS]).index[-1]
    latest = data.loc[curve_date]
    settlement = min(dmo_date, curve_date.date())
    gilts["years_to_maturity"] = (gilts["maturity"] - pd.Timestamp(settlement)).dt.days / 365.25

    # Choose the conventional non-green gilt nearest each target maturity.  Exact
    # historical security selection needs a point-in-time reference database; this
    # current mapping is intentionally for paper execution only.
    selected = {
        tenor: gilts.loc[(gilts["years_to_maturity"] - tenor).abs().idxmin()].copy()
        for tenor in TENORS
    }
    security_rows, execution_rows = [], []
    for tenor, gilt in selected.items():
        price, dv01 = bond_price_and_dv01(
            settlement,
            gilt["maturity"].date(),
            gilt["coupon_pct"],
            latest[f"y{int(tenor)}"],
        )
        gilt_info = {
            "target_tenor": int(tenor),
            "name": gilt["name"],
            "isin": gilt["isin"],
            "maturity": gilt["maturity"].date(),
            "coupon_pct": gilt["coupon_pct"],
            "curve_yield_pct": latest[f"y{int(tenor)}"],
            "curve_dirty_price": price,
            "dv01_per_100": dv01,
            "amount_in_issue_gbp_mn": gilt["amount_in_issue_gbp_mn"],
        }
        security_rows.append(gilt_info)
        selected[tenor] = gilt_info

    for pair, (short, long) in PAIRS.items():
        z = latest[f"{pair}_z"]
        signal = "flattener" if z >= settings.entry_z else "steepener" if z <= -settings.entry_z else "flat"
        actions = {
            "flattener": ("SELL", "BUY"),
            "steepener": ("BUY", "SELL"),
            "flat": ("HOLD", "HOLD"),
        }[signal]
        for leg, tenor, action in zip(("short", "long"), (short, long), actions):
            gilt = selected[tenor]
            execution_rows.append(
                {
                    "curve_date": curve_date.date(),
                    "pair": pair,
                    "z_score": z,
                    "signal": signal,
                    "leg": leg,
                    "action": action,
                    "target_tenor": int(tenor),
                    "gilt": gilt["name"],
                    "isin": gilt["isin"],
                    "nominal_gbp": 100 * settings.dv01_per_leg / gilt["dv01_per_100"],
                    "leg_dv01_gbp": settings.dv01_per_leg,
                }
            )
    return pd.DataFrame(security_rows), pd.DataFrame(execution_rows)


def make_charts(
    data: pd.DataFrame, daily: pd.DataFrame, execution: pd.DataFrame, settings: Settings
) -> None:
    """Create historical, diagnostic, and current-signal Matplotlib figures."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    plt.style.use("seaborn-v0_8-whitegrid")

    fig, axes = plt.subplots(2, 1, figsize=(13, 9), sharex=True)
    data[[f"y{int(t)}" for t in TENORS]].plot(ax=axes[0], linewidth=1)
    axes[0].set(title="Bank of England nominal zero-coupon gilt curve", ylabel="Yield (%)")
    axes[0].legend([f"{int(t)}Y" for t in TENORS], ncol=4)
    data[[f"{p}_spread" for p in PAIRS]].plot(ax=axes[1], linewidth=1)
    axes[1].axhline(0, color="black", linewidth=0.7)
    axes[1].set(title="Curve slopes", ylabel="Basis points", xlabel="Date")
    axes[1].legend(PAIRS, ncol=3)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "yield_curves.png", dpi=160)
    plt.close(fig)

    equity = daily.filter(like="pnl_gbp").cumsum()
    portfolio = equity["portfolio_pnl_gbp"]
    drawdown = portfolio - portfolio.cummax()
    fig, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True, height_ratios=[3, 1])
    equity.plot(ax=axes[0], linewidth=1.2)
    axes[0].set(
        title="Historical V1 research P&L — equal £1,000 DV01 per leg",
        ylabel="Cumulative P&L (£)",
    )
    axes[0].legend([name.replace("_pnl_gbp", "") for name in equity], ncol=4)
    drawdown.plot(ax=axes[1], color="firebrick", linewidth=1)
    axes[1].fill_between(drawdown.index, drawdown, 0, color="firebrick", alpha=0.18)
    axes[1].set(
        title="Portfolio drawdown",
        xlabel="Date",
        ylabel="£",
    )
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "backtest_equity.png", dpi=160)
    plt.close(fig)

    # A compact 'what would the model do now?' chart is more useful for paper
    # trading than another historical plot.
    latest = data.dropna(subset=[f"y{int(t)}" for t in TENORS]).iloc[-1]
    zscores = pd.Series({pair: latest[f"{pair}_z"] for pair in PAIRS})
    colors = ["firebrick" if abs(z) >= settings.entry_z else "steelblue" for z in zscores]
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    axes[0].plot(TENORS, [latest[f"y{int(t)}"] for t in TENORS], marker="o")
    axes[0].set(title=f"Latest curve: {latest.name.date()}", xlabel="Maturity (years)", ylabel="Yield (%)")
    axes[1].bar(zscores.index, zscores, color=colors)
    axes[1].axhline(settings.entry_z, color="black", linestyle="--", linewidth=0.8)
    axes[1].axhline(-settings.entry_z, color="black", linestyle="--", linewidth=0.8)
    axes[1].set(title="Current lagged z-scores", ylabel="Z-score")
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "paper_signals.png", dpi=160)
    plt.close(fig)


def run(refresh: bool = False) -> None:
    """Execute the complete reproducible research pipeline."""

    settings = Settings()
    curves = load_curves(refresh=refresh)
    data = add_signals(curves, settings)
    all_daily, all_trades = [], []

    for pair in PAIRS:
        daily, trades = backtest_pair(data, pair, settings)
        all_daily.append(daily["pnl_gbp"].rename(f"{pair}_pnl_gbp"))
        all_trades.append(trades)

    daily = pd.concat(all_daily, axis=1).fillna(0.0)
    daily["portfolio_pnl_gbp"] = daily.sum(axis=1)
    trades = pd.concat(all_trades, ignore_index=True)
    stats = performance(daily[["portfolio_pnl_gbp"]].rename(columns={"portfolio_pnl_gbp": "pnl_gbp"}), trades)
    securities, execution = paper_execution(data, settings)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    data.to_csv(OUTPUT_DIR / "signals.csv", float_format="%.6f")
    daily.to_csv(OUTPUT_DIR / "daily_pnl.csv", float_format="%.2f")
    trades.to_csv(OUTPUT_DIR / "trades.csv", index=False, float_format="%.4f")
    stats.rename("value").to_csv(OUTPUT_DIR / "performance.csv")
    securities.to_csv(OUTPUT_DIR / "selected_gilts.csv", index=False, float_format="%.6f")
    execution.to_csv(OUTPUT_DIR / "paper_execution.csv", index=False, float_format="%.4f")
    make_charts(data, daily, execution, settings)

    print("\nV1 backtest and paper execution complete")
    print(stats.to_string())
    print("\nLatest paper signals")
    print(execution.groupby("pair", sort=False)[["z_score", "signal"]].first().to_string())
    print(f"\nOutputs: {OUTPUT_DIR}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="refresh BoE and DMO data")
    run(refresh=parser.parse_args().refresh)
