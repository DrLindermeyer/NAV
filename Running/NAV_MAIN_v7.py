
# -*- coding: utf-8 -*-
"""
Created on Tue Nov 11 18:02:46 2025

@author: Administrator
Only uses data from 2017 onwards.
"""
import os
from pathlib import Path
import pandas as pd
import numpy as np

# ====== CONFIG ======
BASE_DIR = "C:/Users/Administrator/Desktop/DS/Python/NAV_v2/Running"
OUTPUT_DIR = Path(r"C:/Users/Administrator/Desktop/DS/Python/NAV_v2/Running/Outputs")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def out(name: str) -> Path:
    """Build a full path inside OUTPUT_DIR."""
    return OUTPUT_DIR / name

def save_csv(df, filename, **kwargs):
    """Write CSV into OUTPUT_DIR with a friendly log."""
    p = out(filename)
    df.to_csv(p, **kwargs)
    print(f"[WRITE] {p}")

START_YEAR = 2017
START_DATE = pd.Timestamp(START_YEAR, 1, 1)
START_Q    = pd.Period(f"{START_YEAR}Q1")

FILES = {
    "prices_txt": "lastPrices_Nav_corrected.txt",
    "tickers": "tickers.csv",
    "bal_com": "balance_sheets_com_db.csv",
    "bal_hol": "balance_sheets_hol_db.csv",
    "fx": "usdclp.csv",
    "netdebt_guide": "balance_netdebt.csv",
    "ownership_hol": "ownership_data_hol_db.csv",
    "ownership_com": "ownership_data_com_db.csv",
    "id_holding_2": "id_holding_2.csv",
    "netdebt_external": "netdebt.csv",  # optional override
}

# =========================
# Path utilities
# =========================

def _get_base_dir():
    if BASE_DIR:
        return Path(BASE_DIR)
    try:
        return Path(__file__).parent.resolve()
    except NameError:
        return Path.cwd().resolve()

def resolve_path(name_or_path: str) -> Path:
    p = Path(name_or_path)
    if p.is_absolute() and p.exists():
        return p
    base = _get_base_dir()
    candidates = [base / name_or_path, Path.cwd() / name_or_path]
    for c in candidates:
        if c.exists():
            return c
    tried = [str(base / name_or_path), str(Path.cwd() / name_or_path)]
    msg = [
        f"✗ File not found: {name_or_path}",
        f"  - Script base: {base}",
        f"  - CWD        : {Path.cwd().resolve()}",
        f"  - Looked in  :",
        *[f"    • {t}" for t in tried]
    ]
    raise FileNotFoundError("\n".join(msg))

def debug_where():
    print(f"[DEBUG] script base: {_get_base_dir()}")
    print(f"[DEBUG] cwd        : {Path.cwd().resolve()}")

# =========================
# Helpers: Safe CSV reading
# =========================

def read_expected_csv(path, expected_cols, sep=",", dtype="str"):
    p = resolve_path(path)
    if dtype == "str":
        df = pd.read_csv(p, sep=sep, dtype=str)
    elif dtype is None:
        df = pd.read_csv(p, sep=sep)
    else:
        df = pd.read_csv(p, sep=sep, dtype=dtype)
    missing = [c for c in expected_cols if c not in df.columns]
    if missing:
        raise ValueError(f"{p}: missing required columns {missing}. Found: {list(df.columns)}")
    return df[expected_cols].copy()

# =========================
# 0) Prices
# =========================

def _try_read_prices(p: Path):
    names = ['TICKER','DATE','OPEN','HIGH','LOW','CLOSE','VOLUME']
    try:
        df = pd.read_csv(p, header=None, names=names)
        if df.shape[1] == 7:
            return df
    except Exception:
        pass
    try:
        df = pd.read_csv(p, header=None, names=names, sep=';')
        if df.shape[1] == 7:
            return df
    except Exception:
        pass
    try:
        df = pd.read_csv(p, header=None, names=names, delim_whitespace=True, engine="python")
        if df.shape[1] == 7:
            return df
    except Exception:
        pass
    return None

def load_prices(prices_path="lastPrices_Nav.txt"):
    p = resolve_path(prices_path)
    df = _try_read_prices(p)
    if df is None:
        first = ""
        try:
            first = Path(p).open("r", encoding="utf-8", errors="ignore").readline().rstrip("\n")
        except Exception:
            pass
        raise ValueError(
            f"Could not parse {p} into 7 columns.\n"
            f"Preview first line: {first!r}\n"
            f"Tip: open the file to check the delimiter; set a fixed sep in _try_read_prices if needed."
        )
    df = df[df['DATE'] != 'DATE.trans(-)']
    df['DATE'] = pd.to_datetime(df['DATE'], format='%Y%m%d', errors='coerce')
    df = df.dropna(subset=['DATE'])
    # >>> filter from 2017 onwards
    df = df[df['DATE'] >= START_DATE].copy()
    for c in ['OPEN','HIGH','LOW','CLOSE','VOLUME']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df

def fill_missing_dates(data):
    pc = data[['TICKER','DATE','CLOSE']].copy()
    wide = pc.pivot(index='DATE', columns='TICKER', values='CLOSE').reset_index()
    wide.columns.name = None
    all_days = pd.date_range(start=pc['DATE'].min(), end=pc['DATE'].max(), freq='D')
    full = pd.DataFrame({'DATE': all_days}).merge(wide, on='DATE', how='left')
    full.ffill(inplace=True)
    long = full.melt(id_vars=['DATE'], var_name='TICKER', value_name='CLOSE')
    return long

