"""Phase 5a: export Power BI-ready files to outputs/powerbi/.

    python -m src.export

Files
  fact_tract.csv         one row per census tract (the main fact table)
  dim_store.csv          store locations (SNAP retailers near the city + farmers markets)
  summary_metrics.csv    headline numbers, each with its definition and source
  need_sensitivity.csv   tract rank under each weighting scenario
  tracts.geojson         tract boundaries (GEOID key) for map visuals

Column names are plain English with units so they read well in Power BI
without renaming. GEOID is quoted text in the CSV; set its type to Text in
Power Query so leading digits are never treated as a number.
"""

from __future__ import annotations

import json
import logging

import pandas as pd

from src import config
from src.load import connect

log = logging.getLogger("export")
OUT = config.OUTPUTS_DIR / "powerbi"

# analysis/results column -> Power BI column name
FACT_COLUMNS = {
    "geoid": "GEOID",
    "tract_name": "Tract Name",
    "neighborhood": "Neighborhood",
    "land_area_sq_mi": "Land Area (sq mi)",
    "total_population": "Population",
    "households": "Households",
    "median_household_income": "Median Household Income ($)",
    "pct_poverty": "Poverty Rate (%)",
    "households_no_vehicle": "Households Without Vehicle",
    "pct_households_no_vehicle": "Households Without Vehicle (%)",
    "population_65_plus": "Population 65+",
    "pct_65_plus": "Population 65+ (%)",
    "diabetes_pct": "Diabetes (% adults)",
    "obesity_pct": "Obesity (% adults)",
    "high_bp_pct": "High Blood Pressure (% adults)",
    "chd_pct": "Coronary Heart Disease (% adults)",
    "dist_nearest_full_service_mi": "Distance to Full-Service Grocery (mi)",
    "nearest_full_service_store": "Nearest Full-Service Grocery",
    "full_service_within_1_mi": "Full-Service Groceries Within 1 mi",
    "dist_nearest_any_snap_mi": "Distance to Any SNAP Store (mi)",
    "snap_stores_in_tract": "SNAP Stores in Tract",
    "convenience_in_tract": "Convenience Stores in Tract",
    "distance_band": "Distance Band",
    "low_income_2025": "Low Income (USDA 2025)",
    "lila_half_mi_2019": "Low Income & Low Access 1/2 mi (USDA 2019)",
    "lila_1mi_2019": "Low Income & Low Access 1 mi (USDA 2019)",
    "li_la_half_mi_2026": "Low Income & >1/2 mi From Grocery (2026)",
    "need_score": "Need Score (z)",
    "need_score_0_100": "Need Score (0-100)",
    "need_rank": "Need Rank",
    "top_driver": "Top Need Driver",
    "food_access_contribution": "Contribution: Food Access",
    "health_contribution": "Contribution: Health",
    "poverty_contribution": "Contribution: Poverty",
    "no_car_contribution": "Contribution: No Car",
    "seniors_contribution": "Contribution: Seniors",
    "is_top10_need": "Top 10 Need Tract",
    "has_health_data": "Has Health Data",
    "centroid_lat": "Centroid Latitude",
    "centroid_lon": "Centroid Longitude",
}


def build_fact(con) -> pd.DataFrame:
    df = con.execute("""
        SELECT t.*,
               d.distance_band,
               t.low_income_2025 AND t.beyond_half_mi_full_service AS li_la_half_mi_2026,
               n.need_score, n.need_score_0_100, n.need_rank, n.top_driver,
               n.food_access_contribution, n.health_contribution, n.poverty_contribution,
               n.no_car_contribution, n.seniors_contribution,
               coalesce(n.need_rank <= 10, false) AS is_top10_need
        FROM analysis.tract_analysis t
        LEFT JOIN results.q2_tract_distances d USING (geoid)
        LEFT JOIN results.need_scores n USING (geoid)
        ORDER BY t.geoid
    """).df()
    for col in ["need_score", "need_score_0_100"] + [c for c in df if c.endswith("_contribution")]:
        df[col] = df[col].round(3)
    df["need_rank"] = df["need_rank"].astype("Int64")
    return df[list(FACT_COLUMNS)].rename(columns=FACT_COLUMNS)


def build_stores(con) -> pd.DataFrame:
    return con.execute("""
        SELECT CAST(store_id AS VARCHAR)       AS "Store ID",
               store_name                      AS "Store Name",
               store_type                      AS "USDA Store Type",
               store_category                  AS "Store Category",
               is_full_service                 AS "Full-Service Grocery",
               address                         AS "Address",
               city                            AS "City",
               zip_code                        AS "ZIP",
               tract_geoid                     AS "GEOID",
               in_baltimore_city               AS "In Baltimore City",
               lat                             AS "Latitude",
               lon                             AS "Longitude",
               'USDA SNAP Retailer Locator'    AS "Source"
        FROM analysis.stores
        UNION ALL
        SELECT 'FM-' || row_number() OVER (ORDER BY market_name), market_name, 'Farmers market',
               'Farmers market', false, address, 'Baltimore', NULL, NULL, true, lat, lon,
               'Open Baltimore (2023)'
        FROM clean.farmers_markets
    """).df()


