# -*- coding: utf-8 -*-
"""
Created on Tue Nov 11 16:23:29 2025

@author: Administrator
"""
# ownership_selenium.py
# Scrapes CMF "12 Mayores Accionistas" for tickers listed in tickers.csv
# Outputs CSVs with schema: Holding,pct_holding,eqy_shares,eqy_shares_total,Date,ID

import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# ------------- Config -------------
BASE = "https://www.cmfchile.cl/institucional/mercados/entidad.php"
CANDIDATE_TYPES = ["RVSOC", "RVEMI"]      # Try both
PERIODS = ["03", "06", "09", "12"]        # Quarterly months
START_YEAR = 2017
THIS_YEAR = date.today().year
HEADLESS_DEFAULT = True
DEBUG_HTML = False  # True to dump __debug_*.html if no data

# If dropdown shows month names, map values here (script tries value then visible text):
MONTH_MAP_VISIBLE = {
    "03": "03",         # change to "Marzo" if needed
    "06": "06",         # change to "Junio"
    "09": "09",         # change to "Septiembre"
    "12": "12",         # change to "Diciembre"
}

# Your selectors:
CSS_TABLE         = "#contenido > table:nth-child(6)"                # 12 Mayores Accionistas table
CSS_PERIOD_SELECT = "#contenido > form > div:nth-child(9) > select"  # Month dropdown
CSS_YEAR_SELECT   = "#ano"                                           # Year dropdown
CSS_SUBMIT        = "#contenido > form > div.fieldset.submit > input"  # Consultar button
# ----------------------------------

def log(*msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}]",
          *msg, flush=True)

def clean_int(s: str):
    if s is None:
        return None
    s = s.replace("\xa0", " ").replace(".", "").replace(",", "").strip()
    if not s:
        return None
    try:
        return int(float(s))
    except Exception:
        return None

def clean_pct(s: str):
    if s is None:
        return None
    s = s.replace("%", "").replace("\xa0", " ").replace(".", "").replace(",", ".").strip()
    if not s:
        return None
    try:
        return float(s) / 100.0
    except Exception:
        return None

def infer_total_shares(rows):
    cands = []
    for r in rows:
        p = r.get("pct_holding")
        v = r.get("eqy_shares")
        if p and p > 0 and v:
            cands.append(round(v / p))
    if not cands:
        return None
    vc = pd.Series(cands).value_counts()
    top_count = vc.iloc[0]
    ties = vc[vc == top_count].index.tolist()
    return max(ties)

def find_12maj_table(driver, wait_seconds=6):
    try:
        table = WebDriverWait(driver, wait_seconds).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, CSS_TABLE))
        )
        tbody = table.find_element(By.TAG_NAME, "tbody")
        rows = tbody.find_elements(By.TAG_NAME, "tr")
        return table, rows
    except Exception:
        return None, []

def select_period_via_controls(driver, year: int, per: str):
    """Use provided selectors for month/year + click Consultar."""
    try:
        period_select_el = WebDriverWait(driver, 6).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, CSS_PERIOD_SELECT))
        )
    except TimeoutException:
        period_select_el = None

    try:
        year_select_el = WebDriverWait(driver, 6).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, CSS_YEAR_SELECT))
        )
    except TimeoutException:
        year_select_el = None

    # Month
    if period_select_el is not None:
        sel = Select(period_select_el)
        target_visible = MONTH_MAP_VISIBLE.get(per, per)
        try:
            sel.select_by_value(per)
        except Exception:
            try:
                sel.select_by_visible_text(target_visible)
            except Exception:
                pass

    # Year
    if year_select_el is not None:
        sely = Select(year_select_el)
        try:
            sely.select_by_value(str(year))
        except Exception:
            try:
                sely.select_by_visible_text(str(year))
            except Exception:
                pass

    # Consultar button
    try:
        submit_btn = driver.find_element(By.CSS_SELECTOR, CSS_SUBMIT)
        submit_btn.click()
    except NoSuchElementException:
        # If no explicit submit, rely on onchange
        pass

    # Wait for table refresh
    try:
        WebDriverWait(driver, 6).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, CSS_TABLE))
        )
    except TimeoutException:
        pass
    time.sleep(0.5)