# =========================
# 1) Run prices processing
# =========================

debug_where()

data = load_prices(FILES["prices_txt"])
trading_dates = data['DATE'].sort_values().drop_duplicates().reset_index(drop=True)
prices = fill_missing_dates(data)
prices['Date_quarter'] = prices['DATE'].dt.to_period('Q')
prices = prices.rename(columns={'DATE':'Date','TICKER':'ID','CLOSE':'Price'})

# =========================
# 2) Load reference inputs
# =========================

# Tickers
TICKER_COLS = ['ID','RUT','IS_HOLDING','GROUP']
tickers = read_expected_csv(FILES["tickers"], TICKER_COLS, sep=',')

# Balance Sheets (company + holding)
BAL_COLS = ['Description','Value','Curncy','Date','ID']
balance_comp = read_expected_csv(FILES["bal_com"], BAL_COLS)
balance_hol  = read_expected_csv(FILES["bal_hol"], BAL_COLS)
balance = pd.concat([balance_comp, balance_hol], ignore_index=True)

# Parse balance sheet values
balance['Date'] = pd.to_datetime(balance['Date'], errors='coerce')
balance = balance.dropna(subset=['Date'])
# >>> filter from 2017 onwards
balance = balance[balance['Date'] >= START_DATE].copy()

balance['Value'] = balance['Value'].replace('-', '0')
balance['Value'] = (balance['Value']
                    .str.replace('.', '', regex=False)
                    .str.replace(',', '.', regex=False))
balance['Value'] = pd.to_numeric(balance['Value'], errors='coerce')

# FX
FX_COLS = ['Date', 'USDCLP']
fx = read_expected_csv(FILES["fx"], FX_COLS, sep=',')
fx['Date'] = pd.to_datetime(fx['Date'], errors='coerce')
fx = fx.dropna(subset=['Date'])
# >>> filter from 2017 onwards
fx = fx[fx['Date'] >= START_DATE].copy()

fx['USDCLP'] = (fx['USDCLP'].astype(str).str.replace(',', '', regex=False))
fx['USDCLP'] = pd.to_numeric(fx['USDCLP'], errors='coerce')

# NetDebt guide
netdebt_guide = read_expected_csv(
    FILES["netdebt_guide"],
    expected_cols=['ID','Description','Balance2','Holding'],
    sep=','  # it's comma-separated
)

id_holding = netdebt_guide[['Holding','ID']].drop_duplicates().copy()

# Map prices to holdings
prices_final = prices.merge(id_holding, on='ID', how='inner')

# =========================
# 3) Currency fix and balance_total
# =========================

balance.loc[balance['ID'] == 'ANTARCHILE', 'Curncy'] = 'USD'

balance_usd = balance[balance['Curncy'] == 'USD'].copy()
balance_cl  = balance[balance['Curncy'] != 'USD'].copy()

balance_usd = balance_usd.merge(fx, on='Date', how='inner')
balance_usd['Value'] = balance_usd['Value'] * balance_usd['USDCLP']
balance_usd = balance_usd.drop(columns=['USDCLP'])

balance_total = pd.concat([balance_cl, balance_usd], ignore_index=True)
balance_total['Value'] = balance_total['Value'] / 1000.0
balance_total = balance_total.drop(columns=['Curncy'], errors='ignore')
# >>> balance_total is already 2017+ because inputs were filtered
save_csv(balance_total, 'balance_total.csv', index=False, encoding='utf-8-sig')
# =========================
# 4) Select only NetDebt-related lines
# =========================

balance_sel = (balance_total
               .merge(netdebt_guide, on=['ID','Description'], how='inner')
               .merge(tickers, on='ID', how='inner')
               [['Date','ID','Holding','IS_HOLDING','Description','Balance2','Value']])

save_csv(balance_sel, 'balance_selected_netdebt.csv', index=False, encoding='utf-8-sig')

balance_sel['Value'] = np.where(balance_sel['IS_HOLDING'].astype(float) == -1,
                                -balance_sel['Value'], balance_sel['Value'])

balance_group = (balance_sel
                 .groupby(['ID','Date','Balance2'], as_index=False)['Value']
                 .sum())
save_csv(balance_group, 'balance_netdebt_comp_hol.csv', index=False, encoding='utf-8-sig')

# latest consolidation date by quarter for holdings
id_date_max = (balance_group[['ID','Date']].drop_duplicates()
               .merge(tickers, on='ID', how='left')[['Date','ID','IS_HOLDING']]
               .drop_duplicates())
hol_date_max = id_date_max[id_date_max['IS_HOLDING'].astype(float) == 1][['ID','Date']].copy()
hol_date_max['Date_quarter'] = hol_date_max['Date'].dt.to_period('Q')
hol_date_max = hol_date_max.drop(columns=['Date'])
hol_date_max.columns = ['Holding','Date_quarter']
# >>> hol_date_max is derived from 2017+ sources
save_csv(hol_date_max, 'Holding_Max_Report.csv', index=False, encoding='utf-8-sig')
# Aggregate to holding level by Balance2, then compute NetDebt
balance_group_2 = (balance_group
                   .merge(id_holding, on='ID', how='left')
                   .groupby(['Date','Holding','Balance2'], as_index=False)['Value']
                   .sum())
