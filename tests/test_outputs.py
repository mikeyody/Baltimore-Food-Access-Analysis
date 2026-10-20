"""Checks on the Phase 5 deliverables: Power BI files, charts, and the map."""

import json

import pandas as pd
import pytest

from src import config

POWERBI = config.OUTPUTS_DIR / "powerbi"
CHARTS = config.OUTPUTS_DIR / "charts"


@pytest.fixture(scope="module")
def fact():
    return pd.read_csv(POWERBI / "fact_tract.csv", dtype={"GEOID": str})


def test_fact_table_one_row_per_tract(fact):
    assert len(fact) == config.EXPECTED_TRACT_COUNT
    assert fact["GEOID"].is_unique
    assert fact["GEOID"].str.fullmatch(r"24510\d{6}").all()


def test_fact_matches_database(fact, scalar):
    """Exported population and scores equal what's in DuckDB."""
    assert fact["Population"].sum() == scalar("SELECT sum(total_population) FROM analysis.tract_analysis")
    assert fact["Need Rank"].notna().sum() == scalar("SELECT count(*) FROM results.need_scores")


def test_store_table_has_coordinates():
    stores = pd.read_csv(POWERBI / "dim_store.csv", dtype={"GEOID": str, "Store ID": str})
    assert stores[["Latitude", "Longitude"]].notna().all().all()
    assert stores["Store ID"].is_unique


def test_summary_metrics_have_sources():
    summary = pd.read_csv(POWERBI / "summary_metrics.csv")
    assert summary["Value"].notna().all()
    assert summary["Source Table"].str.startswith("results.").all()


def test_geojson_keys_match_fact_table(fact):
    gj = json.loads((POWERBI / "tracts.geojson").read_text())
    keys = {f["properties"]["GEOID"] for f in gj["features"]}
    assert keys == set(fact["GEOID"])


def test_charts_exist():
    pngs = sorted(CHARTS.glob("*.png"))
    assert 4 <= len(pngs) <= 6, f"expected 4-6 charts, found {len(pngs)}"
    assert all(p.stat().st_size > 10_000 for p in pngs)


def test_map_exists():
    html = (config.OUTPUTS_DIR / "map.html").read_text(encoding="utf-8")
    assert "leaflet" in html.lower()
    assert "Full-service grocery stores" in html


def test_reports_generated():
    """The findings report exists and the README findings block was filled in."""
    findings = (config.REPORTS_DIR / "findings.md").read_text(encoding="utf-8")
    assert "## Key findings" in findings and "## Recommendations" in findings
    readme = (config.PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    block = readme.split("<!-- KEY-FINDINGS:START -->")[1].split("<!-- KEY-FINDINGS:END -->")[0]
    assert "Run `python run_pipeline.py` to generate" not in block
    assert "**1." in block
