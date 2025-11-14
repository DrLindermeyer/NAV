# balance_sheets_selenium_xpath_only.py
# Scrapes CMF Balance Sheet using ONLY a fixed XPath for the results table.

import time, calendar, re
from datetime import date, datetime
import pandas as pd

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# ------------------- Config -------------------
BASE = "https://www.cmfchile.cl/institucional/mercados/entidad.php"
P_TAB = 3
CANDIDATE_TYPES = ["RVSOC", "RVEMI"]
PERIODS = ["03", "06", "09", "12"]
START_YEAR = 2017
THIS_YEAR = date.today().year

TIPO_PRIORITY = ["C", "I"]
FRAMEWORK_VALUE = "IFRS"

# Filters
SEL_MONTH      = "#mm"
SEL_YEAR       = "#aa"
SEL_TIPO       = "#fm > div:nth-child(3) > select"
SEL_FRAMEWORK  = "#fila_tipo > select"
SEL_CONSULTAR  = "#fm > div.fieldset.submit > input"

# *** Use ONLY this XPath for the output table ***
TABLE_XPATH = "/html/body/div[2]/div[2]/div/div/div/div[3]/table[2]"

HEADLESS_DEFAULT = True
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

def log(*msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}]", *msg, flush=True)

def month_last_day(y: int, mm: int) -> int:
    return calendar.monthrange(y, mm)[1]

def parse_currency_from_header(driver) -> str:
    try:
        html = driver.page_source
        m = re.search(r"Moneda:\s*([A-Z]{3,4})", html)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "CLP"

def set_select_value(sel_el, value, visible_fallback=None) -> bool:
    s = Select(sel_el)
    try:
        s.select_by_value(value); return True
    except Exception:
        if visible_fallback:
            try:
                s.select_by_visible_text(visible_fallback); return True
            except Exception:
                return False
        return False

def open_financials_tab(driver, rut: str, entity_type: str) -> bool:
    url = f"{BASE}?mercado=V&rut={rut}&grupo=&tipoentidad={entity_type}&vig=VI&control=svs&pestania={P_TAB}"
    driver.get(url)
    try:
        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#contenido"))
        )
        return True
    except TimeoutException:
        return False

def select_period_and_filters(driver, year: int, per: str, tipo: str):
    # Month
    m_el = WebDriverWait(driver, 8).until(EC.presence_of_element_located((By.CSS_SELECTOR, SEL_MONTH)))
    set_select_value(m_el, per, per)
    # Year
    y_el = WebDriverWait(driver, 8).until(EC.presence_of_element_located((By.CSS_SELECTOR, SEL_YEAR)))
    set_select_value(y_el, str(year), str(year))
    # Tipo C/I
    t_el = WebDriverWait(driver, 8).until(EC.presence_of_element_located((By.CSS_SELECTOR, SEL_TIPO)))
    set_select_value(t_el, tipo, tipo)
    # Framework IFRS
    f_el = WebDriverWait(driver, 8).until(EC.presence_of_element_located((By.CSS_SELECTOR, SEL_FRAMEWORK)))
    set_select_value(f_el, FRAMEWORK_VALUE, FRAMEWORK_VALUE)
    # Click Consultar
    try:
        btn = WebDriverWait(driver, 8).until(EC.element_to_be_clickable((By.CSS_SELECTOR, SEL_CONSULTAR)))
        btn.click()
    except Exception:
        pass

def get_table_by_xpath(driver, timeout=12):
    """Return (table_element, rows, header_th_texts) using ONLY the fixed XPath."""
    try:
        table = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.XPATH, TABLE_XPATH))
        )
    except TimeoutException:
        return None, [], []

    # headers
    ths = table.find_elements(By.CSS_SELECTOR, "th")
    header_texts = [th.text.strip() for th in ths if th.text.strip()]

    # body rows
    try:
        tbody = table.find_element(By.TAG_NAME, "tbody")
        rows = tbody.find_elements(By.TAG_NAME, "tr")
    except NoSuchElementException:
        rows = table.find_elements(By.CSS_SELECTOR, "tr")

    return table, rows, header_texts

def parse_table_for_selected_date(rows, target_col_idx: int):
    out = []
    for r in rows:
        tds = r.find_elements(By.TAG_NAME, "td")
        if len(tds) < (target_col_idx + 1):
            continue
        desc = tds[0].text.strip()
        val  = tds[target_col_idx].text.strip()
        if not desc:
            continue
        out.append({"Description": desc, "Value": val})
    return out