balance_group_2['NetDebt'] = np.where(balance_group_2['Balance2'] == 'Cash',
                                      -balance_group_2['Value'], balance_group_2['Value'])

netdebt = (balance_group_2.groupby(['Date','Holding'], as_index=False)['NetDebt']
           .sum())
netdebt['Date_quarter'] = netdebt['Date'].dt.to_period('Q')
netdebt = netdebt.drop(columns=['Date'])
# >>> netdebt is 2017+ because balance inputs were 2017+

# --- external override (optional) ---
ext_path = None
try:
    ext_path = resolve_path(FILES["netdebt_external"])
except FileNotFoundError:
    ext_path = None

if ext_path:
    ext = pd.read_csv(ext_path, sep=';')
    need_cols = {'Holding', 'Date_quarter', 'NetDebt'}
    if not need_cols.issubset(ext.columns):
        print(f"[WARN] {ext_path} missing columns {need_cols - set(ext.columns)} — ignoring external override.")
        netdebt_corrected_dt = netdebt.copy()
    else:
        ext['NetDebt'] = (ext['NetDebt'].astype(str)
                          .str.replace(',', '', regex=True)
                          .str.strip())
        ext['NetDebt'] = pd.to_numeric(ext['NetDebt'], errors='coerce')
        ext['Date_quarter'] = pd.PeriodIndex(ext['Date_quarter'], freq='Q')
        # >>> keep only 2017+
        ext = ext[ext['Date_quarter'] >= START_Q].copy()
        netdebt_corrected_dt = ext[['Holding','Date_quarter','NetDebt']].copy()
else:
    netdebt_corrected_dt = netdebt.copy()

# Ensure Period dtype and choose start date
if not str(netdebt_corrected_dt['Date_quarter'].dtype).startswith('period'):
    netdebt_corrected_dt['Date_quarter'] = pd.PeriodIndex(netdebt_corrected_dt['Date_quarter'], freq='Q')

# >>> also filter computed netdebt to 2017+
netdebt_corrected_dt = netdebt_corrected_dt[netdebt_corrected_dt['Date_quarter'] >= START_Q].copy()

valid_q = netdebt_corrected_dt.loc[netdebt_corrected_dt['NetDebt'].notna(), 'Date_quarter']

if valid_q.empty:
    print("[WARN] No non-null NetDebt found. Using earliest price date as start.")
    min_date_final = prices['Date'].min()
else:
    min_date_final = valid_q.min().start_time

# =========================
# 5) Forward-fill NetDebt to trading dates (per holding)
# =========================

dates_holding = prices_final[['Date','Holding']].copy()
dates_holding['Date_quarter'] = dates_holding['Date'].dt.to_period('Q')
dates_holding_final = dates_holding[dates_holding['Date'] >= min_date_final].copy()

netdebt_final_dt = dates_holding_final.merge(netdebt_corrected_dt,
                                             on=['Holding','Date_quarter'],
                                             how='left')
netdebt_final_dt['NetDebt'] = netdebt_final_dt.groupby('Holding')['NetDebt'].ffill()
netdebt_final_dt = netdebt_final_dt.drop_duplicates().reset_index(drop=True)

# =========================
# 6) Ownership (holdings → only share count)
# =========================

OWN_COLS = ['Holding','pct_holding','eqy_shares','eqy_shares_total','Date','ID']

ownership_hol = read_expected_csv(FILES["ownership_hol"], OWN_COLS, sep=',')
ownership_hol['Date'] = pd.to_datetime(ownership_hol['Date'], errors='coerce')
ownership_hol = ownership_hol.dropna(subset=['Date'])
# >>> filter from 2017 onwards
ownership_hol = ownership_hol[ownership_hol['Date'] >= START_DATE].copy()

ownership_hol['Date_quarter'] = ownership_hol['Date'].dt.to_period('Q')

holding_shares = ownership_hol[['ID','Date_quarter','eqy_shares_total']].copy()
holding_shares['eqy_shares_total'] = pd.to_numeric(holding_shares['eqy_shares_total'], errors='coerce') / 1_000_000
holding_shares = holding_shares.drop_duplicates()
holding_shares.columns = ['Holding','Date_quarter','eqy_shares_total']

holding_shares_dt = dates_holding.merge(holding_shares, on=['Date_quarter','Holding'], how='left')
holding_shares_final = holding_shares_dt[holding_shares_dt['Date'] >= min_date_final].copy()
holding_shares_final['eqy_shares_total'] = holding_shares_final.groupby('Holding')['eqy_shares_total'].ffill()
holding_shares_final = holding_shares_final.drop_duplicates().reset_index(drop=True)

# =========================
# 7) Ownership (subsidiaries → % holdings)
# =========================

ownership_com = read_expected_csv(FILES["ownership_com"], OWN_COLS, sep=',')
ownership_com['Date'] = pd.to_datetime(ownership_com['Date'], errors='coerce')
ownership_com = ownership_com.dropna(subset=['Date'])
# >>> filter from 2017 onwards
ownership_com = ownership_com[ownership_com['Date'] >= START_DATE].copy()

holdings_dict = {
    'INV ALTEL LTDA': 'ALMENDRAL',
    'ALMENDRAL S A': 'ALMENDRAL',
    'INV AGUAS METROPOLITANAS S A': 'IAM',
    'INVERCAP S.A.': 'INVERCAP',
    'INVERCAP SA': 'INVERCAP',
    'ANTARCHILE S.A.': 'ANTARCHILE',
}

