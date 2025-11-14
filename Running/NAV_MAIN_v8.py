"""Modern entry-point that executes the cohesive NAV pipeline."""
from __future__ import annotations

import argparse
from pathlib import Path

from nav_pipeline import PipelineConfig, run_pipeline


def build_config(args: argparse.Namespace) -> PipelineConfig:
    base = Path(args.base_dir).resolve() if args.base_dir else Path(__file__).resolve().parent
    output = Path(args.output_dir).resolve() if args.output_dir else base / "Outputs"
    cfg = PipelineConfig(base_dir=base, output_dir=output, start_year=args.start_year)
    if args.prices:
        cfg.overrides(prices_txt=args.prices)
    if args.fx:
        cfg.overrides(fx=args.fx)
    if args.ownership_hol:
        cfg.overrides(ownership_hol=args.ownership_hol)
    if args.ownership_com:
        cfg.overrides(ownership_com=args.ownership_com)
    if args.balance_company:
        cfg.overrides(bal_com=args.balance_company)
    if args.balance_holding:
        cfg.overrides(bal_hol=args.balance_holding)
    return cfg


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the NAV processing pipeline")
    parser.add_argument("--base-dir", help="Root directory that contains the Running assets")
    parser.add_argument("--output-dir", help="Directory for generated artefacts")
    parser.add_argument("--start-year", type=int, default=2017, help="First fiscal year to include (default: 2017)")
    parser.add_argument("--prices", help="Override path to lastPrices_Nav file")
    parser.add_argument("--fx", help="Override path to USDCLP FX file")
    parser.add_argument("--ownership-hol", help="Override path to holding ownership CSV")
    parser.add_argument("--ownership-com", help="Override path to subsidiary ownership CSV")
    parser.add_argument("--balance-company", help="Override path to company balance sheet CSV")
    parser.add_argument("--balance-holding", help="Override path to holding balance sheet CSV")
    parser.add_argument("--no-write", action="store_true", help="Skip writing CSV outputs (useful for dry-runs)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    cfg = build_config(args)
    run_pipeline(cfg, write_outputs=not args.no_write)


if __name__ == "__main__":  # pragma: no cover
    main()