def build_summary(con) -> pd.DataFrame:
    """Headline metrics, each read from a results table with its definition."""
    rows = []

    def add(metric, value, unit, definition, source):
        # Whole numbers stay whole (199, not 199.0)
        value = int(value) if float(value).is_integer() else float(value)
        rows.append({"Metric": metric, "Value": value, "Unit": unit,
                     "Definition": definition, "Source Table": source})

    city = con.execute("SELECT * FROM results.q1_city_totals").df().iloc[0].to_dict()
    add("Census tracts", city["tracts"], "tracts", "Baltimore City 2020-vintage tracts", "results.q1_city_totals")
    add("Residents", city["residents"], "people", "ACS 2020-2024 total population", "results.q1_city_totals")
    add("Households without a vehicle", city["households_no_vehicle"], "households",
        "ACS 2020-2024", "results.q1_city_totals")
    add("Full-service grocery stores in city", city["full_service_stores_in_city"], "stores",
        "SNAP Supermarket/Super Store, excl. warehouse clubs & gas stations", "results.q1_city_totals")

    for r in con.execute("SELECT * FROM results.q1_low_income_low_access ORDER BY sort").df().itertuples():
        add(f"Tracts: {r.definition}", r.tracts_flagged, "tracts", r.basis, "results.q1_low_income_low_access")
        add(f"Residents: {r.definition}", r.residents, "people", r.basis, "results.q1_low_income_low_access")

    s = con.execute("SELECT * FROM results.q2_summary").df().iloc[0].to_dict()
    add("Median tract distance to full-service grocery", s["median_distance_mi"], "miles",
        "Straight line from population-weighted centroid", "results.q2_summary")
    add("Car-free households in tracts > 1 mi from grocery", s["no_vehicle_hh_over_1_mi_current"],
        "households", "2026 stores, tract-centroid method", "results.q2_summary")
    add("Car-free households in tracts > 1/2 mi from grocery", s["no_vehicle_hh_over_half_mi_current"],
        "households", "2026 stores, tract-centroid method", "results.q2_summary")
    add("Car-free households > 1 mi from supermarket (USDA 2019)", s["no_vehicle_hh_over_1_mi_usda_2019"],
        "households", "USDA LRAM 2019 grid estimate, crosswalked", "results.q2_summary")

    tests = con.execute("""SELECT * FROM results.q3_tests
        WHERE comparison = 'Low-income & nearest full-service store > 1/2 mi (2026)'""").df()
    for r in tests.itertuples():
        add(f"{r.outcome} gap, low-income low-access vs other tracts", r.difference_pts,
            "percentage points", f"Mean {r.low_access_mean} vs {r.other_mean}; Welch p={r.p_value:.2g}",
            "results.q3_tests")

    reg = con.execute("""SELECT * FROM results.q4_regression
        WHERE model = 'M2 distance + income' AND term = 'dist_nearest_full_service_mi'""").df()
    for r in reg.itertuples():
        add(f"{r.outcome}: change per extra mile, income-adjusted", round(r.coef, 3),
            "percentage points per mile", f"OLS, HC3; 95% CI {r.ci_low:.2f} to {r.ci_high:.2f}; p={r.p_value:.2g}",
            "results.q4_regression")

    top = con.execute("SELECT sum(total_population), sum(households_no_vehicle) FROM results.need_scores "
                      "WHERE need_rank <= 10").fetchone()
    add("Residents in top-10 need tracts", int(top[0]), "people", "Default need-score weights", "results.need_scores")
    add("Car-free households in top-10 need tracts", int(top[1]), "households", "Default need-score weights",
        "results.need_scores")
    return pd.DataFrame(rows, dtype=object)


def build_geojson(con) -> dict:
    """Tract polygons with key attributes in the properties (for Power BI/Folium maps)."""
    con.execute("LOAD spatial")
    rows = con.execute("""
        SELECT t.geoid, a.neighborhood, n.need_score_0_100, n.need_rank,
               ST_AsGeoJSON(ST_SimplifyPreserveTopology(t.geom, 0.00002)) AS geometry
        FROM clean.tracts t
        JOIN analysis.tract_analysis a USING (geoid)
        LEFT JOIN results.need_scores n USING (geoid)
        ORDER BY t.geoid
    """).fetchall()
    features = [{
        "type": "Feature",
        "properties": {"GEOID": g, "Neighborhood": nb,
                       "Need Score (0-100)": None if s is None else round(s, 1),
                       "Need Rank": rank},
        "geometry": json.loads(geom),
    } for g, nb, s, rank, geom in rows]
    return {"type": "FeatureCollection", "features": features}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    con = connect(read_only=True)
    try:
        outputs = {
            "fact_tract.csv": build_fact(con),
            "dim_store.csv": build_stores(con),
            "summary_metrics.csv": build_summary(con),
            "need_sensitivity.csv": con.execute("SELECT * FROM results.need_sensitivity_ranks").df()
                                       .rename(columns={"geoid": "GEOID"}),
        }
        for name, df in outputs.items():
            # Quote text columns so GEOID/ZIP keep their leading characters.
            df.to_csv(OUT / name, index=False, quoting=2)  # csv.QUOTE_NONNUMERIC
            log.info("exported outputs/powerbi/%s (%d rows)", name, len(df))
        (OUT / "tracts.geojson").write_text(json.dumps(build_geojson(con)))
        log.info("exported outputs/powerbi/tracts.geojson")
    finally:
        con.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    main()