holdings_dict.update({
    'SOCIEDAD DE INVERSIONES PAMPA CALICHERA SA': 'PAMPA_CALICHERA',
    'PAMPA CALICHERA S.A.': 'PAMPA_CALICHERA',   # <- was 'PAMPA _CALICHERA'
    'GLOBAL MINING SPA': 'GLOBAL_MINING',
    'SOCIEDAD DE INVERSIONES ORO BLANCO SA': 'ORO_BLANCO',
    'ORO BLANCO S.A.': 'ORO_BLANCO',
    'POTASIOS DE CHILE SA': 'POTASIOS',
    'POTASIOS S.A.': 'POTASIOS',
})

ownership_com = ownership_com[ownership_com['Holding'].isin(holdings_dict.keys())].copy()
ownership_com['Holding'] = ownership_com['Holding'].map(holdings_dict)

ownership_com = (ownership_com
                 .merge(tickers, on='ID', how='inner')
                 [['Date','ID','Holding','pct_holding','eqy_shares_total']])
ownership_com['eqy_shares_total'] = pd.to_numeric(ownership_com['eqy_shares_total'], errors='coerce') / 1_000_000
ownership_com['Date_quarter'] = ownership_com['Date'].dt.to_period('Q')
ownership_com = ownership_com.drop(columns=['Date'])

# --- Look-through ORO_BLANCO -> SQM (company-level, A+B weighted) ---

own = ownership_com.copy()  # cols: Date_quarter, ID, Holding, pct_holding, eqy_shares_total
own['Date_quarter'] = own['Date_quarter'].astype(str)

def pick_series_pct(holder: str, issuer: str):
    s = (own[(own['Holding']==holder) & (own['ID']==issuer)]
           [['Date_quarter','pct_holding']]
           .drop_duplicates('Date_quarter')
           .set_index('Date_quarter')['pct_holding']
           .astype(float))
    # convert percent -> fraction if needed
    if s.dropna().median() > 1:
        s = s / 100.0
    return s

# 1) Series-level % for each relevant holder
pampa_A  = pick_series_pct('PAMPA_CALICHERA','SQM-A')
pampa_B  = pick_series_pct('PAMPA_CALICHERA','SQM-B')
global_A = pick_series_pct('GLOBAL_MINING','SQM-A')
global_B = pick_series_pct('GLOBAL_MINING','SQM-B')
obl_A    = pick_series_pct('ORO_BLANCO','SQM-A')
obl_B    = pick_series_pct('ORO_BLANCO','SQM-B')

# 2) Series shares by quarter (use your existing override/ffill machinery)
def series_shares_quarterly(id_):
    # Prefer overrides; fall back to scraped eqy_shares_total
    ovr = pd.read_csv('series_shares_overrides.csv', dtype={'ID':str,'Date_quarter':str,'eqy_shares_total':float})
    ovrs = (ovr[ovr['ID']==id_].set_index('Date_quarter')['eqy_shares_total'])
    scr  = (own[own['ID']==id_]
              .dropna(subset=['eqy_shares_total'])
              .groupby('Date_quarter', as_index=True)['eqy_shares_total']
              .max())
    s = pd.concat([ovrs, scr[~scr.index.isin(ovrs.index)]], axis=0).sort_index()
    # expand to all quarters present in ownership
    q_all = own['Date_quarter'].drop_duplicates().sort_values().tolist()
    return s.reindex(q_all).ffill()

sh_A = series_shares_quarterly('SQM-A')
sh_B = series_shares_quarterly('SQM-B')
tot_sh = (sh_A + sh_B).replace(0, np.nan)  # guard

# 3) Convert series % -> company % by share-count weighting
def weighted_company_pct(pct_A, pct_B):
    sA = pct_A.reindex(tot_sh.index).fillna(0.0)
    sB = pct_B.reindex(tot_sh.index).fillna(0.0)
    num = (sA*sh_A).fillna(0.0) + (sB*sh_B).fillna(0.0)
    return (num / tot_sh).fillna(0.0)

pampa_total  = weighted_company_pct(pampa_A,  pampa_B)
global_total = weighted_company_pct(global_A, global_B)
obl_dir_tot  = weighted_company_pct(obl_A,    obl_B)

# 4) OBL -> Pampa link (as before)
obl_pampa = (own[(own['Holding']=='ORO_BLANCO') & (own['ID']=='PAMPA_CALICHERA')]
               [['Date_quarter','pct_holding']]
               .drop_duplicates('Date_quarter')
               .set_index('Date_quarter')['pct_holding']
               .astype(float)
               .reindex(tot_sh.index).fillna(method='ffill').fillna(0.0))


# Fallback: if OBL->Pampa is missing or suspiciously low, enforce a constant look-through
DEFAULT_OBL_TO_PAMPA = 0.8864  # TODO: replace with audited OBL % in Pampa
if (obl_pampa.dropna().abs().median() < 0.01) or (obl_pampa.dropna().max() < 0.05):
    print("[WARN] OBL->PAMPA_CALICHERA missing/too small; applying fallback", DEFAULT_OBL_TO_PAMPA)
    obl_pampa = pd.Series(DEFAULT_OBL_TO_PAMPA, index=tot_sh.index)
if obl_pampa.dropna().median() > 1:
    obl_pampa = obl_pampa / 100.0
# 5) Hard rule: Pampa owns 100% of Global Mining (so add Global’s company %)
pampa_plus_global_company = (pampa_total.reindex(tot_sh.index).fillna(0.0) +
                             global_total.reindex(tot_sh.index).fillna(0.0))

