"""Data-quality tests for the cleaned database.

Run with:  pytest -v

Each test checks one property that, if broken, would make the analysis wrong:
tract counts, unique IDs, valid value ranges, joins that don't lose tracts,
the 2010->2020 crosswalk, and the nearest-store distances.
"""

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from src import config

TRACT_TABLES = [
    "clean.tracts",
    "clean.tract_centroids",
    "clean.acs",
    "clean.places",
    "clean.lram_2020",
    "clean.sram_2025",
    "clean.tract_food_access",
    "analysis.tract_analysis",
]


# ---------------------------------------------------------------------------
# Tract counts and IDs
# ---------------------------------------------------------------------------
def test_tract_count_matches_expected(scalar):
    assert scalar("SELECT count(*) FROM clean.tracts") == config.EXPECTED_TRACT_COUNT


def test_analysis_table_has_one_row_per_tract(scalar):
    assert scalar("SELECT count(*) FROM analysis.tract_analysis") == config.EXPECTED_TRACT_COUNT


@pytest.mark.parametrize("table", TRACT_TABLES)
def test_no_duplicate_geoids(scalar, table):
    dupes = scalar(f"SELECT count(*) - count(DISTINCT geoid) FROM {table}")
    assert dupes == 0, f"{table} has {dupes} duplicate GEOIDs"


@pytest.mark.parametrize("table", TRACT_TABLES)
def test_geoids_are_11_digit_baltimore_strings(scalar, table):
    bad = scalar(f"""
        SELECT count(*) FROM {table}
        WHERE NOT regexp_full_match(geoid, '24510[0-9]{{6}}')
    """)
    assert bad == 0, f"{table} has {bad} malformed GEOIDs"


def test_geoid_column_is_text(con):
    dtype = con.execute("""
        SELECT data_type FROM information_schema.columns
        WHERE table_schema = 'analysis' AND table_name = 'tract_analysis' AND column_name = 'geoid'
    """).fetchone()[0]
    assert dtype == "VARCHAR"


# ---------------------------------------------------------------------------
# Joins don't lose tracts
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("table", ["clean.acs", "clean.lram_2020", "clean.sram_2025",
                                   "clean.tract_food_access", "clean.tract_centroids"])
def test_every_tract_has_a_match(scalar, table):
    missing = scalar(f"SELECT count(*) FROM clean.tracts t ANTI JOIN {table} x USING (geoid)")
    assert missing == 0, f"{missing} tracts have no row in {table}"


@pytest.mark.parametrize("table", ["clean.acs", "clean.places", "clean.lram_2020", "clean.sram_2025"])
def test_no_rows_outside_master_tract_list(scalar, table):
    orphans = scalar(f"SELECT count(*) FROM {table} x ANTI JOIN clean.tracts t USING (geoid)")
    assert orphans == 0, f"{table} has {orphans} tracts not in the TIGER tract list"


def test_tracts_missing_health_data_are_nearly_empty(con):
    """PLACES skips very-low-population tracts. Any tract without health data
    should be one of those, not a data-loading bug."""
    rows = con.execute("""
        SELECT geoid, total_population FROM analysis.tract_analysis WHERE NOT has_health_data
    """).fetchall()
    for geoid, pop in rows:
        assert pop is not None and pop < 100, f"{geoid} (pop {pop}) is missing PLACES data"


# ---------------------------------------------------------------------------
# Valid value ranges
# ---------------------------------------------------------------------------
PERCENT_COLUMNS = ["pct_poverty", "pct_households_no_vehicle", "pct_65_plus",
                   "diabetes_pct", "obesity_pct", "high_bp_pct", "chd_pct"]


@pytest.mark.parametrize("column", PERCENT_COLUMNS)
def test_percentages_between_0_and_100(scalar, column):
    bad = scalar(f"SELECT count(*) FROM analysis.tract_analysis WHERE {column} NOT BETWEEN 0 AND 100")
    assert bad == 0


@pytest.mark.parametrize("column", ["total_population", "households", "households_no_vehicle",
                                    "population_65_plus", "below_poverty"])
def test_counts_non_negative(scalar, column):
    assert scalar(f"SELECT count(*) FROM analysis.tract_analysis WHERE {column} < 0") == 0


def test_subgroups_not_larger_than_totals(scalar):
    bad = scalar("""
        SELECT count(*) FROM analysis.tract_analysis
        WHERE households_no_vehicle > households OR population_65_plus > total_population
    """)
    assert bad == 0


def test_income_positive_and_plausible(scalar):
    bad = scalar("""
        SELECT count(*) FROM analysis.tract_analysis
        WHERE median_household_income NOT BETWEEN 1000 AND 500000
    """)
    assert bad == 0


def test_census_sentinel_codes_removed(scalar):
    """-666666666 etc. must never reach the clean tables."""
    assert scalar("SELECT count(*) FROM clean.acs WHERE least(total_population, "
                  "median_household_income, households, below_poverty) < 0") == 0


def test_health_estimates_inside_confidence_interval(scalar):
    bad = scalar("""
        SELECT count(*) FROM clean.places WHERE
            diabetes_pct NOT BETWEEN diabetes_ci_low AND diabetes_ci_high
         OR obesity_pct  NOT BETWEEN obesity_ci_low  AND obesity_ci_high
         OR high_bp_pct  NOT BETWEEN high_bp_ci_low  AND high_bp_ci_high
         OR chd_pct      NOT BETWEEN chd_ci_low      AND chd_ci_high
    """)
    assert bad == 0


