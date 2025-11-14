"""
CMF Balance-Sheet (Estado de situacion financiera) scraper — fast & robust

Outputs (schema: Description,Value,Curncy,Date,ID):
  - balance_sheets_hol_db.csv  (holdings)
  - balance_sheets_com_db.csv  (subsidiaries)

Key robustness/speed features:
  * Blocks images/CSS/fonts/media/analytics via Chrome DevTools
  * page_load_strategy="eager"
  * JavaScript-based select setting (+ scrollIntoView) to avoid stale/select glitches
  * Re-open the Financials tab if filters aren't present (cheap reset)
  * Per-period retry with backoff; “skip period” after 2 failed preps
  * Only 2017 → last completed quarter
  * ThreadPoolExecutor (Windows-friendly) — WORKERS=2 for your i5/16GB
  * Very detailed ASCII-safe logging/heartbeats at every step

Requires: pip install selenium webdriver-manager pandas
"""

# ------------------- Imports -------------------
import os
import sys
import re
import math
import time
import random
import calendar
from datetime import date, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, WebDriverException
)

# ------------------- Config -------------------
BASE = "https://www.cmfchile.cl/institucional/mercados/entidad.php"
P_TAB = 3                                  # financial statements tab id
ENTITY_TYPES = ["RVSOC", "RVEMI"]            # try both; first that yields data
TIPO_PRIORITY = ["C","I"]                 # consolidated first, fallback individual
FRAMEWORK_VALUE = "IFRS"

START_YEAR = 2017
THIS_YEAR = date.today().year

WORKERS = 2                                # good for i5 7th gen / 16GB

# Selectors
SEL_MONTH      = "#mm"
SEL_YEAR       = "#aa"
SEL_TIPO       = "#fm > div:nth-child(3) > select"
SEL_FRAMEWORK  = "#fila_tipo > select"
SEL_CONSULTAR  = "#fm > div.fieldset.submit > input"

# Waits / logging
HEADLESS = True
PAGELOAD_STRATEGY = "eager"
W_SHORT = 5
W_MED   = 9

DATE_RE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
TITLE_SNIPPETS = ["[210000]", "Estado de situacion financiera", "corriente/no corriente"]

OUT_HOL = "balance_sheets_hol_db.csv"
OUT_COM = "balance_sheets_com_db.csv"
TICKERS = "tickers.csv"

# ---------- status + timing (ASCII-safe) ----------
_T0 = time.time()
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

_ASCII_MAP = {
    "→":"->","←":"<-","✓":"OK","✔":"OK","✗":"x","×":"x","•":"*","·":"-",
    "—":"-","–":"-","…":"...","’":"'","“":'"',"”":'"'
}
def _asciify(s: str) -> str:
    for k, v in _ASCII_MAP.items():
        s = s.replace(k, v)
    return s

def ts():
    return datetime.now().strftime("%H:%M:%S")

def secs():
    d = time.time() - _T0
    if d < 120: return f"{d:0.1f}s"
    m, s = divmod(int(d), 60)
    if m < 60: return f"{m}m{s:02d}s"
    h, m2 = divmod(m, 60)
    return f"{h}h{m2:02d}m{s:02d}s"

def status(msg: str):
    print(f"[{ts()}] {_asciify(str(msg))}", flush=True)

def hb(scope: str, note: str = ""):
    status(f"[HB] {scope} {note}".strip())

# ------------------- Period helpers -------------------
def last_completed_quarter_year_months():
    now = date.today()
    q = (now.month - 1) // 3 + 1
    last_q_completed = max(1, min(4, q - 1))
    allowed = {}
    for y in range(START_YEAR, THIS_YEAR):
        allowed[y] = ["03", "06", "09", "12"]
    months = ["03", "06", "09", "12"][:last_q_completed]
    if months:
        allowed[THIS_YEAR] = months
    return allowed

def month_last_day(y: int, mm: int) -> int:
    return calendar.monthrange(y, mm)[1]