# 6) Effective company-level OBL -> SQM %
eff_obl_company_pct = (obl_pampa * pampa_plus_global_company) + obl_dir_tot
eff_obl_company_pct.name = 'pct_holding'
print('[CHK] OBL effective company % (last 4Q):', eff_obl_company_pct.tail(4).round(6).tolist())

# 7) Upsert into ownership_com as a synthetic company-level edge ORO_BLANCO -> SQM-B
# (we’ll attach the total company % to the SQM-B line; SQM-A will be ignored later)
eff_df = eff_obl_company_pct.reset_index().rename(columns={'Date_quarter':'Date_quarter'})
eff_df['Holding'] = 'ORO_BLANCO'
eff_df['ID'] = 'SQM-B'   # single sink; we'll value total cap on SQM-B only
eff_df['eqy_shares_total'] = np.nan

# Fill eqy_shares_total for the issuer row by mapping SQM-B shares (not used in total cap override, but keeps schema consistent)
issuer_shares = (own[own['ID']=='SQM-B']
                 .dropna(subset=['eqy_shares_total'])
                 .groupby('Date_quarter', as_index=True)['eqy_shares_total']
                 .max())
eff_df['eqy_shares_total'] = eff_df['Date_quarter'].map(issuer_shares)

# Clean & insert
ownership_com = ownership_com[~((ownership_com['Holding']=='ORO_BLANCO') & (ownership_com['ID'].isin(['SQM-A','SQM-B'])))]
ownership_com = pd.concat([ownership_com, eff_df], ignore_index=True)



# Extra ID→Holding mapping
id_holding_2 = read_expected_csv(FILES["id_holding_2"], expected_cols=['ID','Holding'], sep=';')

def process_ownership_data(prices_df, mapping_df, ownership_df):
    # 🔧 normalize Date_quarter dtype on both sides to Period(Q)
    if not str(prices_df['Date_quarter'].dtype).startswith('period'):
        prices_df['Date_quarter'] = pd.PeriodIndex(prices_df['Date_quarter'], freq='Q')
    if not str(ownership_df['Date_quarter'].dtype).startswith('period'):
        ownership_df['Date_quarter'] = pd.PeriodIndex(ownership_df['Date_quarter'], freq='Q')

    prices_final_local = prices_df.merge(mapping_df, on='ID', how='inner')
    holdings = mapping_df['Holding'].unique()
    prices_final_local = prices_final_local[~prices_final_local['ID'].isin(holdings)].copy()
    cols_keep = ['Date','Date_quarter','ID','Holding','pct_holding','Price','eqy_shares_total']
    merged = (prices_final_local
              .merge(ownership_df, on=['ID','Holding','Date_quarter'], how='left'))[cols_keep]
    min_date = merged.loc[merged['pct_holding'].notna(), 'Date'].min()
    if pd.notnull(min_date):
        filtered = merged[merged['Date'] >= min_date].copy()
    else:
        filtered = merged.copy()
    filtered['pct_holding'] = filtered.groupby('ID')['pct_holding'].ffill()
    filtered['eqy_shares_total'] = filtered.groupby('ID')['eqy_shares_total'].ffill()
    filtered = filtered.reset_index(drop=True)
    return filtered

price_ownership = process_ownership_data(prices, id_holding_2, ownership_com)
save_csv(price_ownership, 'price_ownership.csv', index=False, encoding='utf-8-sig')

price_ownership['MarketCap'] = price_ownership['Price'] * price_ownership['eqy_shares_total']
price_ownership['pct_holding'] = pd.to_numeric(price_ownership['pct_holding'], errors='coerce')
price_ownership['HoldingValue'] = price_ownership['MarketCap'] * price_ownership['pct_holding']
save_csv(price_ownership, 'info_prices_ownership.csv', index=False, encoding='utf-8-sig')

# ========== SQM dual-class market cap (retrospective, override-friendly) ==========

OVR_PATH = Path("series_shares_overrides.csv")

def load_series_shares_overrides():
    if not OVR_PATH.exists():
        return pd.DataFrame(columns=["ID","Date_quarter","eqy_shares_total"])
    ovr = pd.read_csv(OVR_PATH, dtype={"ID":str, "Date_quarter":str, "eqy_shares_total":float})
    ovr = ovr.dropna(subset=["ID","Date_quarter","eqy_shares_total"]).drop_duplicates(["ID","Date_quarter"], keep="last")
    return ovr

def _to_quarter_str_index(s: pd.Series) -> pd.Series:
    s = s.copy()
    # unify PeriodIndex / object to plain string like "2025Q2"
    if isinstance(s.index, pd.PeriodIndex):
        s.index = s.index.astype(str)
    else:
        s.index = s.index.astype(str)
    return s

def series_shares_quarterly(id_):
    """
    Quarterly eqy_shares_total for a series (SQM-A / SQM-B), preferring overrides,
    falling back to scraped 'ownership_com', with unified string quarters.
    """
    # 1) Overrides
    ovr = load_series_shares_overrides()
    ovrs = (ovr[ovr["ID"]==id_]
              .set_index("Date_quarter")["eqy_shares_total"])
    ovrs = _to_quarter_str_index(ovrs).sort_index()

    # 2) Scraped fallback
    scr = (ownership_com[ownership_com["ID"]==id_]
             .dropna(subset=["eqy_shares_total"])
             .groupby("Date_quarter", as_index=True)["eqy_shares_total"]
             .max())
    scr = _to_quarter_str_index(scr).sort_index()

    # 3) Combine & forward-fill across all quarters seen in price_ownership
    s = pd.concat([ovrs, scr[~scr.index.isin(ovrs.index)]], axis=0)
    q_all = (price_ownership["Date_quarter"].astype(str).drop_duplicates().sort_values().tolist())
    s = s.reindex(q_all).ffill()
    s.name = id_
    return s