def test_distances_present_and_plausible(scalar):
    """Baltimore is ~10 miles across; no tract centroid can be >5 miles from
    every full-service store in Maryland."""
    assert scalar("SELECT count(*) FROM analysis.tract_analysis "
                  "WHERE dist_nearest_full_service_mi IS NULL") == 0
    assert scalar("SELECT count(*) FROM analysis.tract_analysis "
                  "WHERE dist_nearest_full_service_mi NOT BETWEEN 0 AND 5") == 0


def test_any_store_never_farther_than_full_service_store(scalar):
    """Full-service stores are a subset of all SNAP stores."""
    assert scalar("SELECT count(*) FROM analysis.tract_analysis "
                  "WHERE dist_nearest_any_snap_mi > dist_nearest_full_service_mi") == 0


# ---------------------------------------------------------------------------
# Crosswalk
# ---------------------------------------------------------------------------
def test_crosswalk_weights_cover_each_2020_tract(con):
    """The land shares making up each 2020 tract must add to 1."""
    lo, hi = con.execute(
        "SELECT min(crosswalk_coverage), max(crosswalk_coverage) FROM clean.lram_2020").fetchone()
    assert lo == pytest.approx(1, abs=1e-6) and hi == pytest.approx(1, abs=1e-6)


def test_crosswalk_preserves_low_access_population(scalar):
    """Splitting 2010 counts across 2020 tracts shouldn't create or lose people
    (for 2010 tracts entirely inside the city)."""
    before = scalar("""
        SELECT sum(low_access_pop_half_mi) FROM clean.lram_2010
        WHERE geoid_2010 LIKE '24510%'
    """)
    after = scalar("SELECT sum(low_access_pop_half_mi_2019) FROM clean.lram_2020")
    outside = scalar("SELECT count(*) FROM clean.lram_2010 WHERE geoid_2010 NOT LIKE '24510%'")
    # Every 2010 piece of a Baltimore 2020 tract is itself a Baltimore 2010 tract,
    # so the split should be exact. If border tracts ever appear, allow 1%.
    assert after == pytest.approx(before, rel=1e-9 if outside == 0 else 0.01)


# ---------------------------------------------------------------------------
# Spatial checks, recomputed independently with GeoPandas
# ---------------------------------------------------------------------------
def test_nearest_store_distance_matches_geopandas(con):
    """Recompute nearest full-service distance with GeoPandas/pyproj (a separate
    projection library from DuckDB's) and compare to the SQL result."""
    tracts = con.execute("""
        SELECT geoid, centroid_lon, centroid_lat, dist_nearest_full_service_mi
        FROM analysis.tract_analysis
    """).df()
    stores = con.execute("SELECT lon, lat FROM clean.snap_stores WHERE is_full_service").df()

    to_ft = lambda df, x, y: gpd.GeoDataFrame(  # noqa: E731
        df, geometry=[Point(a, b) for a, b in zip(df[x], df[y])], crs="EPSG:4326"
    ).to_crs("EPSG:2248")
    t = to_ft(tracts, "centroid_lon", "centroid_lat")
    s = to_ft(stores, "lon", "lat")

    nearest = gpd.sjoin_nearest(t, s, distance_col="dist_ft")
    nearest = nearest.groupby("geoid")["dist_ft"].min() / 5280
    merged = tracts.set_index("geoid").join(nearest)
    diff = (merged["dist_nearest_full_service_mi"] - merged["dist_ft"]).abs()
    # SQL rounds to 0.001 mi; allow a small tolerance for datum differences.
    assert diff.max() < 0.01, f"max difference {diff.max():.4f} mi"


def test_store_tract_assignment_matches_geopandas(con):
    stores = con.execute(
        "SELECT store_id, lon, lat, tract_geoid FROM clean.snap_stores").df()
    tracts = gpd.read_file(config.PROCESSED_DIR / "tracts.geojson")
    pts = gpd.GeoDataFrame(stores, geometry=gpd.points_from_xy(stores.lon, stores.lat),
                           crs="EPSG:4326").to_crs(tracts.crs)
    joined = gpd.sjoin(pts, tracts[["geoid", "geometry"]], how="left", predicate="within")
    joined = joined.drop_duplicates("store_id").set_index("store_id")
    agree = (joined["geoid"].fillna("") == joined["tract_geoid"].fillna("")).mean()
    assert agree > 0.999, f"only {agree:.2%} of store-tract assignments agree"


# ---------------------------------------------------------------------------
# Data-quality log
# ---------------------------------------------------------------------------
def test_dq_log_accounts_for_every_filter(con):
    """rows_in - rows_affected must equal rows_out for every 'filtered' step."""
    log = con.execute("SELECT * FROM clean.dq_log WHERE action = 'filtered'").df()
    assert not log.empty
    pd.testing.assert_series_equal(log.rows_in - log.rows_affected, log.rows_out,
                                   check_names=False)


def test_no_lram_tracts_missing(scalar):
    assert scalar("""SELECT rows_affected FROM clean.dq_log
                     WHERE step = 'lram_2010' AND action = 'flagged'""") == 0
