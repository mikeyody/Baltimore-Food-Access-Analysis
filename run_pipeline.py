"""Rebuild the whole project with one command.

    python run_pipeline.py            # run every step in order
    python run_pipeline.py download   # run only the named step(s)

Steps are added here as each project phase is built.
"""

import logging
import sys

from src import download, inventory

STEPS = {
    "download": lambda: _fail_on(download.run()),
    "inventory": inventory.main,
}


def _fail_on(failed: list[str]) -> None:
    if failed:
        raise SystemExit(f"Download failed for: {', '.join(failed)}")


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