def scrape_one_period(driver, rut: str, entity_type: str, ticker: str, year: int, per: str):
    """Try querystring first; if no rows, use form with your selectors."""
    log(f"  ├─ Period {year}-{per}: trying querystring …")
    qurl = (f"{BASE}?mercado=V&rut={rut}&grupo=&tipoentidad={entity_type}"
            f"&vig=VI&control=svs&pestania=5&periodo={per}%2F{year}")
    driver.get(qurl)
    time.sleep(0.6)

    table, rows = find_12maj_table(driver)
    page_html = driver.page_source
    used = "querystring"

    if not rows or "Sin datos para este periodo" in page_html:
        log(f"  │    querystring returned no rows; trying form controls …")
        url = f"{BASE}?mercado=V&rut={rut}&grupo=&tipoentidad={entity_type}&vig=VI&control=svs&pestania=5"
        driver.get(url)
        try:
            WebDriverWait(driver, 6).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#contenido"))
            )
        except TimeoutException:
            if DEBUG_HTML:
                Path(f"__debug_{ticker}_{year}_{per}_no_contenido.html").write_text(page_html, encoding="utf-8")
            log(f"  │    form container not found")
            return []
        select_period_via_controls(driver, year, per)
        table, rows = find_12maj_table(driver)
        page_html = driver.page_source
        used = "form"

    if not rows or "Sin datos para este periodo" in page_html:
        if DEBUG_HTML:
            Path(f"__debug_{ticker}_{year}_{per}_nodata.html").write_text(page_html, encoding="utf-8")
        log(f"  │    no rows for {year}-{per}")
        return []

    period_rows = []
    for r in rows:
        tds = r.find_elements(By.TAG_NAME, "td")
        if len(tds) < 4:
            continue
        name = tds[0].text.strip()
        sus  = tds[1].text.strip()
        pag  = tds[2].text.strip()
        pct  = tds[3].text.strip()
        eqy = clean_int(pag) or clean_int(sus)
        p   = clean_pct(pct)
        if not name or eqy is None:
            continue
        period_rows.append({"Holding": name, "pct_holding": p, "eqy_shares": eqy})

    if not period_rows:
        if DEBUG_HTML:
            Path(f"__debug_{ticker}_{year}_{per}_emptyrows.html").write_text(page_html, encoding="utf-8")
        log(f"  │    empty rows after parse for {year}-{per}")
        return []

    total = infer_total_shares(period_rows)
    dt = f"{year}-{per}-01"
    out = []
    for r in period_rows:
        out.append({
            "Holding": r["Holding"],
            "pct_holding": r["pct_holding"],
            "eqy_shares": r["eqy_shares"],
            "eqy_shares_total": total,
            "Date": dt,
            "ID": ticker
        })
    log(f"  │    {used}: scraped {len(out)} rows")
    return out

def scrape_one_entity(driver, rut: str, ticker: str, is_holding: int):
    """Try both entity types until we get data."""
    for et in CANDIDATE_TYPES:
        url = f"{BASE}?mercado=V&rut={rut}&grupo=&tipoentidad={et}&vig=VI&control=svs&pestania=5"
        driver.get(url)
        time.sleep(0.3)
        if "12 Mayores Accionistas" not in driver.page_source:
            log(f"  [skip] tipoentidad={et} doesn't show the tab")
            continue

        log(f"  Using tipoentidad={et}")
        all_rows = []
        for year in range(START_YEAR, THIS_YEAR + 1):
            log(f"  ─ Year {year}")
            for per in PERIODS:
                try:
                    rows = scrape_one_period(driver, rut, et, ticker, year, per)
                    if rows:
                        all_rows.extend(rows)
                except Exception as ex:
                    log(f"  │    ERROR {year}-{per}: {ex}")
                    continue

        if all_rows:
            return all_rows
    return []

def run_selenium(tickers_csv: str, out_hol: str, out_com: str, headless: bool = HEADLESS_DEFAULT):
    # Load tickers
    tick = pd.read_csv(tickers_csv, dtype={"RUT": str})
    tick["RUT"] = tick["RUT"].str.replace(r"[^0-9]", "", regex=True)

    # Chrome driver
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1280,900")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

    records_hol = []
    records_com = []

    try:
        for _, row in tick.iterrows():
            ticker = row["ID"]
            rut = row["RUT"]
            is_holding = int(row["IS_HOLDING"])
            log(f"Ticker {ticker} (RUT {rut}) | holding={is_holding == 1}")
            rows = scrape_one_entity(driver, rut, ticker, is_holding)
            if not rows:
                log(f"[WARN] No data for {ticker}")
            else:
                if is_holding == 1:
                    records_hol.extend(rows)
                else:
                    records_com.extend(rows)
            time.sleep(0.2)
    finally:
        driver.quit()

    # Save with exact schema (even if empty)
    cols = ["Holding", "pct_holding", "eqy_shares", "eqy_shares_total", "Date", "ID"]
    df_h = pd.DataFrame(records_hol, columns=cols)
    df_c = pd.DataFrame(records_com, columns=cols)

    df_h.to_csv(out_hol, index=False)
    df_c.to_csv(out_com, index=False)

    log(f"[INFO] Saved {len(df_h)} rows -> {out_hol}")
    log(f"[INFO] Saved {len(df_c)} rows -> {out_com}")

if __name__ == "__main__":
    # Defaults for running from Spyder
    TICKERS = "tickers.csv"
    OUT_HOL = "ownership_data_hol_db_new.csv"
    OUT_COM = "ownership_data_com_db_new.csv"
    run_selenium(TICKERS, OUT_HOL, OUT_COM, headless=HEADLESS_DEFAULT)