def daily_series_from_quarterly(q_series: pd.Series) -> pd.Series:
    date_q = (price_ownership[["Date","Date_quarter"]]
              .drop_duplicates())
    date_q["Date_quarter"] = date_q["Date_quarter"].astype(str)
    date_q = date_q.set_index("Date")["Date_quarter"]
    return date_q.map(q_series).astype(float)

# 1) Build daily shares and price for each class
shares_A_q = series_shares_quarterly("SQM-A")
shares_B_q = series_shares_quarterly("SQM-B")

shares_A_d = daily_series_from_quarterly(shares_A_q)
shares_B_d = daily_series_from_quarterly(shares_B_q)

def price_series(ticker):
    s = (price_ownership[price_ownership["ID"]==ticker]
           .set_index("Date")["Price"]
           .sort_index())
    all_dates = price_ownership["Date"].drop_duplicates().sort_values()
    return s.reindex(all_dates).ffill()

px_A = price_series("SQM-A")
px_B = price_series("SQM-B")

# Total company market cap
mkcap_sqm_total = (px_A * shares_A_d).fillna(0) + (px_B * shares_B_d).fillna(0)

is_a = price_ownership['ID'].eq('SQM-A')
is_b = price_ownership['ID'].eq('SQM-B')
is_sqm = is_a | is_b

price_ownership['MarketCap_default'] = price_ownership['Price'] * price_ownership['eqy_shares_total']

# assign total only to SQM-B; zero SQM-A
price_ownership.loc[is_b, 'MarketCap'] = price_ownership.loc[is_b, 'Date'].map(mkcap_sqm_total)
price_ownership.loc[is_a, 'MarketCap'] = 0.0
price_ownership.loc[~is_sqm, 'MarketCap'] = price_ownership.loc[~is_sqm, 'MarketCap_default']

price_ownership['HoldingValue'] = price_ownership['pct_holding'].astype(float) * price_ownership['MarketCap'].astype(float)



# Optional diagnostics
if mkcap_sqm_total.isna().any():
    missing_dates = mkcap_sqm_total[mkcap_sqm_total.isna()].index[:5]
    print(f"[WARN] SQM MKCap missing on {len(mkcap_sqm_total.isna())} dates. First few: {list(missing_dates)}")


prices_ownership = (price_ownership
                    .groupby(['Date','Date_quarter','Holding'], as_index=False)['HoldingValue']
                    .sum()
                    ).drop_duplicates()
save_csv(prices_ownership, 'prices_ownership.csv', index=False, encoding='utf-8-sig')

# =========================
# 8) NAV computation → aligned + spot
# =========================

holdings = id_holding['Holding'].unique()
prices_holding = prices_final[prices_final['ID'].isin(holdings)][['Date','Date_quarter','Holding','Price']]

# Base NAV (daily dates joined to quarterly NetDebt)
nav_base = (prices_ownership
            .merge(netdebt_final_dt, on=['Holding','Date','Date_quarter'], how='inner')
            [['Date','Date_quarter','Holding','HoldingValue','NetDebt']])

# Attach quarterly share count (ffilled earlier to daily)
nav_base = (nav_base
            .merge(holding_shares_final, on=['Holding','Date','Date_quarter'], how='inner'))

# -------- Aligned series (clean history) --------
# Last quarter with BOTH NetDebt and (reported) shares per holding
last_q_nd = (netdebt_corrected_dt.dropna(subset=['NetDebt'])
             .groupby('Holding')['Date_quarter'].max())

# Use the quarterly "holding_shares" (not the daily ffilled) to get last reported shares quarter
# (created earlier from ownership_hol)
last_q_sh = (holding_shares
             .dropna(subset=['eqy_shares_total'])
             .groupby('Holding')['Date_quarter'].max())

# Intersection per holding
last_q_ok = {}
for h in pd.unique(holding_shares['Holding']):
    q_nd = last_q_nd.get(h, pd.Period('1900Q1'))
    q_sh = last_q_sh.get(h, pd.Period('1900Q1'))
    last_q_ok[h] = min(q_nd, q_sh)

nav_aligned = nav_base.copy()

# --- ORO_BLANCO shares override (audited, thousands normalized) ---
OBL_SHARES_OVERRIDE = 212_511_108  # If EEFF reports in thousands, this is the correct unit
if 'eqy_shares_total' in nav_aligned.columns:
    mask_obl = nav_aligned['Holding'].eq('ORO_BLANCO')
    nav_aligned.loc[mask_obl, 'eqy_shares_total'] = OBL_SHARES_OVERRIDE
    # Recompute NAV per share and Discount
    if {'NAV','eqy_shares_total'}.issubset(nav_aligned.columns):
        nav_aligned['NAV_PS'] = nav_aligned['NAV'] / nav_aligned['eqy_shares_total']
        if 'Price' in nav_aligned.columns:
            nav_aligned['Discount'] = 1 - (nav_aligned['Price'] / nav_aligned['NAV_PS']).replace([float('inf'), -float('inf')], float('nan'))
