# build_nav_pack_with_freeze.py
# 1) Builds nav_trailing.csv from nav.csv using an inline FREEZE_MAP (per holding -> FreezeQuarter).
# 2) Uses nav_trailing.csv to create the NAV Excel pack with price-ownership breakdowns.
#
# Requirements: pandas, xlsxwriter

from __future__ import annotations
import pandas as pd
import numpy as np
from pathlib import Path

# ========= CONFIG =========
BASE = Path(".")  # change if needed
NAV_IN_PATH = BASE / "nav.csv"
NAV_TRAILING_PATH = BASE / "nav_trailing.csv"   # auto-created first
PRICE_OWNERSHIP_CANDIDATES = [BASE / "price_ownership.csv", BASE / "prices_ownership.csv"]
OUT_XLSX = BASE / "HoldCo_NAV_Pack_PO_FROZEN.xlsx"
ROLLING_DAYS_1Y = 365

# Per-holding quarter to freeze (use "YYYYQn" like "2025Q2").
# Leave a holding absent or set "" to skip freezing.
FREEZE_MAP = {
    # "ORO_BLANCO": "2025Q2",
     "QUINENCO":   "2025Q2",
     "ANTARCHILE": "2025Q3",
     "ALMENDRAL":  "2025Q2",
}

# Which columns get frozen & forward-filled from the *next* quarter onward:
FREEZE_COLS = ["HoldingValue","NetDebt","NAV","eqy_shares_total","NAV_PS"]
# =========================


