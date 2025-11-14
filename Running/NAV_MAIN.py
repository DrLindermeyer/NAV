# -*- coding: utf-8 -*-
"""
Created on Tue Nov 11 17:50:52 2025

@author: Administrator
"""

# main_nav_pipeline.py
# End-to-end NAV pipeline (no Jupyter), with SAFE CSV reading (no "drop first column").
# It only keeps the expected columns from each file to avoid interference
# from stray index columns like 'Unnamed: 0'.

import pandas as pd
import numpy as np
from pathlib import Path

# =========================
# Helpers: Safe CSV reading
# =========================

def read_expected_csv(path, expected_cols, sep=",", dtype="str"):
    """
    Read `path` and return ONLY the expected columns (and in that order).
    - Extra columns (e.g., 'Unnamed: 0') are ignored.
    - If required columns are missing, raises a clear error.
    dtype:
      - "str" -> read as strings (default)
      - None  -> let pandas infer
      - dict  -> pass to pandas
    """
    if dtype == "str":
        df = pd.read_csv(path, sep=sep, dtype=str)
    elif dtype is None:
        df = pd.read_csv(path, sep=sep)
    else:
        df = pd.read_csv(path, sep=sep, dtype=dtype)

    missing = [c for c in expected_cols if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: missing required columns {missing}. Found: {list(df.columns)}")
    return df[expected_cols].copy()


# =========================
# 0) Prices
# =========================

def load_prices(prices_path="lastPrices_Nav.txt"):
    # lastPrices_Nav.txt structure expected: TICKER,DATE,OPEN,HIGH,LOW,CLOSE,VOLUME
    df = pd.read_csv(prices_path, header=None, names=['TICKER','DATE','OPEN','HIGH','LOW','CLOSE','VOLUME'])
    # Remove header-like rows
    df = df[df['DATE'] != 'DATE.trans(-)']
    # Parse date
    df['DATE'] = pd.to_datetime(df['DATE'], format='%Y%m%d', errors='coerce')
    df = df.dropna(subset=['DATE'])
    # Numeric columns
    num_cols = ['OPEN','HIGH','LOW','CLOSE','VOLUME']
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df

def fill_missing_dates(data):
    # Keep TICKER, DATE, CLOSE
    pc = data[['TICKER','DATE','CLOSE']].copy()
    wide = pc.pivot(index='DATE', columns='TICKER', values='CLOSE').reset_index()
    wide.columns.name = None

    # Full daily range
    all_days = pd.date_range(start=pc['DATE'].min(), end=pc['DATE'].max(), freq='D')
    full = pd.DataFrame({'DATE': all_days}).merge(wide, on='DATE', how='left')

    # ffill prices
    full.ffill(inplace=True)

    # Back to long
    long = full.melt(id_vars=['DATE'], var_name='TICKER', value_name='CLOSE')
    return long


# =========================
# 1) Run prices processing
# =========================

data = load_prices('lastPrices_Nav.txt')
trading_dates = data['DATE'].sort_values().drop_duplicates().reset_index(drop=True)
prices = fill_missing_dates(data)

prices['Date_quarter'] = prices['DATE'].dt.to_period('Q')
prices = prices.rename(columns={'DATE':'Date','TICKER':'ID','CLOSE':'Price'})

# =========================
# 2) Load reference inputs
# =========================

# Tickers
TICKER_COLS = ['ID','RUT','IS_HOLDING','GROUP']
tickers = read_expected_csv('tickers.csv', TICKER_COLS, sep=',')

# Balance Sheets (company + holding) with SAFE reading
BAL_COLS = ['Description','Value','Curncy','Date','ID']
balance_comp = read_expected_csv('balance_sheets_com_db.csv', BAL_COLS)
balance_hol  = read_expected_csv('balance_sheets_hol_db.csv', BAL_COLS)
balance = pd.concat([balance_comp, balance_hol], ignore_index=True)

# Parse balance sheet values
balance['Date'] = pd.to_datetime(balance['Date'], errors='coerce')
balance = balance.dropna(subset=['Date'])
balance['Value'] = balance['Value'].replace('-', '0')
balance['Value'] = (balance['Value']
                    .str.replace('.', '', regex=False)   # remove thousand sep
                    .str.replace(',', '.', regex=False)) # if decimal comma
balance['Value'] = pd.to_numeric(balance['Value'], errors='coerce')

# FX
FX_COLS = ['Date', 'USDCLP']
fx = read_expected_csv('usdclp.csv', FX_COLS, sep=',')
fx['Date'] = pd.to_datetime(fx['Date'], errors='coerce')
fx = fx.dropna(subset=['Date'])
# If USDCLP comes with commas, normalize:
fx['USDCLP'] = (fx['USDCLP'].astype(str)
                .str.replace(',', '', regex=False))
fx['USDCLP'] = pd.to_numeric(fx['USDCLP'], errors='coerce')

# NetDebt guide (mapping of which Description to include under "Balance2" category)
# Expecting ';' separator per your example
netdebt_guide = read_expected_csv('balance_netdebt.csv',
                                  expected_cols=['ID','Description','Balance2','Holding'],
                                  sep=';')
id_holding = netdebt_guide[['Holding','ID']].drop_duplicates().copy()

# Map prices to holdings (for later joins)
prices_final = prices.merge(id_holding, on='ID', how='inner')


# =========================
# 3) Currency fix and balance_total
# =========================

# Manual currency fix example
balance.loc[balance['ID'] == 'ANTARCHILE', 'Curncy'] = 'USD'

balance_usd = balance[balance['Curncy'] == 'USD'].copy()
balance_cl  = balance[balance['Curncy'] != 'USD'].copy()

# Merge USD rows with FX and convert to CLP (then K CLP)
balance_usd = balance_usd.merge(fx, on='Date', how='inner')
balance_usd['Value'] = balance_usd['Value'] * balance_usd['USDCLP']
balance_usd = balance_usd.drop(columns=['USDCLP'])

balance_total = pd.concat([balance_cl, balance_usd], ignore_index=True)
balance_total['Value'] = balance_total['Value'] / 1000.0  # to thousands
balance_total = balance_total.drop(columns=['Curncy'], errors='ignore')
balance_total.to_csv('balance_total.csv', index=False, encoding='utf-8-sig')


# =========================
# 4) Select only NetDebt-related lines
# =========================

# keep only lines whose Description is in the guide (by ID)
balance_sel = (balance_total
               .merge(netdebt_guide, on=['ID','Description'], how='inner')
               .merge(tickers, on='ID', how='inner')
               [['Date','ID','Holding','IS_HOLDING','Description','Balance2','Value']])

balance_sel.to_csv('balance_selected_netdebt.csv', index=False, encoding='utf-8-sig')

# Sign convention: companies as negative (they are sub-components); holding as positive
balance_sel['Value'] = np.where(balance_sel['IS_HOLDING'].astype(float) == -1,
                                -balance_sel['Value'], balance_sel['Value'])

# Group by company/holding/date/category to sum values
balance_group = (balance_sel
                 .groupby(['ID','Date','Balance2'], as_index=False)['Value']
                 .sum())
balance_group.to_csv('balance_netdebt_comp_hol.csv', index=False, encoding='utf-8-sig')

# Build helper to find latest consolidation date for each holding (by quarter)
id_date_max = (balance_group[['ID','Date']].drop_duplicates()
               .merge(tickers, on='ID', how='left')[['Date','ID','IS_HOLDING']]
               .drop_duplicates())
hol_date_max = id_date_max[id_date_max['IS_HOLDING'].astype(float) == 1][['ID','Date']].copy()
hol_date_max['Date_quarter'] = hol_date_max['Date'].dt.to_period('Q')
hol_date_max = hol_date_max.drop(columns=['Date'])
hol_date_max.columns = ['Holding','Date_quarter']
hol_date_max.to_csv('Holding_Max_Report.csv', index=False, encoding='utf-8-sig')

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

# If external corrected netdebt exists, prefer it; else use computed
if Path('netdebt.csv').exists():
    ext = pd.read_csv('netdebt.csv', sep=';')
    # Normalize types
    ext['NetDebt'] = (ext['NetDebt'].astype(str)
                      .str.replace(',', '', regex=True)
                      .str.strip())
    ext['NetDebt'] = pd.to_numeric(ext['NetDebt'], errors='coerce')
    ext['Date_quarter'] = pd.PeriodIndex(ext['Date_quarter'], freq='Q')
    netdebt_corrected_dt = ext
else:
    netdebt_corrected_dt = netdebt.copy()


# =========================
# 5) Forward-fill NetDebt to trading dates (per holding)
# =========================

# Use earliest quarter we have non-null NetDebt
min_q = netdebt_corrected_dt.loc[netdebt_corrected_dt['NetDebt'].notna(), 'Date_quarter'].min()
min_date_final = pd.Period(min_q, freq='Q').start_time

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

ownership_hol = read_expected_csv('ownership_data_hol_db.csv', OWN_COLS, sep=',')
ownership_hol['Date'] = pd.to_datetime(ownership_hol['Date'], errors='coerce')
ownership_hol['Date_quarter'] = ownership_hol['Date'].dt.to_period('Q')

holding_shares = ownership_hol[['ID','Date_quarter','eqy_shares_total']].copy()
holding_shares['eqy_shares_total'] = pd.to_numeric(holding_shares['eqy_shares_total'], errors='coerce') / 1_000_000
holding_shares = holding_shares.drop_duplicates()
holding_shares.columns = ['Holding','Date_quarter','eqy_shares_total']

holding_shares_dt = dates_holding.merge(holding_shares, on=['Date_quarter','Holding'], how='left')
min_date_hs = holding_shares_dt.loc[holding_shares_dt['eqy_shares_total'].notna(), 'Date'].min()

holding_shares_final = holding_shares_dt[holding_shares_dt['Date'] >= min_date_final].copy()
holding_shares_final['eqy_shares_total'] = holding_shares_final.groupby('Holding')['eqy_shares_total'].ffill()
holding_shares_final = holding_shares_final.drop_duplicates().reset_index(drop=True)


# =========================
# 7) Ownership (subsidiaries → % holdings)
# =========================

ownership_com = read_expected_csv('ownership_data_com_db.csv', OWN_COLS, sep=',')
ownership_com['Date'] = pd.to_datetime(ownership_com['Date'], errors='coerce')

# Map noisy Holding strings to canonical tickers (adjust as needed)
holdings_dict = {
    'INV ALTEL LTDA': 'ALMENDRAL',
    'ALMENDRAL S A': 'ALMENDRAL',
    'INV AGUAS METROPOLITANAS S A': 'IAM',
    'INVERCAP S.A.': 'INVERCAP',
    'INVERCAP SA': 'INVERCAP',
    'ANTARCHILE S.A.': 'ANTARCHILE',
}
ownership_com = ownership_com[ownership_com['Holding'].isin(holdings_dict.keys())].copy()
ownership_com['Holding'] = ownership_com['Holding'].map(holdings_dict)

ownership_com = (ownership_com
                 .merge(tickers, on='ID', how='inner')
                 [['Date','ID','Holding','pct_holding','eqy_shares_total']])
# Convert shares to millions
ownership_com['eqy_shares_total'] = pd.to_numeric(ownership_com['eqy_shares_total'], errors='coerce') / 1_000_000
ownership_com['Date_quarter'] = ownership_com['Date'].dt.to_period('Q')
ownership_com = ownership_com.drop(columns=['Date'])

# Extra mapping file
id_holding_2 = read_expected_csv('id_holding_2.csv', expected_cols=['ID','Holding'], sep=';')

def process_ownership_data(prices_df, mapping_df, ownership_df):
    # map each ID to Holding
    prices_final_local = prices_df.merge(mapping_df, on='ID', how='inner')
    holdings = mapping_df['Holding'].unique()

    # remove rows where ID itself is a holding (keep only subsidiaries here)
    prices_final_local = prices_final_local[~prices_final_local['ID'].isin(holdings)].copy()

    cols_keep = ['Date','Date_quarter','ID','Holding','pct_holding','Price','eqy_shares_total']
    merged = (prices_final_local
              .merge(ownership_df, on=['ID','Holding','Date_quarter'], how='left'))[cols_keep]

    # earliest Date with non-null pct_holding
    min_date = merged.loc[merged['pct_holding'].notna(), 'Date'].min()
    if pd.notnull(min_date):
        filtered = merged[merged['Date'] >= min_date].copy()
    else:
        filtered = merged.copy()

    # forward fill
    filtered['pct_holding'] = filtered.groupby('ID')['pct_holding'].ffill()
    filtered['eqy_shares_total'] = filtered.groupby('ID')['eqy_shares_total'].ffill()
    filtered = filtered.reset_index(drop=True)

    return filtered

price_ownership = process_ownership_data(prices, id_holding_2, ownership_com)
price_ownership.to_csv('price_ownership.csv', index=False, encoding='utf-8-sig')

# MarketCap (subsidiary) and HoldingValue
price_ownership['MarketCap'] = price_ownership['Price'] * price_ownership['eqy_shares_total']
price_ownership['pct_holding'] = pd.to_numeric(price_ownership['pct_holding'], errors='coerce')
price_ownership['HoldingValue'] = price_ownership['MarketCap'] * price_ownership['pct_holding']

price_ownership.to_csv('info_prices_ownership.csv', index=False, encoding='utf-8-sig')

prices_ownership = (price_ownership
                    .groupby(['Date','Date_quarter','Holding'], as_index=False)['HoldingValue']
                    .sum()
                   ).drop_duplicates()
prices_ownership.to_csv('prices_ownership.csv', index=False, encoding='utf-8-sig')


# =========================
# 8) NAV computation
# =========================

# Holding prices (price of the holding tickers themselves)
holdings = id_holding['Holding'].unique()
prices_holding = prices_final[prices_final['ID'].isin(holdings)][['Date','Date_quarter','Holding','Price']]

# Merge PriceOwnership with NetDebt, compute NAV
nav = (prices_ownership
       .merge(netdebt_final_dt, on=['Holding','Date','Date_quarter'], how='inner')
       [['Date','Date_quarter','Holding','HoldingValue','NetDebt']])
nav['NAV'] = nav['HoldingValue'] - nav['NetDebt']

# Bring holding share count to compute NAV per share
nav_final = (nav
             .merge(holding_shares_final, on=['Holding','Date','Date_quarter'], how='inner')
             [['Date','Date_quarter','Holding','HoldingValue','NetDebt','NAV','eqy_shares_total']])
nav_final['NAV_PS'] = nav_final['NAV'] / nav_final['eqy_shares_total']

# Discount = (Price / NAV_PS - 1) * -1
nav_final = nav_final.merge(prices_holding, on=['Date','Date_quarter','Holding'], how='left')
nav_final['Discount'] = ((nav_final['Price'] / nav_final['NAV_PS']) - 1) * -1
nav_final = nav_final.sort_values(['Holding','Date']).reset_index(drop=True)

# Keep only trading dates
nav_final = nav_final[nav_final['Date'].isin(trading_dates)].copy()
nav_final.to_csv('nav.csv', index=False, encoding='utf-8-sig')

print("[INFO] Done. Files written:")
print(" - balance_total.csv")
print(" - balance_selected_netdebt.csv")
print(" - balance_netdebt_comp_hol.csv")
print(" - Holding_Max_Report.csv")
print(" - price_ownership.csv")
print(" - info_prices_ownership.csv")
print(" - prices_ownership.csv")
print(" - nav.csv")