def scrape_one_period_try(driver, rut: str, entity_type: str, ticker: str, year: int, per: str, tipo: str):
    log(f"      set filters (mm={per}, aa={year}, tipo={tipo}, IFRS)")
    select_period_and_filters(driver, year, per, tipo)

    # find the table strictly by XPath
    table, rows, header_texts = get_table_by_xpath(driver, timeout=12)
    if table is None:
        log("      ! table XPath not found; skipping")
        return []

    if "Sin datos para este periodo" in driver.page_source:
        log("      · page indicates no data for this period")
        return []

    # choose which column to read
    last_day = month_last_day(year, int(per))
    wanted_full = f"{year}-{per}-{str(last_day).zfill(2)}"
    wanted_mm = f"{year}-{per}"

    # usually the two last THs are dates: [desc | yyyy-mm-dd | yyyy-mm-dd]
    # default to first number column (index 1)
    target_col_idx = 1
    if len(header_texts) >= 3:
        # find the last 2 ths that look like dates
        date_cells = [t for t in header_texts if DATE_RE.search(t) or wanted_mm in t]
        if len(date_cells) >= 2:
            left, right = date_cells[-2], date_cells[-1]
            if left.startswith(wanted_mm) or left == wanted_full:
                target_col_idx = 1
            elif right.startswith(wanted_mm) or right == wanted_full:
                target_col_idx = 2

    curncy = parse_currency_from_header(driver)
    log(f"      headers={header_texts[-3:]} | picking col idx={target_col_idx} | ccy={curncy}")

    body = parse_table_for_selected_date(rows, target_col_idx)
    if not body:
        log("      · parsed 0 lines")
        return []

    dt = wanted_full
    out = [{
        "Description": r["Description"],
        "Value": r["Value"],
        "Curncy": curncy,
        "Date": dt,
        "ID": ticker
    } for r in body]

    log(f"      · parsed {len(out)} lines (tipo={tipo})")
    return out

def scrape_one_period(driver, rut: str, entity_type: str, ticker: str, year: int, per: str):
    log(f"    · {year}-{per} → open tab")
    ok = open_financials_tab(driver, rut, entity_type)
    if not ok:
        log("      ! cannot open financials tab")
        return []
    for tipo in TIPO_PRIORITY:
        rows = scrape_one_period_try(driver, rut, entity_type, ticker, year, per, tipo)
        if rows:
            log(f"      ✓ using tipo={tipo}")
            return rows
        log("      ↩ fallback to next tipo (if any)")
    log("      × no data for both tipos (C & I)")
    return []

def scrape_one_entity(driver, rut: str, ticker: str, is_holding: int):
    for et in CANDIDATE_TYPES:
        log(f"  tipoentidad={et} → open")
        if not open_financials_tab(driver, rut, et):
            log(f"  [skip] tipoentidad={et} not accessible"); continue
        all_rows = []
        for year in range(START_YEAR, THIS_YEAR + 1):
            log(f"  Year {year}")
            for per in PERIODS:
                try:
                    rows = scrape_one_period(driver, rut, et, ticker, year, per)
                    if rows: all_rows.extend(rows)
                except Exception as ex:
                    log(f"    ! ERROR {year}-{per}: {ex}")
        if all_rows:
            log(f"  ✓ gathered {len(all_rows)} rows with tipoentidad={et}")
            return all_rows
    log("  × no data for any tipoentidad")
    return []

def run_selenium(tickers_csv: str, out_hol: str, out_com: str, headless: bool = HEADLESS_DEFAULT):
    tick = pd.read_csv(tickers_csv, dtype={"RUT": str})
    tick["RUT"] = tick["RUT"].str.replace(r"[^0-9]", "", regex=True)

    options = webdriver.ChromeOptions()
    if headless: options.add_argument("--headless=new")
    options.add_argument("--window-size=1320,950")
    options.add_argument("--disable-gpu"); options.add_argument("--no-sandbox")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

    records_hol, records_com = [], []
    try:
        for _, row in tick.iterrows():
            ticker = row["ID"]; rut = row["RUT"]; is_holding = int(row["IS_HOLDING"])
            log(f"Ticker {ticker} (RUT {rut}) | holding={is_holding == 1}")
            rows = scrape_one_entity(driver, rut, ticker, is_holding)
            if not rows:
                log("  [WARN] No balance-sheet data for this ticker")
            else:
                (records_hol if is_holding == 1 else records_com).extend(rows)
            time.sleep(0.2)
    finally:
        driver.quit()

    cols = ["Description","Value","Curncy","Date","ID"]
    pd.DataFrame(records_hol, columns=cols).to_csv(out_hol, index=False)
    pd.DataFrame(records_com, columns=cols).to_csv(out_com, index=False)
    log(f"[INFO] Saved {len(records_hol)} rows -> {out_hol}")
    log(f"[INFO] Saved {len(records_com)} rows -> {out_com}")

if __name__ == "__main__":
    TICKERS = "tickers.csv"
    OUT_HOL = "balance_sheets_hol_db.csv"
    OUT_COM = "balance_sheets_com_db.csv"
    run_selenium(TICKERS, OUT_HOL, OUT_COM, headless=True)