# keep rows only up to last_q_ok for each holding
mask_aligned = nav_aligned.apply(
    lambda r: r['Date_quarter'] <= last_q_ok.get(r['Holding'], pd.Period('1900Q1')),
    axis=1
)
nav_aligned = nav_aligned[mask_aligned].copy()

# Compute NAV metrics
nav_aligned['NAV'] = nav_aligned['HoldingValue'] - nav_aligned['NetDebt']
nav_aligned['NAV_PS'] = nav_aligned['NAV'] / nav_aligned['eqy_shares_total']

nav_aligned = (nav_aligned
               .merge(prices_holding, on=['Holding','Date','Date_quarter'], how='left'))
nav_aligned['Discount'] = ((nav_aligned['Price'] / nav_aligned['NAV_PS']) - 1) * -1

nav_aligned = (nav_aligned
               .sort_values(['Holding','Date'])
               .query('Date in @trading_dates')
               .reset_index(drop=True))

# -------- Spot series (live) with staleness guard --------
# Allow “spot” only up to MAX_STALENESS_D after quarter end;
# beyond that, mask (to avoid fake spikes when results lag)
MAX_STALENESS_D = 90

nav_spot = nav_base.copy()
nav_spot['NAV'] = nav_spot['HoldingValue'] - nav_spot['NetDebt']
nav_spot['NAV_PS'] = nav_spot['NAV'] / nav_spot['eqy_shares_total']
nav_spot = (nav_spot
            .merge(prices_holding, on=['Holding','Date','Date_quarter'], how='left'))

# flag stale rows
q_end_dt = nav_spot['Date_quarter'].dt.end_time
nav_spot['stale'] = (nav_spot['Date'] - q_end_dt).dt.days > MAX_STALENESS_D

# compute Discount, then mask stale
nav_spot['Discount'] = ((nav_spot['Price'] / nav_spot['NAV_PS']) - 1) * -1
nav_spot.loc[nav_spot['stale'], ['NAV','NAV_PS','Discount']] = np.nan

nav_spot = (nav_spot
            .sort_values(['Holding','Date'])
            .query('Date in @trading_dates')
            .reset_index(drop=True))

# =========================
# ADDON (REPLACE): Include QUINENCO via publisher NAV (per-share, CLP)
# =========================
# =========================
# HOTFIX 2023+: QUINENCO via publisher NAV (rebuild & guard)
# =========================
try:
    q_path = resolve_path("quinenco_nav_processed.csv")
    qraw = pd.read_csv(q_path)

    # --- normalize ---
    qraw['Date'] = pd.to_datetime(qraw['Date'], errors='coerce')
    qraw = qraw.dropna(subset=['Date'])
    qraw = qraw[qraw['Date'] >= START_DATE].copy()
    qraw['Date_quarter'] = pd.PeriodIndex(qraw['Date'], freq='Q')
    qraw['Holding'] = 'QUINENCO'

    # make sure numerics are numeric
    for c in ['NAV_CLP_per_share','Total_NAV_MCLP','Shares_outstanding','Price_CLP','MCAP_MCLP']:
        if c in qraw.columns:
            qraw[c] = pd.to_numeric(qraw[c], errors='coerce')

    # ---------- build one clean row per quarter ----------
    # Priority for Shares:
    #   1) Shares_outstanding if present
    #   2) Derive Shares = MCAP_MCLP * 1e6 / Price_CLP
    # Priority for NAV_PS (CLP/share):
    #   1) NAV_CLP_per_share if present
    #   2) Derive NAV_PS = Total_NAV_MCLP * 1e6 / Shares
    def _quarter_fix(g):
        g = g.sort_values('Date')
        row = g.iloc[-1].copy()  # last reported within the quarter

        shares = row.get('Shares_outstanding', np.nan)
        if pd.isna(shares):
            mcap = row.get('MCAP_MCLP', np.nan)
            px   = row.get('Price_CLP', np.nan)
            if pd.notna(mcap) and pd.notna(px) and px != 0:
                shares = mcap * 1_000_000.0 / px

        nav_ps = row.get('NAV_CLP_per_share', np.nan)
        if pd.isna(nav_ps):
            tot = row.get('Total_NAV_MCLP', np.nan)
            if pd.notna(tot) and pd.notna(shares) and shares != 0:
                nav_ps = (tot * 1_000_000.0) / shares

        # basic plausibility: drop obviously broken values
        if pd.notna(nav_ps) and nav_ps <= 0:
            nav_ps = np.nan
        if pd.notna(shares) and shares <= 0:
            shares = np.nan

        out = {
            'Holding': 'QUINENCO',
            'Date_quarter': row['Date_quarter'],
            'ReportDate': row['Date'],
            'NAV_PS': nav_ps,
            'eqy_shares_total': shares / 1_000_000.0 if pd.notna(shares) else np.nan,
        }
        return pd.Series(out)

    q_quarter = (qraw.groupby('Date_quarter', as_index=False)
                      .apply(_quarter_fix)
                      .dropna(subset=['NAV_PS']))  # keep only quarters that yielded NAV_PS

    # ---------- expand to daily trading days within each quarter ----------
    # Pull QUINENCO price series (holding-mapped or raw)
    quin_prices = prices_holding[prices_holding['Holding'] == 'QUINENCO'][['Date','Date_quarter','Holding','Price']].copy()
    if quin_prices.empty:
        qp2 = prices[prices['ID'] == 'QUINENCO'][['Date','Date_quarter','ID','Price']].copy()
        if not qp2.empty:
            qp2 = qp2.rename(columns={'ID':'Holding'})
            quin_prices = qp2
    if quin_prices.empty:
        raise ValueError("QUINENCO price series not found.")

    qdays = (quin_prices
             .merge(q_quarter[['Holding','Date_quarter','NAV_PS','eqy_shares_total','ReportDate']],
                    on=['Holding','Date_quarter'],
                    how='left')
             .sort_values(['Date']))

    # only ffill INSIDE the quarter (no bleeding across)
    qdays['NAV_PS'] = qdays.groupby('Date_quarter')['NAV_PS'].ffill()
    qdays['eqy_shares_total'] = qdays.groupby('Date_quarter')['eqy_shares_total'].ffill()

    # compute daily NAV(MCh$) and discount; guard against zero/NaN NAV_PS
    qdays['NAV'] = qdays['NAV_PS'] * qdays['eqy_shares_total']
    qdays.loc[qdays['NAV_PS'].isna() | (qdays['NAV_PS'] <= 0), ['NAV','Discount']] = np.nan
    qdays.loc[qdays['NAV_PS'].notna() & (qdays['NAV_PS'] > 0), 'Discount'] = 1.0 - (qdays['Price'] / qdays['NAV_PS'])

    # restrict to trading calendar used elsewhere
    qdays = (qdays
             .query('Date in @trading_dates')
             .reset_index(drop=True)
             [['Date','Date_quarter','Holding','NAV','NAV_PS','Price','Discount']])

    # ---------- append to aligned (clean) ----------
    nav_aligned = pd.concat([nav_aligned, qdays], ignore_index=True)
    nav_aligned = (nav_aligned
                   .sort_values(['Holding','Date'])
                   .drop_duplicates(['Holding','Date'], keep='last')
                   .reset_index(drop=True))

    # ---------- append to spot (live) with staleness mask ----------
    qspot = qdays.copy()
    q_end_dt = qspot['Date_quarter'].dt.end_time
    qspot['stale'] = (qspot['Date'] - q_end_dt).dt.days > MAX_STALENESS_D
    qspot.loc[qspot['stale'], ['NAV','NAV_PS','Discount']] = np.nan
    nav_spot = pd.concat([nav_spot, qspot.drop(columns=['stale'])], ignore_index=True)
    nav_spot = (nav_spot
                .sort_values(['Holding','Date'])
                .drop_duplicates(['Holding','Date'], keep='last')
                .reset_index(drop=True))

    print("[INFO] QUINENCO publisher NAV integrated with quarter-safe rebuild (2023+ guard).")

