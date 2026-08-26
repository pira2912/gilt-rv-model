"""small model for finding unusual moves in the uk gilt curve."""

from __future__ import annotations

import argparse
import io
import urllib.request
import zipfile
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
RAW_ZIP = ROOT / "data/raw/boe_nominal_daily.zip"
LATEST_ZIP = ROOT / "data/raw/boe_nominal_latest.zip"
CURVE_CSV = ROOT / "data/processed/boe_nominal_spot.csv"
OUTPUT_DIR = ROOT / "outputs"

BOE_URL = "https://www.bankofengland.co.uk/-/media/boe/files/statistics/yield-curves/glcnominalddata.zip"
BOE_LATEST_URL = "https://www.bankofengland.co.uk/-/media/boe/files/statistics/yield-curves/latest-yield-curve-data.zip"
TENORS = (2, 5, 10, 30)
PAIRS = {"2s5s": (2, 5), "5s10s": (5, 10), "10s30s": (10, 30)}


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "gilt-curve-model/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        destination.write_bytes(response.read())


def _read_spot_sheet(workbook: bytes) -> pd.DataFrame:
    excel = pd.ExcelFile(io.BytesIO(workbook))
    sheet = next(name for name in excel.sheet_names if name.endswith("spot curve"))
    raw = pd.read_excel(excel, sheet_name=sheet, header=None)
    maturities = pd.to_numeric(raw.iloc[3, 1:], errors="coerce")
    result = pd.DataFrame({"date": pd.to_datetime(raw.iloc[5:, 0], errors="coerce")})
    for tenor in TENORS:
        matches = np.flatnonzero(np.isclose(maturities, tenor))
        result[f"y{tenor}"] = (
            pd.to_numeric(raw.iloc[5:, 1 + matches[0]], errors="coerce")
            if len(matches)
            else np.nan
        )
    return result.dropna(subset=["date"])


def load_curves(refresh: bool = False) -> pd.DataFrame:
    """load the official curve archive and return one daily dataframe."""

    RAW_ZIP.parent.mkdir(parents=True, exist_ok=True)
    for url, path in ((BOE_URL, RAW_ZIP), (BOE_LATEST_URL, LATEST_ZIP)):
        if refresh or not path.exists():
            print(f"downloading {path.name}")
            download(url, path)

    frames = []
    for archive in (RAW_ZIP, LATEST_ZIP):
        with zipfile.ZipFile(archive) as files:
            names = sorted(n for n in files.namelist() if "Nominal" in n and n.endswith(".xlsx"))
            for name in names:
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


def add_features(curves: pd.DataFrame, lookback: int = 60) -> pd.DataFrame:
    data = curves.copy()
    for pair, (short, long) in PAIRS.items():
        spread = (data[f"y{long}"] - data[f"y{short}"]) * 100
        history = spread.shift(1)
        mean = history.rolling(lookback, min_periods=lookback).mean()
        std = history.rolling(lookback, min_periods=lookback).std()
        data[f"{pair}_spread"] = spread
        data[f"{pair}_z"] = (spread - mean) / std.replace(0, np.nan)
    return data


def largest_irregularities(data: pd.DataFrame, limit: int = 10) -> pd.DataFrame:
    rows = []
    for pair in PAIRS:
        rows.append(
            data[[f"{pair}_spread", f"{pair}_z"]]
            .dropna()
            .rename(columns={f"{pair}_spread": "spread_bp", f"{pair}_z": "z_score"})
            .assign(pair=pair)
            .reset_index(names="date")
        )
    result = pd.concat(rows, ignore_index=True)
    result["abs_z"] = result["z_score"].abs()
    return result.sort_values("abs_z", ascending=False).head(limit)[
        ["date", "pair", "spread_bp", "z_score"]
    ].reset_index(drop=True)


def make_charts(data: pd.DataFrame, outliers: pd.DataFrame) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    plt.style.use("seaborn-v0_8-whitegrid")

    fig, axes = plt.subplots(2, 1, figsize=(13, 9), sharex=True)
    data[[f"y{tenor}" for tenor in TENORS]].plot(ax=axes[0], linewidth=1)
    axes[0].set(title="uk nominal gilt curve", ylabel="yield (%)")
    axes[0].legend([f"{tenor}y" for tenor in TENORS], ncol=4)
    data[[f"{pair}_spread" for pair in PAIRS]].plot(ax=axes[1], linewidth=1)
    axes[1].axhline(0, color="black", linewidth=0.7)
    axes[1].set(title="curve spreads", ylabel="basis points", xlabel="date")
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "yield_curves.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(12, 6))
    labels = outliers["date"].dt.strftime("%Y-%m-%d") + " " + outliers["pair"]
    ax.barh(labels[::-1], outliers["z_score"][::-1], color=np.where(outliers["z_score"][::-1] > 0, "firebrick", "steelblue"))
    ax.axvline(0, color="black", linewidth=0.7)
    ax.set(title="top 10 curve irregularities", xlabel="lagged rolling z-score")
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "top_irregularities.png", dpi=160)
    plt.close(fig)


def run(refresh: bool = False) -> None:
    data = add_features(load_curves(refresh=refresh))
    outliers = largest_irregularities(data)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    data.to_csv(OUTPUT_DIR / "signals.csv", float_format="%.6f")
    outliers.to_csv(OUTPUT_DIR / "irregularities.csv", index=False, float_format="%.4f")
    make_charts(data, outliers)
    print(f"modelled {len(data):,} curve observations")
    print(outliers.to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true")
    run(refresh=parser.parse_args().refresh)
