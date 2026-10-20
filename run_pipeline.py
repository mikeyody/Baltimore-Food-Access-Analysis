"""Rebuild the whole project with one command.

    python run_pipeline.py              # run every step in order
    python run_pipeline.py load test    # run only the named step(s)

Steps:
    download   fetch raw data into data/raw/ (skips files already present)
    inventory  write reports/data_sources.md from the downloaded files
    load       run sql/01-04 to build data/baltimore_food_access.duckdb
    test       run the pytest data-quality checks (stops the pipeline on failure)
"""

import logging
import sys

import pytest

from src import config, download, inventory, load


def _download() -> None:
    failed = download.run()
    if failed:
        raise SystemExit(f"Download failed for: {', '.join(failed)}")


def _test() -> None:
    code = pytest.main(["-q", str(config.PROJECT_ROOT / "tests")])
    if code != 0:
        raise SystemExit("Data-quality tests failed - fix before using the outputs.")


STEPS = {
    "download": _download,
    "inventory": inventory.main,
    "load": load.main,
    "test": _test,
}


def main(selected: list[str]) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    unknown = set(selected) - set(STEPS)
    if unknown:
        raise SystemExit(f"Unknown step(s): {', '.join(unknown)}. Choose from: {', '.join(STEPS)}")
    for name, step in STEPS.items():
        if selected and name not in selected:
            continue
        logging.info("=== %s ===", name)
        step()


if __name__ == "__main__":
    main(sys.argv[1:])
