# build_nav_pack_with_freeze_v11.py
# Change log vs v10:
# - Uses the AsOf(-7d) snapshot (with lastPrices fallback) to override
#   "Discount Now" across the workbook (Summary + per-holding sheets).
# - Per-holding charts get a synthetic last point at AsOfTarget with the
#   snapshot discount so the line ends at the latest computed value.
# - Each per-holding sheet shows a small "Snapshot details" box
#   (AsOfTarget, PriceDateUsed, PriceSource).
#
# Other features from v10 are preserved: freeze by quarter, flexible lastPrices
# parser (date-like numeric columns are ignored), and robust formatting.
#
# --------- USAGE ---------
# Put this script alongside: nav.csv, price_ownership.csv (optional),
# and lastPrices_Nav.txt (or edit LASTPRICES_TXT).
#
# Then run:
#   python build_nav_pack_with_freeze_v11.py
#
# Outputs:
#   nav_trailing.csv
#   discount_minus1w_latestnav.csv
#   HoldCo_NAV_Pack_PO_FROZEN.xlsx
#
from __future__ import annotations
import pandas as pd
import numpy as np
from pathlib import Path
import re

# ========= CONFIG =========
BASE = Path(".")
NAV_IN_PATH = BASE / "Outputs/nav_spot.csv"
NAV_TRAILING_PATH = BASE / "nav_trailing.csv"
PRICE_OWNERSHIP_CANDIDATES = [BASE / "Outputs/price_ownership.csv", BASE / "Outputs/prices_ownership.csv"]

LASTPRICES_TXT = BASE / "lastPrices_Nav_corrected.txt"   # or edit to lastPrices_Nav_corrected.txt
OUT_XLSX = BASE / "HoldCo_NAV_Pack_PO_FROZEN.xlsx"
SNAPSHOT_CSV = BASE / "discount_minus1w_latestnav.csv"
ROLLING_DAYS_1Y = 365
MAX_BACKTRACK_DAYS = 14

# Freeze map: start applying the freeze from the quarter AFTER this label.
FREEZE_MAP = {
    "QUINENCO": "2025Q2",
    "ANTARCHILE": "2025Q2",
    "ALMENDRAL": "2025Q2",
    "ORO_BLANCO": "2025Q3",
    "INVERCAP": "2025Q1",
    "IAM": "2025Q3",
}
FREEZE_COLS_ALL = ["HoldingValue","NetDebt","NAV","eqy_shares_total","NAV_PS"]


def _to_num(val):
    if pd.isna(val): return np.nan
    if isinstance(val, (int, float, np.number)): return float(val)
    s = str(val).strip()
    if s == "": return np.nan
    s = s.replace(" ", "")
    # european → dot
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(".", "").replace(",", ".")
    s = s.replace("’","").replace("'","")
    try:
        return float(s)
    except:
        return np.nan


