"""Configuration helpers for the NAV pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable


DEFAULT_FILES: Dict[str, str] = {
    "prices_txt": "lastPrices_Nav_corrected.txt",
    "tickers": "tickers.csv",
    "bal_com": "balance_sheets_com_db.csv",
    "bal_hol": "balance_sheets_hol_db.csv",
    "fx": "usdclp.csv",
    "netdebt_guide": "balance_netdebt.csv",
    "ownership_hol": "ownership_data_hol_db.csv",
    "ownership_com": "ownership_data_com_db.csv",
    "id_holding_2": "id_holding_2.csv",
    "netdebt_external": "netdebt.csv",
    "series_shares_overrides": "series_shares_overrides.csv",
    "quinenco_nav": "quinenco_nav_processed.csv",
    "quinenco_prices": "quinenco_prices.py",  # legacy diagnostics
}


@dataclass(slots=True)
class PipelineConfig:
    """Runtime configuration for the NAV pipeline."""

    base_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    output_dir: Path | None = None
    start_year: int = 2017
    files: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_FILES))

    def __post_init__(self) -> None:
        if not isinstance(self.base_dir, Path):
            self.base_dir = Path(self.base_dir)
        if self.output_dir is None:
            self.output_dir = self.base_dir / "Outputs"
        elif not isinstance(self.output_dir, Path):
            self.output_dir = Path(self.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def resolve(self, key: str) -> Path:
        if key not in self.files:
            raise KeyError(f"Unknown config key: {key}")
        candidate = Path(self.files[key])
        if candidate.is_absolute():
            return candidate
        return self.base_dir / candidate

    def overrides(self, **kwargs: str) -> None:
        """Convenience helper to override file names in-place."""
        for k, v in kwargs.items():
            self.files[k] = v

    def iter_existing(self, keys: Iterable[str]) -> Dict[str, Path]:
        out: Dict[str, Path] = {}
        for k in keys:
            try:
                path = self.resolve(k)
            except KeyError:
                continue
            if path.exists():
                out[k] = path
        return out