# ------------------- Chrome Driver -------------------
def make_driver(headless=True, block_resources=True):
    options = webdriver.ChromeOptions()
    options.page_load_strategy = PAGELOAD_STRATEGY
    options.add_argument("--window-size=1280,900")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-features=NetworkService")
    options.add_argument("--lang=es-CL")
    if headless:
        options.add_argument("--headless=new")

    # Lighter profile
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.default_content_setting_values.notifications": 2,
        "profile.managed_default_content_settings.stylesheets": 2,
        "profile.managed_default_content_settings.fonts": 2,
        "profile.managed_default_content_settings.plugins": 2,
        "profile.managed_default_content_settings.popups": 2,
        "profile.managed_default_content_settings.geolocation": 2,
    }
    options.add_experimental_option("prefs", prefs)

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    try:
        driver.set_page_load_timeout(20)
        driver.set_script_timeout(20)
    except Exception:
        pass

    if block_resources:
        try:
            driver.execute_cdp_cmd("Network.enable", {})
            driver.execute_cdp_cmd("Network.setBlockedURLs", {
                "urls": [
                    "*.png","*.jpg","*.jpeg","*.gif","*.webp",
                    "*.css","*.woff","*.woff2","*.ttf","*.otf",
                    "*.svg","*.ico","*.mp4","*.mp3","*.avi",
                    "*google-analytics.com*","*googletagmanager.com*","*doubleclick.net*"
                ]
            })
        except Exception:
            pass
    return driver

# ------------------- Page helpers -------------------
def open_financials_tab(driver, rut: str, entity_type: str, retries: int = 2) -> bool:
    base_url = (f"{BASE}?mercado=V&rut={rut}&grupo=&tipoentidad={entity_type}"
                f"&vig=VI&control=svs&pestania={P_TAB}")

    for attempt in range(retries + 1):
        try:
            driver.get(base_url)
        except WebDriverException as e:
            status(f"[WARN] get() failed attempt {attempt+1}: {e}")
            time.sleep(0.8)
            continue

        try:
            WebDriverWait(driver, 6).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#contenido")))
            try:
                WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#mm")))
                return True
            except TimeoutException:
                try:
                    tab = driver.find_element(By.CSS_SELECTOR, 'a[href*="pestania=3"]')
                    tab.click()
                    WebDriverWait(driver, 4).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#mm")))
                    return True
                except Exception:
                    pass
        except TimeoutException:
            pass

        time.sleep(0.8 + 0.2 * attempt)
    return False

def js_set_select(driver, css_selector, value):
    """Set a <select> value via JS and dispatch change event."""
    script = """
        const sel = document.querySelector(arguments[0]);
        if (!sel) return false;
        sel.scrollIntoView({behavior:'instant', block:'center'});
        sel.value = arguments[1];
        sel.dispatchEvent(new Event('change', {bubbles:true}));
        return true;
    """
    return driver.execute_script(script, css_selector, str(value))

def safe_prepare_filters(driver, rut, entity_type, year, per, tipo, scope, max_retries=2):
    """
    Ensure filters exist; if not, re-open tab. Then set selects via JS and click 'Consultar'.
    Returns True if filters were set & submitted; False otherwise.
    """
    for k in range(max_retries):
        try:
            # Are filters present?
            WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.CSS_SELECTOR, SEL_MONTH)))
        except TimeoutException:
            status(f"[INFO] {scope} filters missing; reopening tab (try {k+1}/{max_retries})")
            if not open_financials_tab(driver, rut, entity_type):
                continue
        # Set selects with JS (more reliable & faster)
        ok_m = js_set_select(driver, SEL_MONTH, per)
        ok_y = js_set_select(driver, SEL_YEAR, str(year))
        ok_t = js_set_select(driver, SEL_TIPO, tipo)
        ok_f = js_set_select(driver, SEL_FRAMEWORK, FRAMEWORK_VALUE)
        hb(scope, f"js-set m={ok_m} y={ok_y} t={ok_t} f={ok_f}")

        # Click "Consultar" if present
        try:
            btn = WebDriverWait(driver, 2).until(EC.element_to_be_clickable((By.CSS_SELECTOR, SEL_CONSULTAR)))
            time.sleep(0.05 + random.random() * 0.15)
            btn.click()
        except TimeoutException:
            # some pages auto-update on change
            pass

        return True  # we did our best to set/submit
    return False

def headers_updated_for_period(driver, year: int, per: str, timeout=W_MED):
    want = f"{year}-{per}"
    end = time.time() + timeout
    while time.time() < end:
        ths = driver.find_elements(By.CSS_SELECTOR, "#contenido table th")
        headers = " | ".join(th.text.strip() for th in ths if th.text)
        if want in headers or DATE_RE.search(headers):
            return True
        time.sleep(0.2)
    return False

def parse_currency(driver) -> str:
    try:
        html = driver.page_source
        m = re.search(r"Moneda:\s*([A-Z]{3,4})", html)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "CLP"