def _load_nav_raw(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    if "Date_quarter" in df.columns:
        df["Date_quarter"] = df["Date_quarter"].astype(str)
    else:
        df["Date_quarter"] = df["Date"].dt.to_period("Q").astype(str)
    df["Holding"] = df["Holding"].astype(str).str.upper().str.replace("Ñ","N", regex=False)
    for c in ["HoldingValue","NetDebt","NAV","eqy_shares_total","NAV_PS","Price","Discount"]:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values(["Holding","Date"])


def _next_quarter_label(qstr: str) -> str | None:
    try:
        p = pd.Period(qstr, freq="Q"); return (p + 1).strftime("%YQ%q")
    except Exception:
        return None


def _apply_freeze(group: pd.DataFrame, freeze_q: str) -> pd.DataFrame:
    g = group.sort_values("Date").copy()
    if not freeze_q or str(freeze_q).strip()=="" or str(freeze_q).lower()=="nan": return g
    base = g[g["Date_quarter"] <= freeze_q].copy()
    if base.empty: return g
    base_row = base.sort_values("Date").tail(1).iloc[0]
    avail_cols = [c for c in FREEZE_COLS_ALL if c in g.columns]
    base_vals = {c: base_row.get(c, np.nan) for c in avail_cols}
    q_next = _next_quarter_label(freeze_q)
    if q_next is None: return g
    mask_after = g["Date_quarter"] >= q_next
    for c in avail_cols: g[f"{c}_orig"] = g[c]          # audit
    for c in avail_cols: g.loc[mask_after, c] = base_vals.get(c, np.nan)
    if {"HoldingValue","NetDebt"}.issubset(g.columns):
        g.loc[mask_after, "NAV"] = g.loc[mask_after, "HoldingValue"] - g.loc[mask_after, "NetDebt"]
    if {"NAV","eqy_shares_total"}.issubset(g.columns):
        with np.errstate(divide="ignore", invalid="ignore"):
            g.loc[mask_after, "NAV_PS"] = g.loc[mask_after, "NAV"] / g.loc[mask_after, "eqy_shares_total"]
    return g


def _recompute_discount_all(df: pd.DataFrame) -> pd.DataFrame:
    if {"Price","NAV_PS"}.issubset(df.columns):
        with np.errstate(divide="ignore", invalid="ignore"):
            disc = 1 - (df["Price"] / df["NAV_PS"])
            disc[~np.isfinite(disc)] = np.nan
        df["Discount"] = disc
    return df


def build_nav_trailing(nav_in: Path, nav_out: Path, freeze_map: dict) -> pd.DataFrame:
    nav = _load_nav_raw(nav_in)
    parts = []
    for h, grp in nav.groupby("Holding", sort=False):
        parts.append(_apply_freeze(grp, freeze_map.get(h, "")))
    nav_trailing = pd.concat(parts, ignore_index=True).sort_values(["Holding","Date"])
    nav_trailing = _recompute_discount_all(nav_trailing)
    nav_out.parent.mkdir(parents=True, exist_ok=True)
    nav_trailing.to_csv(nav_out, index=False, encoding="utf-8-sig")
    return nav_trailing


def _pick_price_ownership(cands):
    for p in cands:
        if p.exists(): return p
    return None


def _load_price_ownership(path: Path, nav_panel: pd.DataFrame) -> pd.DataFrame | None:
    if path is None: return None
    df = pd.read_csv(path)
    alias = {"holding":"Holding","HOLDING":"Holding","date":"Date","DATE":"Date",
             "id":"ID","ticker":"ID","Issuer":"ID",
             "pct":"pct_holding","pctHolding":"pct_holding","PCT_HOLDING":"pct_holding",
             "mktcap":"MarketCap","mcap":"MarketCap","MKTCAP":"MarketCap",
             "holdingvalue":"HoldingValue","Holding_Value":"HoldingValue","VALUE_LOOKTHROUGH":"HoldingValue"}
    for c in list(df.columns):
        if c in alias: df.rename(columns={c: alias[c]}, inplace=True)
    if "Holding" not in df.columns or "ID" not in df.columns: return None
    if "Date" in df.columns: df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    else: df["Date"] = pd.NaT
    df["Holding"] = df["Holding"].astype(str).str.upper().str.replace("Ñ","N", regex=False)
    for col in ["pct_holding","MarketCap","HoldingValue"]:
        if col in df.columns: df[col] = df[col].apply(_to_num)
    if "pct_holding" in df.columns and df["pct_holding"].dropna().median() > 1:
        df["pct_holding"] = df["pct_holding"] / 100.0
    if "HoldingValue" not in df.columns:
        if {"MarketCap","pct_holding"}.issubset(df.columns):
            df["HoldingValue"] = df["MarketCap"] * df["pct_holding"]
        else:
            df["HoldingValue"] = np.nan

    panel = nav_panel.copy()
    has_disc = panel.dropna(subset=["Discount"]) if "Discount" in panel.columns else panel
    last_disc = has_disc.groupby("Holding", as_index=False)["Date"].max().rename(columns={"Date":"NavLast"})
    last_any = panel.groupby("Holding", as_index=False)["Date"].max().rename(columns={"Date":"LastAny"})
    last = last_any.merge(last_disc, on="Holding", how="left"); last["UseDate"] = last["NavLast"].fillna(last["LastAny"])
    df = df.merge(last[["Holding","UseDate"]], on="Holding", how="right").rename(columns={"UseDate":"NavLast"})
    def pick_rows(g):
        tgt = g["NavLast"].iloc[0]
        sub = g[g["Date"] <= tgt].copy()
        if sub.empty: sub = g.copy()
        if sub.empty: return sub
        d_last = sub["Date"].max()
        return sub[sub["Date"] == d_last].copy()
    latest_rows = df.groupby("Holding", group_keys=False).apply(pick_rows)
    latest_rows = latest_rows.dropna(subset=["Holding"]).copy()
    if "HoldingValue" in latest_rows.columns:
        latest_rows["Weight"] = latest_rows.groupby("Holding")["HoldingValue"].transform(
            lambda s: s / s.sum() if pd.notnull(s).any() and float(s.sum()) != 0.0 else np.nan)
    else: latest_rows["Weight"] = np.nan
    try: latest_rows = latest_rows.sort_values(["Holding","HoldingValue"], ascending=[True, False])
    except Exception: latest_rows = latest_rows.sort_values(["Holding","ID"])
    return latest_rows


def _panel_and_latest(df: pd.DataFrame, days_1y: int):
    have_disc = df[(df["Price"].notna()) & (df["NAV_PS"].notna())] if {"Price","NAV_PS"}.issubset(df.columns) else \
                (df.dropna(subset=["Discount"]) if "Discount" in df.columns else df.copy())
    last_disc = have_disc.groupby("Holding", as_index=False)["Date"].max().rename(columns={"Date":"LastDate"})
    last_price = (df[df["Price"].notna()].groupby("Holding", as_index=False)["Date"].max()
                  .rename(columns={"Date":"LastPrice"})) if "Price" in df.columns else pd.DataFrame(columns=["Holding","LastPrice"])
    last_any = df.groupby("Holding", as_index=False)["Date"].max().rename(columns={"Date":"LastAny"})
    last = last_any.merge(last_price, on="Holding", how="left").merge(last_disc, on="Holding", how="left")
    last["UseDate"] = last["LastDate"].fillna(last["LastPrice"]).fillna(last["LastAny"])
    panel = df.merge(last[["Holding","UseDate"]], on="Holding", how="left").rename(columns={"UseDate":"LastDate"})
    panel["WindowStart"] = panel["LastDate"] - pd.Timedelta(days=days_1y)
    panel_1y = panel[(panel["Date"] >= panel["WindowStart"]) & (panel["Date"] <= panel["LastDate"])].copy()
    latest_rows = panel.sort_values("Date").groupby("Holding", as_index=False).tail(1)
    return panel_1y, latest_rows


def _mm(x): return x / 1e6 if pd.notnull(x) else np.nan


def _build_summary(latest_rows: pd.DataFrame, panel_1y: pd.DataFrame, snapshot_df: pd.DataFrame) -> pd.DataFrame:
    if "NAV" in latest_rows.columns: nav_vals = latest_rows["NAV"]
    elif {"HoldingValue","NetDebt"}.issubset(latest_rows.columns): nav_vals = latest_rows["HoldingValue"] - latest_rows["NetDebt"]
    else: nav_vals = pd.Series(np.nan, index=latest_rows.index)
    summary = pd.DataFrame({
        "Holding": latest_rows["Holding"],
        "Holding Value (CLP mm)": latest_rows["HoldingValue"].apply(_mm) if "HoldingValue" in latest_rows.columns else np.nan,
        "Net Debt (CLP mm)": latest_rows["NetDebt"].apply(_mm) if "NetDebt" in latest_rows.columns else np.nan,
        "NAV (CLP mm)": nav_vals.apply(_mm),
        "# Shares (mm)": (latest_rows["eqy_shares_total"]/1e6) if "eqy_shares_total" in latest_rows.columns else np.nan,
        "NAV per Share": latest_rows["NAV_PS"] if "NAV_PS" in latest_rows.columns else np.nan,
        "Discount Now": latest_rows["Discount"] if "Discount" in latest_rows.columns else np.nan,
    })
    # override Discount Now with snapshot values
    snap = snapshot_df[["Holding","Discount_AsOf","AsOfTarget"]].copy()
    summary = summary.merge(snap, on="Holding", how="left")
    summary["Discount Now"] = summary["Discount_AsOf"].combine_first(summary["Discount Now"])
    if "Discount" in panel_1y.columns:
        stats = (panel_1y.groupby("Holding")["Discount"].agg(Mean_1Y="mean", Std_1Y="std").reset_index())
        summary = summary.merge(stats, on="Holding", how="left")
        summary["1Y avg"] = summary["Mean_1Y"]; summary["Avg + 1 sd"] = summary["Mean_1Y"] + summary["Std_1Y"]; summary["Avg - 1 sd"] = summary["Mean_1Y"] - summary["Std_1Y"]
        summary = summary.drop(columns=["Mean_1Y","Std_1Y"])
    else:
        summary["1Y avg"] = np.nan; summary["Avg + 1 sd"] = np.nan; summary["Avg - 1 sd"] = np.nan
    cols = ["Holding","Holding Value (CLP mm)","Net Debt (CLP mm)","NAV (CLP mm)","# Shares (mm)","NAV per Share",
            "Discount Now","1Y avg","Avg + 1 sd","Avg - 1 sd","AsOfTarget"]
    return summary[cols].sort_values("Holding")


def _load_lastprices_txt(path: Path) -> pd.DataFrame | None:
    if not path.exists(): return None
    try:
        df = pd.read_csv(path, sep=None, engine="python")
    except Exception:
        df = pd.read_csv(path, delimiter=",", engine="python")
    cand_name = None
    for c in df.columns:
        if str(c).lower().strip() in {"holding","symbol","ticker","id","issuer","name"}:
            cand_name = c; break
    if cand_name is None: cand_name = df.columns[0]
    names = df[cand_name].astype(str).str.upper().str.replace("Ñ","N", regex=False)
    names = names.str.replace(r"\s+","_", regex=True).str.replace("ORO BLANCO","ORO_BLANCO")
    num_cols = [c for c in df.columns if c != cand_name]
    parsed = {c: df[c].apply(_to_num) for c in num_cols}
    best_col, best_score = None, -1
    for c in num_cols:
        s = parsed[c]; nn = s.notna().sum()
        if nn == 0: continue
        med = s.median()
        score = 0
        if 0.05 <= med <= 100000: score += 2
        if med >= 1e6: score -= 2
        frac_8d = (s.dropna().round().astype(int).astype(str).str.len()==8).mean()
        score -= frac_8d * 3
        score += nn/len(df)
        if score > best_score: best_col, best_score = c, score
    if best_col is None: return None
    date_col = None
    for c in df.columns:
        if str(c).lower().strip() in {"date","asof","pricedate","time"}:
            date_col = c; break
    out = pd.DataFrame({"Holding": names, "Price": parsed[best_col]})
    out["Date"] = pd.to_datetime(df[date_col], errors="coerce") if date_col else pd.Timestamp.today().normalize()
    out["Date"] = out["Date"].fillna(pd.Timestamp.today().normalize())
    return out.dropna(subset=["Holding","Price"]).reset_index(drop=True)


def compute_snapshot_minus1w_latestnav(df: pd.DataFrame, lastp: pd.DataFrame | None, max_back_days: int) -> pd.DataFrame:
    asof = (pd.Timestamp.today().normalize() - pd.Timedelta(days=7))
    rows = []
    last_map = {h: g.tail(1).iloc[0] for h, g in lastp.groupby("Holding")} if lastp is not None and not lastp.empty else {}
    for h, g in df.groupby("Holding"):
        g = g.sort_values("Date")
        navps_row = g.dropna(subset=["NAV_PS"]).tail(1)
        navps_val = float(navps_row["NAV_PS"].iloc[0]) if not navps_row.empty else np.nan
        navps_dt = navps_row["Date"].iloc[0] if not navps_row.empty else pd.NaT

        price_row = g[(g["Date"] <= asof) & (g["Price"].notna())].tail(1)
        src, ahead = None, False
        if not price_row.empty:
            px_val = float(price_row["Price"].iloc[0]); px_dt = price_row["Date"].iloc[0]
            age_days = (asof - px_dt).days
            src = "nav<=asof"
        else:
            price_row2 = g[g["Price"].notna()].tail(1)
            if not price_row2.empty:
                px_val = float(price_row2["Price"].iloc[0]); px_dt = price_row2["Date"].iloc[0]
                age_days = (asof - px_dt).days
                src = "nav_latest"
            else:
                px_val = np.nan; px_dt = pd.NaT; age_days = np.nan
        if (not np.isfinite(px_val)) or (pd.notna(age_days) and age_days > max_back_days):
            if h in last_map:
                r = last_map[h]
                px_val = float(r["Price"]); px_dt = r["Date"]
                ahead = pd.notna(px_dt) and px_dt > asof
                age_days = 0 if ahead else ((asof - px_dt).days if pd.notna(px_dt) else np.nan)
                src = "lastPrices_txt"

        stale = False; disc = np.nan
        if np.isfinite(px_val) and np.isfinite(navps_val) and navps_val != 0:
            disc_tmp = 1.0 - (px_val / navps_val)
            if src in ("nav<=asof","nav_latest") and pd.notna(age_days) and age_days > max_back_days:
                stale = True; disc = np.nan
            else:
                disc = disc_tmp

        rows.append({
            "Holding": h,
            "AsOfTarget": asof.date(),
            "PriceDateUsed": px_dt.date() if pd.notna(px_dt) else None,
            "Price_AsOf": px_val,
            "NAV_PS_LatestDate": navps_dt.date() if pd.notna(navps_dt) else None,
            "NAV_PS_Latest": navps_val,
            "PriceAgeDays": age_days,
            "PriceSource": src,
            "AheadOfTarget": ahead,
            "StalePrice": stale,
            "Discount_AsOf": disc
        })
    return pd.DataFrame(rows).sort_values("Holding")


def _write_pack_with_po(summary: pd.DataFrame,
                        panel_1y: pd.DataFrame,
                        by_asset_latest: pd.DataFrame | None,
                        snapshot_df: pd.DataFrame | None,
                        out_path: Path):
    import xlsxwriter
    with pd.ExcelWriter(out_path, engine="xlsxwriter", datetime_format="yyyy-mm-dd") as writer:
        wb = writer.book
        fmt_pct2 = wb.add_format({"num_format":"0.00%"})
        fmt_int  = wb.add_format({"num_format":"#,##0"})
        fmt_2d   = wb.add_format({"num_format":"#,##0.00"})
        fmt_head = wb.add_format({"bold":True, "bg_color":"#E8EEF7", "border":1})
        fmt_title= wb.add_format({"bold":True, "font_size":12})

        sh = "Summary"; summary.to_excel(writer, sheet_name=sh, index=False)
        ws = writer.sheets[sh]
        for j, c in enumerate(summary.columns):
            ws.write(0, j, c, fmt_head); ws.set_column(j, j, 18)
        for j, c in enumerate(summary.columns):
            if c in ["Discount Now","1Y avg","Avg + 1 sd","Avg - 1 sd"]: ws.set_column(j, j, 14, fmt_pct2)
            elif c in ["Holding Value (CLP mm)","Net Debt (CLP mm)","NAV (CLP mm)","# Shares (mm)"]: ws.set_column(j, j, 18, fmt_int)
            elif c in ["NAV per Share"]: ws.set_column(j, j, 16, fmt_2d)

        if snapshot_df is not None and not snapshot_df.empty:
            sh2 = "AsOf(-7d)"; snapshot_df.to_excel(writer, sheet_name=sh2, index=False)
            ws2 = writer.sheets[sh2]
            for j, c in enumerate(snapshot_df.columns): ws2.write(0, j, c, fmt_head)
            for j, c in enumerate(snapshot_df.columns):
                if c in {"Discount_AsOf"}: ws2.set_column(j, j, 14, fmt_pct2)
                elif c in {"Price_AsOf","NAV_PS_Latest"}: ws2.set_column(j, j, 14, fmt_2d)
                else: ws2.set_column(j, j, 18)

        snap_map = snapshot_df.set_index("Holding").to_dict("index") if snapshot_df is not None and not snapshot_df.empty else {}

        for h, g in panel_1y.groupby("Holding"):
            g = g.sort_values("Date")
            gl = g.tail(1).iloc[0]
            if "NAV" in g.columns and pd.notnull(gl.get("NAV")): nav_mm = (gl.get("NAV")/1e6)
            elif pd.notnull(gl.get("HoldingValue")) and pd.notnull(gl.get("NetDebt")): nav_mm = ((gl.get("HoldingValue")-gl.get("NetDebt"))/1e6)
            else: nav_mm = np.nan

            s = snap_map.get(h, {})
            disc_now = s.get("Discount_AsOf", np.nan)
            asof = s.get("AsOfTarget")
            pdate = s.get("PriceDateUsed")
            psrc  = s.get("PriceSource")

            gd = g[["Date","Discount"]].dropna().copy()
            if pd.notna(disc_now):
                asof_ts = pd.to_datetime(asof)
                if gd.empty or gd["Date"].max().date() != asof_ts.date():
                    gd = pd.concat([gd, pd.DataFrame({"Date":[asof_ts], "Discount":[disc_now]})], ignore_index=True)
                else:
                    idx = gd["Date"].idxmax()
                    gd.loc[idx, "Discount"] = disc_now
                gd = gd.sort_values("Date")

            sheet = h[:31]; ws = wb.add_worksheet(sheet); writer.sheets[sheet] = ws
            ws.write(0, 0, f"{h} — NAV Valuation (latest: {gl['Date'].date()})", fmt_title)
            ws.write(2, 0, "Metric", fmt_head); ws.write(2, 1, h, fmt_head)

            rows = [
                ("Holding Value (CLP mm)", (gl.get("HoldingValue", np.nan)/1e6)),
                ("Net Debt (CLP mm)", (gl.get("NetDebt", np.nan)/1e6)),
                ("NAV (CLP mm)", nav_mm),
                ("# Shares (mm)", (gl.get("eqy_shares_total", np.nan)/1e6)),
                ("NAV per Share", gl.get("NAV_PS", np.nan)),
                ("Discount Now", disc_now if pd.notna(disc_now) else (g["Discount"].iloc[-1] if "Discount" in g.columns else np.nan)),
                ("1Y avg", g["Discount"].mean() if "Discount" in g.columns else np.nan),
                ("Avg + 1 sd", (g["Discount"].mean() + g["Discount"].std(ddof=1)) if "Discount" in g.columns else np.nan),
                ("Avg - 1 sd", (g["Discount"].mean() - g["Discount"].std(ddof=1)) if "Discount" in g.columns else np.nan),
            ]
            for i, (k,v) in enumerate(rows, start=3):
                ws.write(i, 0, k)
                try:
                    vv = float(v)
                    if np.isfinite(vv): ws.write_number(i, 1, vv, fmt_2d if k in ["NAV per Share","# Shares (mm)"] else fmt_pct2 if "Discount" in k else None)
                    else: ws.write_blank(i, 1, None)
                except Exception:
                    ws.write_blank(i, 1, None)
            ws.set_column(0, 0, 24); ws.set_column(1, 1, 18)

            base_r = 3 + len(rows) + 1
            ws.write(base_r,   0, "Snapshot AsOf", fmt_head); ws.write(base_r,   1, str(asof) if asof else "")
            ws.write(base_r+1, 0, "PriceDateUsed", fmt_head); ws.write(base_r+1, 1, str(pdate) if pdate else "")
            ws.write(base_r+2, 0, "PriceSource",   fmt_head); ws.write(base_r+2, 1, str(psrc) if psrc else "")

            ts_start = base_r + 4
            ws.write(ts_start, 0, "Date", fmt_head); ws.write(ts_start, 1, "Discount (1Y)", fmt_head)
            if not gd.empty:
                for i, (dt, disc) in enumerate(zip(gd["Date"], gd["Discount"]), start=ts_start+1):
                    ws.write_datetime(i, 0, dt.to_pydatetime())
                    try:
                        if np.isfinite(float(disc)): ws.write_number(i, 1, float(disc), fmt_pct2)
                        else: ws.write_blank(i, 1, None, fmt_pct2)
                    except Exception:
                        ws.write_blank(i, 1, None, fmt_pct2)

                nrows = len(gd)
                if nrows > 2:
                    chart = wb.add_chart({"type":"line"})
                    chart.add_series({"name":"Discount (1Y)",
                                      "categories":[sheet, ts_start+1, 0, ts_start+nrows, 0],
                                      "values":[sheet, ts_start+1, 1, ts_start+nrows, 1]})
                    ttl = f"{h} — Discount (1Y)"
                    if asof: ttl += f" (AsOf {asof})"
                    chart.set_title({"name": ttl}); chart.set_y_axis({"num_format":"0%"})
                    ws.insert_chart(ts_start, 3, chart, {"x_scale":1.2, "y_scale":1.2})

        print(f"[WRITE] {out_path.resolve()}")


def main():
    if not NAV_IN_PATH.exists(): raise FileNotFoundError("nav.csv not found.")
    nav_trailing = build_nav_trailing(NAV_IN_PATH, NAV_TRAILING_PATH, FREEZE_MAP)
    panel_1y, latest_rows = _panel_and_latest(nav_trailing, ROLLING_DAYS_1Y)

    lastp = _load_lastprices_txt(LASTPRICES_TXT)
    snapshot_df = compute_snapshot_minus1w_latestnav(nav_trailing, lastp, MAX_BACKTRACK_DAYS)
    snapshot_df.to_csv(SNAPSHOT_CSV, index=False, encoding="utf-8-sig")

    summary = _build_summary(latest_rows, panel_1y, snapshot_df)

    po_path = _pick_price_ownership(PRICE_OWNERSHIP_CANDIDATES)
    by_asset_latest = _load_price_ownership(po_path, nav_trailing)

    _write_pack_with_po(summary, panel_1y, by_asset_latest, snapshot_df, OUT_XLSX)

    bad = snapshot_df[(snapshot_df["Price_AsOf"] > 1_000_000) | snapshot_df["Price_AsOf"].isna()]
    if not bad.empty:
        print("[CHECK] Suspicious snapshot prices:")
        print(bad[["Holding","Price_AsOf","PriceSource","PriceAgeDays"]].to_string(index=False))


if __name__ == "__main__":
    main()
