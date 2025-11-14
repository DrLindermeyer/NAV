"""Implementation of the NAV processing pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .config import PipelineConfig
from .utils import (
    clean_percent,
    ensure_period,
    last_per_group,
    log,
    read_expected_csv,
    summarize_trailing,
    to_numeric,
    wide_to_long_fill,
)


@dataclass(slots=True)
class NavPipelineResult:
    prices: pd.DataFrame
    price_ownership: pd.DataFrame
    prices_ownership: pd.DataFrame
    nav_aligned: pd.DataFrame
    nav_spot: pd.DataFrame
    summary: pd.DataFrame


class NavPipeline:
    """Co-ordinates the end-to-end NAV workflow."""

    def __init__(self, config: Optional[PipelineConfig] = None):
        self.cfg = config or PipelineConfig()
        self.state: Dict[str, pd.DataFrame] = {}
        self.start_year = self.cfg.start_year
        self.start_date = pd.Timestamp(self.start_year, 1, 1)
        self.start_quarter = pd.Period(f"{self.start_year}Q1")
        self._write_enabled = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run(self, write_outputs: bool = True) -> NavPipelineResult:
        self._write_enabled = write_outputs
        log("Loading market data …")
        self._load_prices()
        log("Loading reference inputs …")
        self._load_reference_inputs()
        log("Preparing balance sheet blocks …")
        self._prepare_balances()
        log("Calculating NetDebt timeline …")
        self._prepare_netdebt()
        log("Loading ownership disclosures …")
        self._load_ownership()
        log("Building look-through pricing …")
        self._prepare_price_ownership()
        log("Computing NAV time series …")
        self._prepare_nav_series()
        log("Deriving summary statistics …")
        self._build_summary()
        if write_outputs:
            log("Writing pipeline artefacts …")
            self._write_outputs()
        return NavPipelineResult(
            prices=self.state["prices"].copy(),
            price_ownership=self.state["price_ownership"].copy(),
            prices_ownership=self.state["prices_ownership"].copy(),
            nav_aligned=self.state["nav_aligned"].copy(),
            nav_spot=self.state["nav_spot"].copy(),
            summary=self.state["summary"].copy(),
        )

    # ------------------------------------------------------------------
    # Step helpers
    # ------------------------------------------------------------------
    def _load_prices(self) -> None:
        path = self.cfg.resolve("prices_txt")
        names = ["TICKER", "DATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]
        try:
            df = pd.read_csv(path, header=None, names=names)
            if df.shape[1] != 7:
                raise ValueError
        except Exception:
            for sep in (";", "\t", None):
                try:
                    df = pd.read_csv(path, header=None, names=names, sep=sep)
                    if df.shape[1] == 7:
                        break
                except Exception:
                    continue
            else:
                raise ValueError(f"Could not parse {path} with 7 columns")
        df = df[df["DATE"] != "DATE.trans(-)"]
        df["DATE"] = pd.to_datetime(df["DATE"], format="%Y%m%d", errors="coerce")
        df = df.dropna(subset=["DATE"])
        df = df[df["DATE"] >= self.start_date].copy()
        numeric_cols = ["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]
        df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce")
        prices = wide_to_long_fill(df[["TICKER", "DATE", "CLOSE"]], "DATE", "TICKER", "CLOSE")
        prices["Date_quarter"] = prices["DATE"].dt.to_period("Q")
        prices = prices.rename(columns={"DATE": "Date", "TICKER": "ID", "CLOSE": "Price"})
        self.state["raw_prices"] = df
        self.state["prices"] = prices
        self.state["trading_dates"] = df["DATE"].sort_values().drop_duplicates().reset_index(drop=True)

    def _load_reference_inputs(self) -> None:
        tickers = read_expected_csv(self.cfg.resolve("tickers"), ["ID", "RUT", "IS_HOLDING", "GROUP"])
        bal_cols = ["Description", "Value", "Curncy", "Date", "ID"]
        balance_comp = read_expected_csv(self.cfg.resolve("bal_com"), bal_cols)
        balance_hol = read_expected_csv(self.cfg.resolve("bal_hol"), bal_cols)
        balance = pd.concat([balance_comp, balance_hol], ignore_index=True)
        balance["Date"] = pd.to_datetime(balance["Date"], errors="coerce")
        balance = balance.dropna(subset=["Date"])
        balance = balance[balance["Date"] >= self.start_date].copy()
        balance["Value"] = (balance["Value"].replace("-", "0")
                             .str.replace(".", "", regex=False)
                             .str.replace(",", ".", regex=False))
        balance["Value"] = to_numeric(balance["Value"])
        fx = read_expected_csv(self.cfg.resolve("fx"), ["Date", "USDCLP"])
        fx["Date"] = pd.to_datetime(fx["Date"], errors="coerce")
        fx = fx.dropna(subset=["Date"])
        fx = fx[fx["Date"] >= self.start_date].copy()
        fx["USDCLP"] = fx["USDCLP"].astype(str).str.replace(",", "", regex=False)
        fx["USDCLP"] = to_numeric(fx["USDCLP"])
        netdebt_guide = read_expected_csv(
            self.cfg.resolve("netdebt_guide"),
            ["ID", "Description", "Balance2", "Holding"],
        )
        self.state.update({
            "tickers": tickers,
            "balance": balance,
            "fx": fx,
            "netdebt_guide": netdebt_guide,
            "id_holding": netdebt_guide[["Holding", "ID"]].drop_duplicates(),
        })

    def _prepare_balances(self) -> None:
        balance = self.state["balance"].copy()
        fx = self.state["fx"]
        balance.loc[balance["ID"].eq("ANTARCHILE"), "Curncy"] = "USD"
        balance_usd = balance[balance["Curncy"].eq("USD")].copy()
        balance_clp = balance[~balance["Curncy"].eq("USD")].copy()
        balance_usd = balance_usd.merge(fx, on="Date", how="inner")
        balance_usd["Value"] = balance_usd["Value"] * balance_usd["USDCLP"]
        balance_usd = balance_usd.drop(columns=["USDCLP"])
        balance_total = pd.concat([balance_clp, balance_usd], ignore_index=True)
        balance_total["Value"] = balance_total["Value"] / 1000.0
        balance_total = balance_total.drop(columns=["Curncy"], errors="ignore")
        self._save_csv(balance_total, "balance_total.csv")
        netdebt_guide = self.state["netdebt_guide"]
        tickers = self.state["tickers"]
        balance_sel = (balance_total
                       .merge(netdebt_guide, on=["ID", "Description"], how="inner")
                       .merge(tickers, on="ID", how="inner")
                       [["Date", "ID", "Holding", "IS_HOLDING", "Description", "Balance2", "Value"]])
        self._save_csv(balance_sel, "balance_selected_netdebt.csv")
        balance_sel["Value"] = np.where(balance_sel["IS_HOLDING"].astype(float) == -1,
                                         -balance_sel["Value"],
                                         balance_sel["Value"])
        balance_group = (balance_sel.groupby(["ID", "Date", "Balance2"], as_index=False)["Value"].sum())
        self._save_csv(balance_group, "balance_netdebt_comp_hol.csv")
        id_date_max = (balance_group[["ID", "Date"]].drop_duplicates()
                       .merge(tickers, on="ID", how="left")[["Date", "ID", "IS_HOLDING"]]
                       .drop_duplicates())
        hol_date_max = id_date_max[id_date_max["IS_HOLDING"].astype(float) == 1][["ID", "Date"]].copy()
        hol_date_max["Date_quarter"] = hol_date_max["Date"].dt.to_period("Q")
        hol_date_max = hol_date_max.drop(columns=["Date"])
        hol_date_max.columns = ["Holding", "Date_quarter"]
        self._save_csv(hol_date_max, "Holding_Max_Report.csv")
        balance_group_2 = (balance_group
                           .merge(self.state["id_holding"], on="ID", how="left")
                           .groupby(["Date", "Holding", "Balance2"], as_index=False)["Value"].sum())
        balance_group_2["NetDebt"] = np.where(balance_group_2["Balance2"].eq("Cash"),
                                               -balance_group_2["Value"],
                                               balance_group_2["Value"])
        netdebt = (balance_group_2.groupby(["Date", "Holding"], as_index=False)["NetDebt"].sum())
        netdebt["Date_quarter"] = netdebt["Date"].dt.to_period("Q")
        netdebt = netdebt.drop(columns=["Date"])
        self.state.update({
            "balance_total": balance_total,
            "balance_sel": balance_sel,
            "balance_group": balance_group,
            "hol_date_max": hol_date_max,
            "netdebt_raw": netdebt,
        })

    def _prepare_netdebt(self) -> None:
        netdebt = self.state["netdebt_raw"].copy()
        ext_path = self.cfg.resolve("netdebt_external")
        if ext_path.exists():
            ext = pd.read_csv(ext_path, sep=";")
            need = {"Holding", "Date_quarter", "NetDebt"}
            if need.issubset(ext.columns):
                ext["NetDebt"] = ext["NetDebt"].astype(str).str.replace(",", "", regex=True).str.strip()
                ext["NetDebt"] = to_numeric(ext["NetDebt"])
                ext["Date_quarter"] = pd.PeriodIndex(ext["Date_quarter"], freq="Q")
                ext = ext[ext["Date_quarter"] >= self.start_quarter].copy()
                netdebt = ext[["Holding", "Date_quarter", "NetDebt"]].copy()
            else:
                log(f"[WARN] {ext_path} missing required columns {need - set(ext.columns)}")
        netdebt["Date_quarter"] = ensure_period(netdebt["Date_quarter"])
        netdebt = netdebt[netdebt["Date_quarter"] >= self.start_quarter].copy()
        self.state["netdebt"] = netdebt
        valid = netdebt.loc[netdebt["NetDebt"].notna(), "Date_quarter"]
        if valid.empty:
            min_date_final = self.state["prices"]["Date"].min()
        else:
            min_date_final = valid.min().start_time
        self.state["min_date_final"] = min_date_final
        dates_holding = self.state["prices"].merge(self.state["id_holding"], on="ID", how="inner")[["Date", "Date_quarter", "Holding"]]
        dates_holding = dates_holding[dates_holding["Date"] >= min_date_final].copy()
        netdebt_final = dates_holding.merge(netdebt, on=["Holding", "Date_quarter"], how="left")
        netdebt_final["NetDebt"] = netdebt_final.groupby("Holding")["NetDebt"].ffill()
        netdebt_final = netdebt_final.drop_duplicates().reset_index(drop=True)
        self.state["netdebt_final"] = netdebt_final
        self.state["dates_holding"] = dates_holding

    def _load_ownership(self) -> None:
        own_cols = ["Holding", "pct_holding", "eqy_shares", "eqy_shares_total", "Date", "ID"]
        ownership_hol = read_expected_csv(self.cfg.resolve("ownership_hol"), own_cols)
        ownership_hol["Date"] = pd.to_datetime(ownership_hol["Date"], errors="coerce")
        ownership_hol = ownership_hol.dropna(subset=["Date"])
        ownership_hol = ownership_hol[ownership_hol["Date"] >= self.start_date].copy()
        ownership_hol["Date_quarter"] = ownership_hol["Date"].dt.to_period("Q")
        holding_shares = ownership_hol[["ID", "Date_quarter", "eqy_shares_total"]].copy()
        holding_shares["eqy_shares_total"] = to_numeric(holding_shares["eqy_shares_total"]) / 1_000_000
        holding_shares = holding_shares.drop_duplicates()
        holding_shares.columns = ["Holding", "Date_quarter", "eqy_shares_total"]
        holding_shares_dt = self.state["dates_holding"].merge(holding_shares, on=["Holding", "Date_quarter"], how="left")
        holding_shares_final = holding_shares_dt.copy()
        holding_shares_final["eqy_shares_total"] = holding_shares_final.groupby("Holding")["eqy_shares_total"].ffill()
        holding_shares_final = holding_shares_final.drop_duplicates().reset_index(drop=True)
        ownership_com = read_expected_csv(self.cfg.resolve("ownership_com"), own_cols)
        ownership_com["Date"] = pd.to_datetime(ownership_com["Date"], errors="coerce")
        ownership_com = ownership_com.dropna(subset=["Date"])
        ownership_com = ownership_com[ownership_com["Date"] >= self.start_date].copy()
        holdings_dict = {
            "INV ALTEL LTDA": "ALMENDRAL",
            "ALMENDRAL S A": "ALMENDRAL",
            "INV AGUAS METROPOLITANAS S A": "IAM",
            "INVERCAP S.A.": "INVERCAP",
            "INVERCAP SA": "INVERCAP",
            "ANTARCHILE S.A.": "ANTARCHILE",
            "SOCIEDAD DE INVERSIONES PAMPA CALICHERA SA": "PAMPA_CALICHERA",
            "PAMPA CALICHERA S.A.": "PAMPA_CALICHERA",
            "GLOBAL MINING SPA": "GLOBAL_MINING",
            "SOCIEDAD DE INVERSIONES ORO BLANCO SA": "ORO_BLANCO",
            "ORO BLANCO S.A.": "ORO_BLANCO",
            "POTASIOS DE CHILE SA": "POTASIOS",
            "POTASIOS S.A.": "POTASIOS",
        }
        ownership_com = ownership_com[ownership_com["Holding"].isin(holdings_dict.keys())].copy()
        ownership_com["Holding"] = ownership_com["Holding"].map(holdings_dict)
        ownership_com = (ownership_com
                         .merge(self.state["tickers"], on="ID", how="inner")
                         [["Date", "ID", "Holding", "pct_holding", "eqy_shares_total"]])
        ownership_com["eqy_shares_total"] = to_numeric(ownership_com["eqy_shares_total"]) / 1_000_000
        ownership_com["Date_quarter"] = ownership_com["Date"].dt.to_period("Q")
        ownership_com = ownership_com.drop(columns=["Date"])
        self.state.update({
            "ownership_hol": ownership_hol,
            "holding_shares": holding_shares,
            "holding_shares_final": holding_shares_final,
            "ownership_com": ownership_com,
        })

    def _prepare_price_ownership(self) -> None:
        prices = self.state["prices"].copy()
        id_holding = self.state["id_holding"].copy()
        ownership_com, eff_pct = self._augment_sqm_holdings(self.state["ownership_com"].copy())
        id_holding_2 = read_expected_csv(self.cfg.resolve("id_holding_2"), ["ID", "Holding"], sep=";")
        prices_final = prices.merge(id_holding_2, on="ID", how="inner")
        holdings = id_holding_2["Holding"].unique()
        prices_final = prices_final[~prices_final["ID"].isin(holdings)].copy()
        ownership_com["Date_quarter"] = ensure_period(ownership_com["Date_quarter"])
        prices_final["Date_quarter"] = ensure_period(prices_final["Date_quarter"])
        merged = prices_final.merge(ownership_com, on=["ID", "Holding", "Date_quarter"], how="left")
        min_date = merged.loc[merged["pct_holding"].notna(), "Date"].min()
        if pd.notnull(min_date):
            merged = merged[merged["Date"] >= min_date].copy()
        merged["pct_holding"] = merged.groupby("ID")["pct_holding"].ffill()
        merged["eqy_shares_total"] = merged.groupby("ID")["eqy_shares_total"].ffill()
        merged["MarketCap_default"] = merged["Price"] * merged["eqy_shares_total"]
        mkcap_sqm_total = self._build_sqm_market_caps(merged)
        merged["MarketCap"] = merged["MarketCap_default"]
        if not mkcap_sqm_total.empty:
            is_sqm = merged["ID"].isin(["SQM-A", "SQM-B"])
            is_a = merged["ID"].eq("SQM-A")
            is_b = merged["ID"].eq("SQM-B")
            merged.loc[is_b, "MarketCap"] = merged.loc[is_b, "Date"].map(mkcap_sqm_total)
            merged.loc[is_a, "MarketCap"] = 0.0
            merged.loc[~is_sqm, "MarketCap"] = merged.loc[~is_sqm, "MarketCap_default"]
        merged["pct_holding"] = clean_percent(merged["pct_holding"])
        merged["HoldingValue"] = merged["MarketCap"] * merged["pct_holding"]
        self._save_csv(merged, "price_ownership.csv")
        prices_ownership = merged.groupby(["Date", "Date_quarter", "Holding"], as_index=False)["HoldingValue"].sum()
        prices_ownership = prices_ownership.drop_duplicates()
        self._save_csv(prices_ownership, "prices_ownership.csv")
        self.state["eff_obl_company_pct"] = eff_pct
        self.state["mkcap_sqm_total"] = mkcap_sqm_total
        self.state.update({
            "price_ownership": merged,
            "prices_ownership": prices_ownership,
            "prices_with_holdings": prices.merge(id_holding, on="ID", how="inner"),
        })

    def _augment_sqm_holdings(self, ownership_com: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
        own = ownership_com.copy()
        own["Date_quarter"] = own["Date_quarter"].astype(str)
        def pick_series_pct(holder: str, issuer: str) -> pd.Series:
            s = (own[(own["Holding"] == holder) & (own["ID"] == issuer)]
                    [["Date_quarter", "pct_holding"]]
                    .drop_duplicates("Date_quarter")
                    .set_index("Date_quarter")["pct_holding"])
            s = clean_percent(s)
            return s
        pampa_A = pick_series_pct("PAMPA_CALICHERA", "SQM-A")
        pampa_B = pick_series_pct("PAMPA_CALICHERA", "SQM-B")
        global_A = pick_series_pct("GLOBAL_MINING", "SQM-A")
        global_B = pick_series_pct("GLOBAL_MINING", "SQM-B")
        obl_A = pick_series_pct("ORO_BLANCO", "SQM-A")
        obl_B = pick_series_pct("ORO_BLANCO", "SQM-B")
        overrides_path = self.cfg.resolve("series_shares_overrides")
        overrides = pd.read_csv(overrides_path, dtype={"ID": str, "Date_quarter": str, "eqy_shares_total": float}) if overrides_path.exists() else pd.DataFrame(columns=["ID", "Date_quarter", "eqy_shares_total"])
        def series_shares_quarterly(ticker: str) -> pd.Series:
            ovr = overrides[overrides["ID"] == ticker].set_index("Date_quarter")["eqy_shares_total"] if not overrides.empty else pd.Series(dtype=float)
            scr = (own[own["ID"] == ticker]
                     .dropna(subset=["eqy_shares_total"])
                     .groupby("Date_quarter")["eqy_shares_total"].max())
            scr = to_numeric(scr)
            missing = scr[~scr.index.isin(ovr.index)]
            s = pd.concat([ovr, missing], axis=0).sort_index()
            q_all = own["Date_quarter"].drop_duplicates().sort_values()
            return s.reindex(q_all).ffill()
        sh_A = series_shares_quarterly("SQM-A")
        sh_B = series_shares_quarterly("SQM-B")
        tot_sh = (sh_A + sh_B).replace(0, np.nan)
        def weighted_company_pct(pct_A: pd.Series, pct_B: pd.Series) -> pd.Series:
            sA = pct_A.reindex(tot_sh.index).fillna(0.0)
            sB = pct_B.reindex(tot_sh.index).fillna(0.0)
            num = (sA * sh_A).fillna(0.0) + (sB * sh_B).fillna(0.0)
            return (num / tot_sh).fillna(0.0)
        pampa_total = weighted_company_pct(pampa_A, pampa_B)
        global_total = weighted_company_pct(global_A, global_B)
        obl_dir = weighted_company_pct(obl_A, obl_B)
        obl_pampa = (own[(own["Holding"] == "ORO_BLANCO") & (own["ID"] == "PAMPA_CALICHERA")]
                       [["Date_quarter", "pct_holding"]]
                       .drop_duplicates("Date_quarter")
                       .set_index("Date_quarter")["pct_holding"])
        obl_pampa = clean_percent(obl_pampa).reindex(tot_sh.index).fillna(method="ffill").fillna(0.0)
        default_link = 0.8864
        if (obl_pampa.dropna().abs().median() < 0.01) or (obl_pampa.dropna().max() < 0.05):
            log("[WARN] OBL->PAMPA missing or too small; applying fallback link 0.8864")
            obl_pampa = pd.Series(default_link, index=tot_sh.index)
        pampa_plus_global = pampa_total.reindex(tot_sh.index).fillna(0.0) + global_total.reindex(tot_sh.index).fillna(0.0)
        eff_pct = obl_pampa * pampa_plus_global + obl_dir
        eff_pct.name = "pct_holding"
        eff_df = eff_pct.reset_index().rename(columns={"index": "Date_quarter"})
        eff_df["Holding"] = "ORO_BLANCO"
        eff_df["ID"] = "SQM-B"
        issuer_shares = (own[own["ID"] == "SQM-B"]
                          .dropna(subset=["eqy_shares_total"])
                          .groupby("Date_quarter")["eqy_shares_total"].max())
        eff_df["eqy_shares_total"] = eff_df["Date_quarter"].map(issuer_shares)
        ownership_com = ownership_com[~((ownership_com["Holding"] == "ORO_BLANCO") &
                                        (ownership_com["ID"].isin(["SQM-A", "SQM-B"])))]
        eff_df["Date_quarter"] = eff_df["Date_quarter"].astype(str)
        ownership_com = pd.concat([ownership_com, eff_df], ignore_index=True)
        ownership_com["Date_quarter"] = pd.PeriodIndex(ownership_com["Date_quarter"], freq="Q")
        return ownership_com, eff_pct

    def _build_sqm_market_caps(self, df: pd.DataFrame) -> pd.Series:
        if df is None or df.empty:
            return pd.Series(dtype=float)
        mask = df["ID"].isin(["SQM-A", "SQM-B"])
        sqm = df[mask].copy()
        sqm["MarketCap_default"] = sqm["Price"] * sqm["eqy_shares_total"]
        sqm = sqm.sort_values(["Date", "ID"])
        by_date = sqm.groupby(["Date", "ID"], as_index=False)["MarketCap_default"].sum()
        pivot = by_date.pivot(index="Date", columns="ID", values="MarketCap_default").fillna(0.0)
        pivot["SQM_TOTAL"] = pivot.sum(axis=1)
        return pivot["SQM_TOTAL"]

    def _prepare_nav_series(self) -> None:
        prices_ownership = self.state["prices_ownership"].copy()
        netdebt_final = self.state["netdebt_final"].copy()
        holding_shares_final = self.state["holding_shares_final"].copy()
        prices_final = self.state["prices_with_holdings"][["Date", "Date_quarter", "Holding", "Price"]].copy()
        nav_base = (prices_ownership.merge(netdebt_final, on=["Holding", "Date", "Date_quarter"], how="inner")
                                     [["Date", "Date_quarter", "Holding", "HoldingValue", "NetDebt"]])
        nav_base = nav_base.merge(holding_shares_final, on=["Holding", "Date", "Date_quarter"], how="inner")
        nav_base = self._apply_obl_shares_override(nav_base)
        self.state["nav_base"] = nav_base
        netdebt = self.state["netdebt"].copy()
        holding_shares = self.state["holding_shares"].copy()
        last_q_nd = netdebt.dropna(subset=["NetDebt"]).groupby("Holding")["Date_quarter"].max()
        last_q_sh = holding_shares.dropna(subset=["eqy_shares_total"]).groupby("Holding")["Date_quarter"].max()
        last_q_ok: Dict[str, pd.Period] = {}
        for holding in holding_shares["Holding"].unique():
            q_nd = last_q_nd.get(holding, pd.Period("1900Q1"))
            q_sh = last_q_sh.get(holding, pd.Period("1900Q1"))
            last_q_ok[holding] = min(q_nd, q_sh)
        nav_aligned = nav_base.copy()
        mask = nav_aligned.apply(lambda r: r["Date_quarter"] <= last_q_ok.get(r["Holding"], pd.Period("1900Q1")), axis=1)
        nav_aligned = nav_aligned[mask].copy()
        nav_aligned["NAV"] = nav_aligned["HoldingValue"] - nav_aligned["NetDebt"]
        nav_aligned["NAV_PS"] = nav_aligned["NAV"] / nav_aligned["eqy_shares_total"]
        nav_aligned = nav_aligned.merge(prices_final, on=["Holding", "Date", "Date_quarter"], how="left")
        nav_aligned["Discount"] = 1 - (nav_aligned["Price"] / nav_aligned["NAV_PS"])
        trading_dates = self.state["trading_dates"]
        nav_aligned = nav_aligned.sort_values(["Holding", "Date"])
        nav_aligned = nav_aligned[nav_aligned["Date"].isin(trading_dates)].reset_index(drop=True)
        nav_aligned["NetDebt_ffill"] = nav_aligned.groupby("Holding")["NetDebt"].ffill()
        nav_aligned["NAV"] = nav_aligned["HoldingValue"] - nav_aligned["NetDebt_ffill"]
        nav_aligned["NAV_PS"] = nav_aligned["NAV"] / nav_aligned["eqy_shares_total"]
        nav_aligned["Discount"] = 1 - (nav_aligned["Price"] / nav_aligned["NAV_PS"])
        last_fin = (nav_aligned.dropna(subset=["NetDebt"])
                                 .sort_values(["Holding", "Date"])
                                 .groupby("Holding", as_index=False)["Date"].last()
                                 .rename(columns={"Date": "LastFinDate"}))
        nav_aligned = nav_aligned.merge(last_fin, on="Holding", how="left")
        nav_aligned["DaysSinceFin"] = (nav_aligned["Date"] - nav_aligned["LastFinDate"]).dt.days
        nav_aligned["StaleFund"] = nav_aligned["DaysSinceFin"].gt(90)
        self.state["nav_aligned"] = nav_aligned
        nav_spot = nav_base.copy()
        nav_spot["NAV"] = nav_spot["HoldingValue"] - nav_spot["NetDebt"]
        nav_spot["NAV_PS"] = nav_spot["NAV"] / nav_spot["eqy_shares_total"]
        nav_spot = nav_spot.merge(prices_final, on=["Holding", "Date", "Date_quarter"], how="left")
        max_staleness = 90
        q_end = nav_spot["Date_quarter"].dt.end_time
        nav_spot["stale"] = (nav_spot["Date"] - q_end).dt.days > max_staleness
        nav_spot["Discount"] = 1 - (nav_spot["Price"] / nav_spot["NAV_PS"])
        nav_spot.loc[nav_spot["stale"], ["NAV", "NAV_PS", "Discount"]] = np.nan
        nav_spot = nav_spot.sort_values(["Holding", "Date"])
        nav_spot = nav_spot[nav_spot["Date"].isin(trading_dates)].reset_index(drop=True)
        nav_spot = self._apply_obl_nav_fix(nav_spot)
        nav_spot = self._apply_quinenco_override(nav_spot)
        self.state["nav_spot"] = nav_spot

    def _apply_obl_shares_override(self, df: pd.DataFrame, override: int = 212_511_108) -> pd.DataFrame:
        cur = df.loc[df["Holding"].eq("ORO_BLANCO"), "eqy_shares_total"].dropna()
        if not cur.empty:
            median_val = float(cur.median())
        else:
            median_val = 100.0
        if median_val > 1_000_000:
            scale = 1.0
            unit = "shares"
        elif median_val > 1_000:
            scale = 1_000.0
            unit = "thousands"
        else:
            scale = 1_000_000.0
            unit = "millions"
        override_val = override / scale
        mask = df["Holding"].eq("ORO_BLANCO")
        df.loc[mask, "eqy_shares_total"] = override_val
        log(f"[INFO] ORO_BLANCO shares override applied ({unit} scale)")
        return df

    def _apply_obl_nav_fix(self, nav_spot: pd.DataFrame) -> pd.DataFrame:
        eff_pct = self.state.get("eff_obl_company_pct")
        mkcap_sqm_total = self.state.get("mkcap_sqm_total")
        if eff_pct is None or mkcap_sqm_total is None or eff_pct.empty:
            return nav_spot
        mask_obl = nav_spot["Holding"].eq("ORO_BLANCO")
        if not mask_obl.any():
            return nav_spot
        obl = nav_spot.loc[mask_obl, ["Date", "Date_quarter", "NetDebt", "Price"]].copy()
        if not isinstance(eff_pct.index, pd.PeriodIndex):
            eff_pct.index = pd.PeriodIndex(eff_pct.index, freq="Q")
        obl["eff_pct"] = obl["Date_quarter"].map(eff_pct)
        obl["mkcap_total"] = obl["Date"].map(mkcap_sqm_total)
        obl = obl.dropna(subset=["eff_pct", "mkcap_total"])
        if obl.empty:
            return nav_spot
        shares_override = 212_511_108
        obl["NAV_fix"] = obl["eff_pct"] * obl["mkcap_total"] - obl["NetDebt"]
        obl["NAV_PS_fix"] = obl["NAV_fix"] / shares_override
        nav_spot = nav_spot.merge(obl[["Date", "NAV_fix", "NAV_PS_fix"]], on="Date", how="left")
        has_fix = mask_obl & nav_spot["NAV_fix"].notna()
        nav_spot.loc[has_fix, "NAV"] = nav_spot.loc[has_fix, "NAV_fix"]
        nav_spot.loc[has_fix, "NAV_PS"] = nav_spot.loc[has_fix, "NAV_PS_fix"]
        nav_spot.loc[has_fix, "Discount"] = 1 - (nav_spot.loc[has_fix, "Price"] / nav_spot.loc[has_fix, "NAV_PS"])
        nav_spot.drop(columns=["NAV_fix", "NAV_PS_fix"], inplace=True, errors="ignore")
        log("[INFO] Applied ORO_BLANCO NAV retro-fix using SQM look-through")
        return nav_spot

    def _apply_quinenco_override(self, nav_spot: pd.DataFrame) -> pd.DataFrame:
        path = self.cfg.resolve("quinenco_nav")
        if not path.exists():
            return nav_spot
        quinenco = pd.read_csv(path)
        need = {"Date", "NAV_per_share", "Price", "Discount"}
        if not need.issubset(quinenco.columns):
            return nav_spot
        quinenco["Date"] = pd.to_datetime(quinenco["Date"], errors="coerce")
        quinenco = quinenco.dropna(subset=["Date"])
        quinenco = quinenco.sort_values("Date")
        quinenco = quinenco.rename(columns={"NAV_per_share": "NAV_PS"})
        quinenco["Holding"] = "QUINENCO"
        nav_spot = nav_spot.merge(quinenco[["Date", "NAV_PS", "Price", "Discount"]],
                                  on="Date", how="left", suffixes=("", "_quin"))
        mask = nav_spot["Holding"].eq("QUINENCO") & nav_spot["NAV_PS_quin"].notna()
        if mask.any():
            nav_spot.loc[mask, "NAV_PS"] = nav_spot.loc[mask, "NAV_PS_quin"]
            nav_spot.loc[mask, "Price"] = nav_spot.loc[mask, "Price_quin"]
            nav_spot.loc[mask, "Discount"] = nav_spot.loc[mask, "Discount_quin"]
            nav_spot.drop(columns=["NAV_PS_quin", "Price_quin", "Discount_quin"], inplace=True)
            log("[INFO] QUINENCO NAV override applied from publisher dataset")
        else:
            nav_spot.drop(columns=["NAV_PS_quin", "Price_quin", "Discount_quin"], inplace=True, errors="ignore")
        return nav_spot

    def _build_summary(self) -> None:
        nav_aligned = self.state["nav_aligned"].copy()
        summary_cols = ["Date", "Price", "HoldingValue", "NetDebt_ffill", "NAV", "NAV_PS", "Discount", "DaysSinceFin", "StaleFund"]
        summary = last_per_group(nav_aligned, ["Holding"], ["Date"])
        summary = summary[["Holding"] + [c for c in summary_cols if c in summary.columns]]
        if "Date" in summary.columns:
            summary["Date"] = pd.to_datetime(summary["Date"], errors="coerce")
        if not nav_aligned.empty:
            asof = pd.to_datetime(nav_aligned["Date"].max())
            stats = summarize_trailing(nav_aligned[["Holding", "Date", "Discount"]].dropna(), asof=asof)
            summary = summary.merge(stats, on=["Holding", "Date", "Discount"], how="left")
        self._save_csv(summary, "summary.csv")
        self.state["summary"] = summary

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------
    def _save_csv(self, df: pd.DataFrame, name: str) -> None:
        if not self._write_enabled:
            return
        path = self.cfg.output_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False, encoding="utf-8-sig")
        log(f"[WRITE] {path}")

    def _write_outputs(self) -> None:
        self._save_csv(self.state["nav_aligned"], "nav_aligned.csv")
        self._save_csv(self.state["nav_spot"], "nav_spot.csv")
        self._save_csv(self.state["nav_aligned"], "nav.csv")


def run_pipeline(config: Optional[PipelineConfig] = None, *, write_outputs: bool = True) -> NavPipelineResult:
    """Convenience wrapper that instantiates and executes :class:`NavPipeline`."""
    pipeline = NavPipeline(config=config)
    return pipeline.run(write_outputs=write_outputs)