# --------- Part A: Create nav_trailing.csv ---------
def _load_nav_raw(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    if "Date_quarter" in df.columns:
        df["Date_quarter"] = df["Date_quarter"].astype(str)
    else:
        df["Date_quarter"] = df["Date"].dt.to_period("Q").astype(str)
    df["Holding"] = df["Holding"].astype(str).str.upper().str.replace("Ñ","N", regex=False)
    for c in ["HoldingValue","NetDebt","NAV","eqy_shares_total","NAV_PS","Price","Discount"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values(["Holding","Date"])


def _next_quarter_label(qstr: str) -> str | None:
    try:
        p = pd.Period(qstr, freq="Q")
        return (p + 1).strftime("%YQ%q")
    except Exception:
        return None


def _apply_freeze(group: pd.DataFrame, freeze_q: str) -> pd.DataFrame:
    g = group.sort_values("Date").copy()
    if not freeze_q or str(freeze_q).strip()=="" or str(freeze_q).lower()=="nan":
        return g
    base = g[g["Date_quarter"] == freeze_q].copy()
    if base.empty:
        return g
    base_row = base.sort_values("Date").tail(1).iloc[0]
    base_vals = {c: base_row.get(c, np.nan) for c in FREEZE_COLS}

    q_next = _next_quarter_label(freeze_q)
    if q_next is None:
        return g
    mask_after = g["Date_quarter"] >= q_next

    # keep originals for audit
    for c in FREEZE_COLS:
        if c in g.columns:
            g[f"{c}_orig"] = g[c]

    # overwrite frozen inputs from the chosen (freeze) quarter forward (starting next quarter)
    for c in FREEZE_COLS:
        if c in g.columns:
            g.loc[mask_after, c] = base_vals.get(c, np.nan)

    # recompute deriveds using frozen inputs
    if {"HoldingValue","NetDebt"}.issubset(g.columns):
        g.loc[mask_after, "NAV"] = g.loc[mask_after, "HoldingValue"] - g.loc[mask_after, "NetDebt"]
    if {"NAV","eqy_shares_total"}.issubset(g.columns):
        with np.errstate(divide="ignore", invalid="ignore"):
            g.loc[mask_after, "NAV_PS"] = g.loc[mask_after, "NAV"] / g.loc[mask_after, "eqy_shares_total"]
    if {"Price","NAV_PS"}.issubset(g.columns):
        with np.errstate(divide="ignore", invalid="ignore"):
            g.loc[mask_after, "Discount"] = 1 - (g.loc[mask_after, "Price"] / g.loc[mask_after, "NAV_PS"])

    return g


def build_nav_trailing(nav_in: Path, nav_out: Path, freeze_map: dict) -> pd.DataFrame:
    nav = _load_nav_raw(nav_in)
    parts = []
    for h, grp in nav.groupby("Holding", sort=False):
        fq = freeze_map.get(h, "")
        parts.append(_apply_freeze(grp, fq))
    nav_trailing = pd.concat(parts, ignore_index=True).sort_values(["Holding","Date"])
    nav_out.parent.mkdir(parents=True, exist_ok=True)
    nav_trailing.to_csv(nav_out, index=False, encoding="utf-8-sig")
    return nav_trailing


# --------- Part B: Build Excel pack from nav_trailing ---------
def _mm(x):
    return x / 1e6 if pd.notnull(x) else np.nan


def _pick_price_ownership(candidates) -> Path | None:
    for p in candidates:
        if p.exists():
            return p
    return None


def _load_price_ownership(path: Path, nav_panel: pd.DataFrame) -> pd.DataFrame | None:
    """Expect columns at least: Date, Holding, ID, pct_holding, MarketCap, HoldingValue."""
    if path is None:
        return None
    df = pd.read_csv(path)
    # Column guards / aliases
    alias = {
        "holding": "Holding", "HOLDING": "Holding",
        "date": "Date", "DATE": "Date",
        "id": "ID", "ticker": "ID", "Issuer": "ID",
        "pct": "pct_holding", "pctHolding": "pct_holding",
        "mktcap": "MarketCap", "mcap": "MarketCap",
        "holdingvalue": "HoldingValue", "Holding_Value": "HoldingValue",
    }
    for c in list(df.columns):
        if c in alias: df.rename(columns={c: alias[c]}, inplace=True)

    required = {"Date","Holding","ID"}
    if not required.issubset(df.columns):
        return None  # bail quietly if structure is different

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Holding"] = df["Holding"].astype(str).str.upper().str.replace("Ñ","N", regex=False)

    # Numeric coercion
    for c in ["pct_holding","MarketCap","HoldingValue"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Normalize pct -> fraction if needed
    if "pct_holding" in df.columns and df["pct_holding"].dropna().median() > 1:
        df["pct_holding"] = df["pct_holding"] / 100.0

    # Align to latest date per holding (prefer the same last date as nav_trailing; else last available <= that; else last overall)
    last_nav = nav_panel.groupby("Holding", as_index=False)["Date"].max().rename(columns={"Date":"NavLast"})
    df = df.merge(last_nav, on="Holding", how="right")
    def pick_rows(g):
        tgt = g["NavLast"].iloc[0]
        sub = g[g["Date"] <= tgt].copy()
        if sub.empty:
            sub = g.copy()
        if sub.empty:  # truly nothing
            return sub
        d_last = sub["Date"].max()
        return sub[sub["Date"] == d_last].copy()
    latest_rows = df.groupby("Holding", group_keys=False).apply(pick_rows)
    latest_rows = latest_rows.dropna(subset=["Date"])
    # Compute weights per holding
    if "HoldingValue" in latest_rows.columns:
        latest_rows["Weight"] = latest_rows.groupby("Holding")["HoldingValue"].apply(lambda s: s / s.sum())
    else:
        latest_rows["Weight"] = np.nan
    # Sort assets largest to smallest
    latest_rows = latest_rows.sort_values(["Holding","HoldingValue"], ascending=[True, False])
    return latest_rows


def _panel_and_latest(df: pd.DataFrame, days_1y: int):
    last = df.groupby("Holding", as_index=False)["Date"].max()
    panel = df.merge(last.rename(columns={"Date":"LastDate"}), on="Holding", how="left")
    panel["WindowStart"] = panel["LastDate"] - pd.Timedelta(days=days_1y)
    panel_1y = panel[(panel["Date"] >= panel["WindowStart"]) & (panel["Date"] <= panel["LastDate"])].copy()
    latest_rows = panel.sort_values("Date").groupby("Holding", as_index=False).tail(1)
    return panel_1y, latest_rows


def _build_summary(latest_rows: pd.DataFrame, panel_1y: pd.DataFrame) -> pd.DataFrame:
    nav_vals = latest_rows["NAV"] if "NAV" in latest_rows.columns else \
               (latest_rows["HoldingValue"] - latest_rows["NetDebt"])
    summary = pd.DataFrame({
        "Holding": latest_rows["Holding"],
        "Holding Value (CLP mm)": latest_rows["HoldingValue"].apply(_mm),
        "Net Debt (CLP mm)": latest_rows["NetDebt"].apply(_mm),
        "NAV (CLP mm)": nav_vals.apply(_mm),
        "# Shares (mm)": latest_rows["eqy_shares_total"] / 1e6,
        "NAV per Share": latest_rows["NAV_PS"],
        "Discount Now": latest_rows["Discount"],
    })
    stats = (panel_1y.groupby("Holding")["Discount"]
             .agg(Mean_1Y="mean", Std_1Y="std")
             .reset_index())
    summary = summary.merge(stats, on="Holding", how="left")
    summary["1Y avg"] = summary["Mean_1Y"]
    summary["Avg + 1 sd"] = summary["Mean_1Y"] + summary["Std_1Y"]
    summary["Avg - 1 sd"] = summary["Mean_1Y"] - summary["Std_1Y"]
    summary = summary.drop(columns=["Mean_1Y","Std_1Y"])
    cols = ["Holding","Holding Value (CLP mm)","Net Debt (CLP mm)","NAV (CLP mm)",
            "# Shares (mm)","NAV per Share","Discount Now","1Y avg","Avg + 1 sd","Avg - 1 sd"]
    return summary[cols].sort_values("Holding")


def _write_pack_with_po(summary: pd.DataFrame,
                        panel_1y: pd.DataFrame,
                        by_asset_latest: pd.DataFrame | None,
                        out_path: Path):
    with pd.ExcelWriter(out_path, engine="xlsxwriter", datetime_format="yyyy-mm-dd") as writer:
        wb = writer.book

        fmt_pct  = wb.add_format({"num_format":"0.0%"})
        fmt_pct2 = wb.add_format({"num_format":"0.00%"})
        fmt_int  = wb.add_format({"num_format":"#,##0"})
        fmt_2d   = wb.add_format({"num_format":"#,##0.00"})
        fmt_head = wb.add_format({"bold":True, "bg_color":"#E8EEF7", "border":1})
        fmt_title= wb.add_format({"bold":True, "font_size":12})

        # Summary
        sh = "Summary"
        summary.to_excel(writer, sheet_name=sh, index=False)
        ws = writer.sheets[sh]
        for j, c in enumerate(summary.columns):
            ws.write(0, j, c, fmt_head)
            ws.set_column(j, j, 18)
        for j, c in enumerate(summary.columns):
            if c in ["Discount Now","1Y avg","Avg + 1 sd","Avg - 1 sd"]:
                ws.set_column(j, j, 14, fmt_pct2)
            elif c in ["Holding Value (CLP mm)","Net Debt (CLP mm)","NAV (CLP mm)","# Shares (mm)"]:
                ws.set_column(j, j, 18, fmt_int)
            elif c == "NAV per Share":
                ws.set_column(j, j, 16, fmt_2d)

        # Per-holding sheets
        for h, g in panel_1y.groupby("Holding"):
            g = g.sort_values("Date")
            gl = g.tail(1).iloc[0]
            nav_mm = _mm((gl["HoldingValue"] - gl["NetDebt"])
                         if pd.notnull(gl.get("HoldingValue")) and pd.notnull(gl.get("NetDebt")) else np.nan)

            sheet = h[:31]
            ws = wb.add_worksheet(sheet)
            writer.sheets[sheet] = ws

            ws.write(0, 0, f"{h} — NAV Valuation (latest: {gl['Date'].date()})", fmt_title)
            ws.write(2, 0, "Metric", fmt_head); ws.write(2, 1, h, fmt_head)
            rows = [
                ("Holding Value (CLP mm)", _mm(gl.get("HoldingValue", np.nan))),
                ("Net Debt (CLP mm)", _mm(gl.get("NetDebt", np.nan))),
                ("NAV (CLP mm)", nav_mm),
                ("# Shares (mm)", gl.get("eqy_shares_total", np.nan)/1e6),
                ("NAV per Share", gl.get("NAV_PS", np.nan)),
                ("Discount Now", gl.get("Discount", np.nan)),
                ("1Y avg", g["Discount"].mean()),
                ("Avg + 1 sd", g["Discount"].mean() + g["Discount"].std(ddof=1)),
                ("Avg - 1 sd", g["Discount"].mean() - g["Discount"].std(ddof=1)),
            ]
            for i, (k,v) in enumerate(rows, start=3):
                ws.write(i, 0, k)
                if pd.isna(v): ws.write_blank(i, 1, None); 
                else: ws.write_number(i, 1, v)

            ws.set_column(0, 0, 24); ws.set_column(1, 1, 18)
            for r in [3,4,5]: ws.set_row(r, None, fmt_int)
            ws.set_row(6, None, fmt_2d)   # shares
            ws.set_row(7, None, fmt_2d)   # NAV/ps
            for r in [8,9,10,11]: ws.set_row(r, None, fmt_pct2)

            # 1Y series
            ts_start = len(rows) + 6
            ws.write(ts_start, 0, "Date", fmt_head); ws.write(ts_start, 1, "Discount (1Y)", fmt_head)
            for i, (dt, disc) in enumerate(zip(g["Date"], g["Discount"]), start=ts_start+1):
                ws.write_datetime(i, 0, dt.to_pydatetime()); ws.write_number(i, 1, disc if pd.notnull(disc) else np.nan, fmt_pct)
            nrows = len(g)
            if nrows > 2:
                chart = wb.add_chart({"type":"line"})
                chart.add_series({
                    "name": "Discount (1Y)",
                    "categories": [sheet, ts_start+1, 0, ts_start+nrows, 0],
                    "values":     [sheet, ts_start+1, 1, ts_start+nrows, 1],
                })
                chart.set_title({"name": f"{h} — Discount (1Y)"})
                chart.set_y_axis({"num_format":"0%"})
                ws.insert_chart(ts_start, 3, chart, {"x_scale":1.2, "y_scale":1.2})

            # By-Asset block (latest)
            if by_asset_latest is not None and not by_asset_latest.empty:
                blk = by_asset_latest[by_asset_latest["Holding"] == h]
                if not blk.empty:
                    start_r = ts_start + nrows + 3
                    ws.write(start_r, 0, "By Asset (latest)", fmt_head)
                    headers = ["ID","pct_holding","MarketCap","HoldingValue","Weight"]
                    for j, col in enumerate(headers, start=0):
                        ws.write(start_r+1, j, col, fmt_head)
                    for k, row in enumerate(blk.itertuples(index=False), start=start_r+2):
                        ws.write(k, 0, getattr(row, "ID"))
                        v = getattr(row, "pct_holding", np.nan)
                        ws.write_number(k, 1, float(v) if pd.notnull(v) else np.nan, fmt_pct2)
                        mv = getattr(row, "MarketCap", np.nan)
                        hv = getattr(row, "HoldingValue", np.nan)
                        ws.write_number(k, 2, _mm(mv) if pd.notnull(mv) else np.nan, fmt_int)
                        ws.write_number(k, 3, _mm(hv) if pd.notnull(hv) else np.nan, fmt_int)
                        w = getattr(row, "Weight", np.nan)
                        ws.write_number(k, 4, float(w) if pd.notnull(w) else np.nan, fmt_pct2)
                    ws.set_column(0, 0, 24)
                    ws.set_column(1, 1, 12, fmt_pct2)
                    ws.set_column(2, 3, 14, fmt_int)
                    ws.set_column(4, 4, 10, fmt_pct2)

        # Aggregate sheet
        if by_asset_latest is not None and not by_asset_latest.empty:
            agg = by_asset_latest.copy()
            agg["HoldingValue_mm"] = agg["HoldingValue"].apply(_mm)
            agg["MarketCap_mm"] = agg["MarketCap"].apply(_mm) if "MarketCap" in agg.columns else np.nan
            cols = ["Holding","ID","Date","pct_holding","MarketCap_mm","HoldingValue_mm","Weight"]
            if "Date" not in agg.columns:
                agg["Date"] = pd.NaT
            agg = agg[cols]
            agg.to_excel(writer, sheet_name="ByAsset_All", index=False)
            ws = writer.sheets["ByAsset_All"]
            for j, c in enumerate(cols):
                ws.write(0, j, c, fmt_head)
                if c in ["pct_holding","Weight"]:
                    ws.set_column(j, j, 12, fmt_pct2)
                elif c in ["MarketCap_mm","HoldingValue_mm"]:
                    ws.set_column(j, j, 16, fmt_int)
                else:
                    ws.set_column(j, j, 20)

    print(f"[WRITE] {out_path.resolve()}")


def main():
    # 1) Build nav_trailing first (frozen fundamentals per FREEZE_MAP)
    nav_trailing = build_nav_trailing(NAV_IN_PATH, NAV_TRAILING_PATH, FREEZE_MAP)

    # 2) Use nav_trailing to build the pack
    panel_1y, latest_rows = _panel_and_latest(nav_trailing, ROLLING_DAYS_1Y)
    summary = _build_summary(latest_rows, panel_1y)

    po_path = _pick_price_ownership(PRICE_OWNERSHIP_CANDIDATES)
    by_asset_latest = _load_price_ownership(po_path, nav_trailing)

    _write_pack_with_po(summary, panel_1y, by_asset_latest, OUT_XLSX)


if __name__ == "__main__":
    main()