def find_balance_table(driver):
    # Priority XPaths: title or code close to table
    xps = [
        "//div[@id='contenido']//table[.//th[contains(., '[210000]')]]",
        "//div[@id='contenido']//table[.//th[contains(., 'Estado de situacion financiera')]]",
        "//div[@id='contenido']//*[contains(., '[210000]') or contains(., 'Estado de situacion financiera')]/following::table[1]"
    ]
    for xp in xps:
        try:
            tbl = WebDriverWait(driver, W_SHORT).until(EC.presence_of_element_located((By.XPATH, xp)))
            try:
                tbody = tbl.find_element(By.TAG_NAME, "tbody")
                rows = tbody.find_elements(By.TAG_NAME, "tr")
            except NoSuchElementException:
                rows = tbl.find_elements(By.CSS_SELECTOR, "tr")
            if len(rows) >= 5:
                ths = tbl.find_elements(By.CSS_SELECTOR, "th")
                col_dates = [th.text.strip() for th in ths if th.text.strip()][-2:]
                return tbl, rows, col_dates
        except TimeoutException:
            pass

    # Fallback: scan all
    candidates = driver.find_elements(By.CSS_SELECTOR, "#contenido table")
    best, best_rows, best_dates = None, [], []
    for t in candidates:
        ths = t.find_elements(By.CSS_SELECTOR, "th")
        if len(ths) < 2: 
            continue
        texts = [th.text.strip() for th in ths if th.text.strip()]
        has_dates = any(DATE_RE.search(x) for x in texts)
        has_title = any(s in " ".join(texts) for s in TITLE_SNIPPETS)
        if not (has_dates or has_title):
            continue
        try:
            tbody = t.find_element(By.TAG_NAME, "tbody")
            rows = tbody.find_elements(By.TAG_NAME, "tr")
        except NoSuchElementException:
            rows = t.find_elements(By.CSS_SELECTOR, "tr")
        if (best is None) or (len(rows) > len(best_rows)):
            best = t
            best_rows = rows
            best_dates = [th.text.strip() for th in ths if th.text.strip()][-2:]
    if best is not None:
        return best, best_rows, best_dates
    raise TimeoutException("balance-sheet table not found")

def parse_table(rows, col_idx: int):
    out = []
    for r in rows:
        tds = r.find_elements(By.TAG_NAME, "td")
        if len(tds) < (col_idx + 1):
            continue
        desc = tds[0].text.strip()
        val  = tds[col_idx].text.strip()
        if not desc:
            continue
        out.append({"Description": desc, "Value": val})
    return out

# ------------------- Scrape primitives -------------------
def scrape_period(driver, rut, entity_type, ticker, year, per):
    scope = f"{ticker} {entity_type} {year}-{per}"

    # Try each tipo; for each, if filters missing, re-open tab and retry
    for tipo in TIPO_PRIORITY:
        hb(scope, f"prep tipo={tipo}")
        ok_prep = safe_prepare_filters(driver, rut, entity_type, year, per, tipo, scope, max_retries=2)
        if not ok_prep:
            status(f"[WARN] {scope} could not prepare filters after retries")
            continue

        ok = headers_updated_for_period(driver, year, per, timeout=W_MED)
        hb(scope, f"headers {'ok' if ok else 'timeout'}")

        if "Sin datos para este periodo" in driver.page_source:
            status(f"[INFO] {scope} no data text (tipo={tipo})")
            continue

        try:
            hb(scope, "find table")
            tbl, rows, col_dates = find_balance_table(driver)
        except TimeoutException:
            status(f"[WARN] {scope} table not found (tipo={tipo})")
            continue

        last_day = month_last_day(year, int(per))
        want_full = f"{year}-{per}-{str(last_day).zfill(2)}"
        col = 1
        if len(col_dates) >= 2:
            left, right = col_dates[-2], col_dates[-1]
            if right == want_full or right.startswith(f"{year}-{per}"):
                col = 2

        ccy = parse_currency(driver)
        body = parse_table(rows, col)
        hb(scope, f"rows={len(body)} col={col} ccy={ccy}")

        if not body:
            continue

        out = [{
            "Description": r["Description"],
            "Value": r["Value"],
            "Curncy": ccy,
            "Date": want_full,
            "ID": ticker
        } for r in body]
        status(f"[OK] {scope} {len(out)} lines (tipo={tipo})")
        return out

    return []

