"""Utility helpers shared across the NAV pipeline."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def ensure_period(series: pd.Series) -> pd.Series:
    if not str(series.dtype).startswith("period"):
        return pd.PeriodIndex(series, freq="Q")
    return series


def read_expected_csv(path: Path, expected_cols: Sequence[str], **kwargs) -> pd.DataFrame:
    df = pd.read_csv(path, **kwargs)
    missing = [c for c in expected_cols if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing required columns {missing}. Available: {list(df.columns)}")
    return df


def to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def clean_percent(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.replace("%", "", regex=False)
    s = s.str.replace(",", ".", regex=False)
    s = s.str.replace("\xa0", " ")
    out = to_numeric(s)
    if out.dropna().median() > 1:
        out = out / 100.0
    return out


def wide_to_long_fill(df: pd.DataFrame, index_col: str, column_col: str, value_col: str) -> pd.DataFrame:
    wide = df.pivot(index=index_col, columns=column_col, values=value_col).reset_index()
    wide.columns.name = None
    idx = pd.date_range(start=df[index_col].min(), end=df[index_col].max(), freq="D")
    full = pd.DataFrame({index_col: idx}).merge(wide, on=index_col, how="left")
    full.ffill(inplace=True)
    long = full.melt(id_vars=[index_col], var_name=column_col, value_name=value_col)
    return long


def safe_merge(left: pd.DataFrame, right: pd.DataFrame, on: Sequence[str], how: str = "inner") -> pd.DataFrame:
    if left.empty or right.empty:
        return left.merge(right, on=on, how=how)
    return left.merge(right, on=on, how=how)


def last_per_group(df: pd.DataFrame, group_cols: Sequence[str], sort_cols: Sequence[str]) -> pd.DataFrame:
    return df.sort_values(list(group_cols) + list(sort_cols)).groupby(list(group_cols), as_index=False).tail(1)


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def summarize_trailing(df: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    windows = {"1M": 30, "3M": 91, "6M": 182, "1Y": 365}
    out = []
    for holding, grp in df.groupby("Holding", sort=False):
        grp = grp.sort_values("Date")
        if grp.empty:
            continue
        last = grp.iloc[-1]
        row = {
            "Holding": holding,
            "Date": pd.to_datetime(last["Date"]),
            "Discount": float(last["Discount"]),
        }
        for label, days in windows.items():
            mask = grp["Date"] >= (asof - pd.Timedelta(days=days))
            window = grp.loc[mask, "Discount"]
            row[f"Disc_mean_{label}"] = float(window.mean()) if not window.empty else np.nan
        mask_1y = grp["Date"] >= (asof - pd.Timedelta(days=365))
        window_1y = grp.loc[mask_1y, "Discount"]
        if not window_1y.empty:
            std_1y = float(window_1y.std(ddof=1))
            mean_1y = float(window_1y.mean())
            row["Disc_std_1Y"] = std_1y
            row["Disc_mean_1Y"] = mean_1y
            row["Disc_z_1Y"] = (row["Discount"] - mean_1y) / std_1y if std_1y not in (0.0, float("nan")) else np.nan
        out.append(row)
    return pd.DataFrame(out)
