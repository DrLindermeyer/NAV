# quinenco_nav_ingest_min.py
# ------------------------------------------------------------
# Put in the same folder as files named like: 01-2018.xls, 02-2024.xlsx, etc.
# Assumptions (all files):
#   - NAV (MCh$) at cell I34
#   - NAV (MUSD) at cell J34  (optional; used only if present)
#   - Optional FX at C5 (only used if I34 missing and J34 present)
# Fixed shares outstanding for all periods:
FIXED_SHARES = 1_662_759_593  # Quiñenco historical shares

from pathlib import Path
import re
import numpy as np
import pandas as pd

PRINT_DEBUG = False

def _fxnum(x):
    if isinstance(x, (int, float)) and pd.notna(x): return float(x)
    s = str(x).strip().replace("\u202f","").replace("\xa0"," ").replace(" ","")
    if "." in s and "," in s: s = s.replace(".","").replace(",",".")
    elif "," in s and "." not in s: s = s.replace(",",".")
    try: return float(s)
    except: return np.nan

def _open_xl(p: Path):
    if p.suffix.lower() == ".xlsx":
        return pd.ExcelFile(p, engine="openpyxl")
    elif p.suffix.lower() == ".xls":
        return pd.ExcelFile(p, engine="xlrd")  # ensure xlrd installed for .xls
    return pd.ExcelFile(p)

def _cell(df, r1, c1):
    r = r1-1; c = c1-1
    if r < 0 or c < 0 or r >= df.shape[0] or c >= df.shape[1]:
        return np.nan
    return df.iat[r, c]

def _date_from_quarter_filename(stem: str) -> pd.Timestamp:
    # accepts 01-YYYY..04-YYYY
    m = re.search(r'(?P<q>0[1-4]|[1-4])[-_](?P<y>\d{4})', stem)
    if not m:
        raise ValueError(f"Filename '{stem}' must look like '01-2019' (Q-YYYY).")
    q = int(m.group('q')); y = int(m.group('y'))
    md = {1:(3,31), 2:(6,30), 3:(9,30), 4:(12,31)}[q]
    return pd.Timestamp(year=y, month=md[0], day=md[1])

def extract_one(path: Path) -> dict:
    xl = _open_xl(path)
    # pick largest sheet just in case
    sizes = {s: xl.parse(s, header=None).shape for s in xl.sheet_names}
    sheet = max(sizes, key=lambda k: sizes[k][0]*sizes[k][1])
    df = xl.parse(sheet, header=None)

    # Date from filename
    date = _date_from_quarter_filename(path.stem)

    # Read NAV (MCh$) and (MUSD)
    nav_mclp = _fxnum(_cell(df, 34, 9))   # I34
    nav_musd = _fxnum(_cell(df, 34,10))   # J34

    # Optional FX at C5, only used if I34 missing
    fx = _fxnum(_cell(df, 5, 3))
    if pd.isna(nav_mclp) and pd.notna(nav_musd) and pd.notna(fx):
        nav_mclp = nav_musd * fx

    # NAV per share (CLP)
    nav_ps = np.nan
    if pd.notna(nav_mclp) and FIXED_SHARES:
        nav_ps = (nav_mclp * 1_000_000.0) / FIXED_SHARES

    if PRINT_DEBUG:
        print(f"[{path.name}] date={date.date()} NAV_MCh$={nav_mclp} NAV_MUSD={nav_musd} NAV_PS={nav_ps}")

    return {
        "Date": pd.Timestamp(date).normalize(),
        "Date_quarter": pd.Period(pd.Timestamp(date), freq="Q"),
        "Holding": "QUINENCO",
        "Total_NAV_MCLP": nav_mclp,
        "Total_NAV_MUSD": nav_musd,
        "Shares_outstanding": FIXED_SHARES,
        "NAV_CLP_per_share": nav_ps,
        "SourceFile": path.name,
    }

def main():
    p = Path.cwd()
    files = sorted([f for f in p.iterdir() if f.suffix.lower() in (".xls",".xlsx")])
    rows = []
    for f in files:
        try:
            rows.append(extract_one(f))
        except Exception as e:
            rows.append({
                "Date": pd.NaT, "Date_quarter": pd.NaT, "Holding": "QUINENCO",
                "Total_NAV_MCLP": np.nan, "Total_NAV_MUSD": np.nan,
                "Shares_outstanding": FIXED_SHARES, "NAV_CLP_per_share": np.nan,
                "SourceFile": f.name, "Error": str(e),
            })
    df = pd.DataFrame(rows).sort_values(["Date","SourceFile"], na_position="last").reset_index(drop=True)
    df["Date_quarter"] = pd.PeriodIndex(pd.to_datetime(df["Date"], errors="coerce"), freq="Q")
    out = p / "quinenco_nav_processed.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"[DONE] Wrote {len(df)} rows → {out.name}")

if __name__ == "__main__":
    main()