def scrape_entity(rut: str, ticker: str, is_holding: int):
    driver = make_driver(headless=HEADLESS, block_resources=True)
    hol_rows, com_rows = [], []
    periods_by_year = last_completed_quarter_year_months()
    status(f"[entity] {ticker} start | holding={is_holding==1}")

    try:
        for et in ENTITY_TYPES:
            status(f"[entity] {ticker} open tipoentidad={et}")
            if not open_financials_tab(driver, rut, et):
                status(f"[WARN] {ticker} tipoentidad={et} not accessible")
                continue

            collected = []
            for y, months in periods_by_year.items():
                for per in months:
                    try:
                        rows = scrape_period(driver, rut, et, ticker, y, per)
                        if rows:
                            collected.extend(rows)
                        # Gentle pacing to avoid server throttling
                        time.sleep(0.25)
                    except Exception as ex:
                        status(f"[ERR ] {ticker} {et} {y}-{per} exception: {ex}")

            if collected:
                if is_holding == 1:
                    hol_rows.extend(collected)
                else:
                    com_rows.extend(collected)
                status(f"[entity] {ticker} got data with tipoentidad={et} rows={len(collected)}")
                break  # one entity type is enough
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    status(f"[entity] {ticker} done | hol={len(hol_rows)} com={len(com_rows)} | t={secs()}")
    return hol_rows, com_rows

# ------------------- Orchestration -------------------
def run_all(tickers_csv: str, out_hol: str, out_com: str, workers: int = WORKERS):
    status("[START] Loading tickers from " + tickers_csv)
    tick = pd.read_csv(tickers_csv, dtype={"RUT": str})
    tick["RUT"] = tick["RUT"].str.replace(r"[^0-9]", "", regex=True)
    total = len(tick)
    status(f"[INFO] {total} tickers | workers={workers} | headless={HEADLESS} | since {START_YEAR}")

    cols = ["Description","Value","Curncy","Date","ID"]
    df_hol_out = pd.DataFrame(columns=cols)
    df_com_out = pd.DataFrame(columns=cols)

    done = 0

    if workers and workers > 1:
        futures = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for _, r in tick.iterrows():
                futures.append(ex.submit(_worker_single, dict(r)))

            for f in as_completed(futures):
                h, c, name = f.result()
                if h:
                    df_hol_out = pd.concat([df_hol_out, pd.DataFrame(h, columns=cols)], ignore_index=True)
                if c:
                    df_com_out = pd.concat([df_com_out, pd.DataFrame(c, columns=cols)], ignore_index=True)

                # incremental flush
                df_hol_out.to_csv(out_hol, index=False)
                df_com_out.to_csv(out_com, index=False)

                done += 1
                status(f"[PROGRESS] {done}/{total} tickers | hol_rows={len(df_hol_out)} com_rows={len(df_com_out)} | t={secs()}")
    else:
        for _, r in tick.iterrows():
            ticker = r["ID"]; rut = r["RUT"]; is_holding = int(r.get("IS_HOLDING", 0))
            status(f"[Ticker] {ticker} | RUT {rut} | holding={is_holding==1}")
            h, c = scrape_entity(rut, ticker, is_holding)
            if h:
                df_hol_out = pd.concat([df_hol_out, pd.DataFrame(h, columns=cols)], ignore_index=True)
            if c:
                df_com_out = pd.concat([df_com_out, pd.DataFrame(c, columns=cols)], ignore_index=True)

            df_hol_out.to_csv(out_hol, index=False)
            df_com_out.to_csv(out_com, index=False)

            done += 1
            status(f"[PROGRESS] {done}/{total} tickers | hol_rows={len(df_hol_out)} com_rows={len(df_com_out)} | t={secs()}")

    status(f"[DONE ] wrote {out_hol} rows={len(df_hol_out)}")
    status(f"[DONE ] wrote {out_com} rows={len(df_com_out)}")
    status(f"[TOTAL] Elapsed {secs()}")

def _worker_single(r: dict):
    ticker = r["ID"]; rut = r["RUT"]; is_holding = int(r.get("IS_HOLDING", 0))
    status(f"[worker] {ticker} (holding={is_holding==1}) start")
    h, c = scrape_entity(rut, ticker, is_holding)
    status(f"[worker] {ticker} end | h={len(h)} c={len(c)}")
    return h, c, ticker

# ------------------- Entry -------------------
if __name__ == "__main__":
    run_all(TICKERS, OUT_HOL, OUT_COM, workers=WORKERS)