except Exception as e:
    print(f"[WARN] QUINENCO publisher NAV integration failed: {e}")

# =========================
# POST-PROCESS GUARD: fix QUINENCO rows before save
# =========================
def _fix_quinen_nav(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    # Only QUINENCO rows
    m = out['Holding'].eq('QUINENCO')
    if not m.any():
        return out

    # Ensure needed numerics
    for c in ['eqy_shares_total','NAV_PS','Price','NAV']:
        if c in out.columns:
            out.loc[m, c] = pd.to_numeric(out.loc[m, c], errors='coerce')

    # 1) If NAV looks suspiciously equal to Price (within tiny tolerance), null it
    tol = 1e-9
    same_as_price = (out.loc[m, 'NAV'].sub(out.loc[m, 'Price']).abs() <= tol)
    out.loc[m & same_as_price, 'NAV'] = np.nan

    # 2) Compute the "correct" NAV from components we trust
    #    NAV_expected (MCh$) = NAV_PS (CLP/share) * eqy_shares_total (million shares)
    nav_expected = out.loc[m, 'NAV_PS'] * out.loc[m, 'eqy_shares_total']

    # If NAV is NaN or far from expected, replace with expected
    need_replace = out.loc[m, 'NAV'].isna() | (nav_expected.notna() & (out.loc[m, 'NAV'] - nav_expected).abs() > 1e-6)
    out.loc[m & need_replace, 'NAV'] = nav_expected

    # 3) Recompute Discount safely when NAV_PS is valid (>0)
    ok_navps = m & out['NAV_PS'].notna() & (out['NAV_PS'] > 0) & out['Price'].notna()
    out.loc[ok_navps, 'Discount'] = 1.0 - (out.loc[ok_navps, 'Price'] / out.loc[ok_navps, 'NAV_PS'])

    # If NAV_PS is invalid, blank the dependent fields
    bad_navps = m & (~ok_navps)
    out.loc[bad_navps, ['NAV','Discount']] = np.nan

    return out

# Apply guard to both aligned and spot versions
nav_aligned = _fix_quinen_nav(nav_aligned)
nav_spot    = _fix_quinen_nav(nav_spot)


# -------- Save --------
save_csv(nav_aligned, 'nav_aligned.csv', index=False, encoding='utf-8-sig')
save_csv(nav_spot, 'nav_spot.csv', index=False, encoding='utf-8-sig')

# For downstream compatibility, keep nav.csv = aligned (clean) by default
save_csv(nav_aligned, 'nav.csv', index=False, encoding='utf-8-sig')

print("[INFO] NAV written:")
print(" - nav_aligned.csv (clean, quarter-aligned)")
print(" - nav_spot.csv    (live; masked when inputs are >90d stale)")
print(" - nav.csv         (alias of nav_aligned.csv)")