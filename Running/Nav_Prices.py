#!/usr/bin/env python
# coding: utf-8
import re
import pandas as pd

INPUT_TXT  = "lastPrices_Nav.txt"
OUTPUT_TXT = "lastPrices_Nav_corrected.txt"  # safe: don’t overwrite the raw file

# 1) map known aliases → canonical IDs our pipeline uses
TICKER_ALIASES = {
    "ORO BLANCO": "ORO_BLANCO",
    "PAMPA CALICHERA": "PAMPA_CALICHERA",
    "GLOBAL MINING": "GLOBAL_MINING",
    # add more if needed, e.g. spacing/accents variants seen in the TXT
}

def canonicalize_ticker(s: str) -> str:
    """
    Normalize a raw ticker label to our pipeline's ID format.
    - Strip spaces, collapse to underscores, remove dots
    - Uppercase
    - Apply explicit alias map (wins over generic rules)
    """
    raw = str(s).strip()
    if raw in TICKER_ALIASES:
        return TICKER_ALIASES[raw]
    # generic cleanup for safety
    x = re.sub(r"[.\u00B7]+", "", raw)       # remove dots / middle-dots
    x = re.sub(r"\s+", "_", x)               # spaces → underscores
    x = x.upper()
    return x

def load_data():
    # try common separators; keep it simple since your file is 7 columns
    names = ['TICKER', 'DATE', 'OPEN', 'HIGH', 'LOW', 'CLOSE', 'VOLUME']
    for sep in [',', ';', r'\s+']:
        try:
            df = pd.read_csv(INPUT_TXT, header=None, names=names,
                             sep=sep if sep != r'\s+' else None,
                             delim_whitespace=(sep == r'\s+'))
            if df.shape[1] == 7:
                break
        except Exception:
            df = None
    if df is None or df.shape[1] != 7:
        raise ValueError(f"Could not parse {INPUT_TXT} into 7 columns.")

    # drop header-ish junk row if present
    df = df[df['DATE'] != "{D.DATE.trans('-')}"].copy()

    # normalize tickers
    df['TICKER'] = df['TICKER'].map(canonicalize_ticker)

    # coerce date & numerics
    df['DATE'] = pd.to_datetime(df['DATE'], format='%Y%m%d', errors='coerce')
    num_cols = ['OPEN','HIGH','LOW','CLOSE','VOLUME']
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors='coerce')

    # write a corrected TXT (no header, original 7-column shape)
    df_out = df.copy()
    df_out = df_out[['TICKER','DATE','OPEN','HIGH','LOW','CLOSE','VOLUME']]
    df_out['DATE'] = df_out['DATE'].dt.strftime('%Y%m%d')
    df_out.to_csv(OUTPUT_TXT, index=False, header=False)

    return df

data = load_data()

# build the wide Excel (unchanged)
prices = data[['TICKER', 'DATE', 'CLOSE']].copy()
prices.columns = ['ID', 'Date', 'Price']
prices['Date'] = pd.to_datetime(prices['Date'])
prices_wide = prices.pivot(index='Date', values='Price', columns='ID').reset_index()
prices_wide.ffill(inplace=True)
prices_wide.to_excel('NAV_Prices_Wide.xlsx', index=False)

print(f"[INFO] Normalized prices written to {OUTPUT_TXT} with canonical tickers (e.g., ORO_BL\nANCO).")
