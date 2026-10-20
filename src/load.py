"""Build the DuckDB database by running the numbered SQL files in sql/.

    python -m src.load

The SQL files do all the cleaning and joining (staging -> clean -> analysis).
This module only:
  1. runs them in order, filling in the data/raw path,
  2. prints the data-quality log so every removed/changed row is visible,
  3. exports the processed tables to data/processed/.
"""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb

from src import config

log = logging.getLogger("load")

# Phase 2 build steps. Analysis queries (Phase 3+) live in later-numbered files
# and are run by src/analyze.py, not here.
BUILD_FILES = ["01_staging.sql", "02_clean.sql", "03_food_access.sql", "04_analysis_table.sql"]


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the project database with the spatial extension loaded."""
    con = duckdb.connect(str(config.DB_PATH), read_only=read_only)
    con.execute("INSTALL spatial; LOAD spatial;")
    return con


def run_sql_file(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    """Execute one SQL file. {raw} becomes the absolute data/raw path."""
    sql = path.read_text(encoding="utf-8").replace("{raw}", config.RAW_DIR.as_posix())
    log.info("running %s", path.name)
    con.execute(sql)


def export_processed(con: duckdb.DuckDBPyConnection) -> None:
    """Write the analysis tables and the data-quality log to data/processed/."""
    out = config.PROCESSED_DIR
    out.mkdir(parents=True, exist_ok=True)
    exports = {
        "tract_analysis": "SELECT * FROM analysis.tract_analysis",
        "stores": "SELECT * FROM analysis.stores",
        "data_quality_log": "SELECT * FROM clean.dq_log",
    }
    for name, query in exports.items():
        dest = out / f"{name}.csv"
        con.execute(f"COPY ({query}) TO '{dest.as_posix()}' (HEADER, DELIMITER ',')")
        log.info("exported %s", dest.relative_to(config.PROJECT_ROOT))

    # Tract boundaries as GeoJSON (lon/lat) for maps.
    dest = out / "tracts.geojson"
    dest.unlink(missing_ok=True)
    con.execute(f"""
        COPY (SELECT geoid, tract_name, geom FROM clean.tracts ORDER BY geoid)
        TO '{dest.as_posix()}' WITH (FORMAT GDAL, DRIVER 'GeoJSON')
    """)
    log.info("exported %s", dest.relative_to(config.PROJECT_ROOT))


def print_dq_log(con: duckdb.DuckDBPyConnection) -> None:
    """Log every data-quality entry so nothing is removed silently."""
    rows = con.execute(
        "SELECT step, action, rows_in, rows_affected, rows_out, reason FROM clean.dq_log"
    ).fetchall()
    log.info("Data-quality log (%d entries):", len(rows))
    for step, action, rows_in, affected, rows_out, reason in rows:
        log.info("  %-16s %-9s in=%-6s affected=%-6s out=%-6s %s",
                 step, action, rows_in, affected, rows_out, reason)


def main() -> None:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = connect()
    try:
        for name in BUILD_FILES:
            run_sql_file(con, config.SQL_DIR / name)
        print_dq_log(con)
        export_processed(con)
        n = con.execute("SELECT count(*) FROM analysis.tract_analysis").fetchone()[0]
        log.info("analysis.tract_analysis built with %d tracts -> %s", n,
                 config.DB_PATH.relative_to(config.PROJECT_ROOT))
    finally:
        con.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    main()
