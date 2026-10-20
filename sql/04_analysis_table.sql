-- =============================================================================
-- 04_analysis_table.sql
-- One row per 2020 Baltimore City census tract, with every measure the
-- analysis needs. Every join is a LEFT JOIN from clean.tracts so no tract can
-- be lost; missing values stay NULL (and are counted by the tests).
-- =============================================================================

LOAD spatial;
CREATE SCHEMA IF NOT EXISTS analysis;

CREATE OR REPLACE TABLE analysis.tract_analysis AS
SELECT
    -- Identity
    t.geoid,
    t.tract_name,
    round(t.land_area_m2 / 2589988.11, 4)              AS land_area_sq_mi,

    -- People (ACS 2020-2024)
    a.total_population,
    a.households,
    a.median_household_income,
    a.income_suppressed,
    a.below_poverty,
    a.pct_poverty,
    a.households_no_vehicle,
    a.pct_households_no_vehicle,
    a.population_65_plus,
    a.pct_65_plus,

    -- Health (CDC PLACES 2025 release, BRFSS 2023, crude % of adults)
    p.diabetes_pct,
    p.obesity_pct,
    p.high_bp_pct,
    p.chd_pct,
    (p.geoid IS NOT NULL)                               AS has_health_data,

    -- Current access computed here from the SNAP retailer list
    f.centroid_type,
    f.dist_nearest_full_service_mi,
    f.nearest_full_service_store,
    f.full_service_within_half_mi,
    f.full_service_within_1_mi,
    f.dist_nearest_any_snap_mi,
    f.snap_stores_in_tract,
    f.full_service_in_tract,
    f.small_grocery_in_tract,
    f.convenience_in_tract,
    f.dist_nearest_full_service_mi > 0.5                AS beyond_half_mi_full_service,
    f.dist_nearest_full_service_mi > 1.0                AS beyond_1_mi_full_service,

    -- USDA LRAM 2019 (supermarket-based), crosswalked from 2010 tracts
    l.lila_1mi_2019,
    l.lila_half_mi_2019,
    l.low_access_1mi_2019,
    l.low_access_half_mi_2019,
    l.low_income_2019,
    round(l.lila_half_mi_share, 3)                      AS lila_half_mi_share_2019,
    round(l.low_access_pop_half_mi_2019, 1)             AS low_access_pop_half_mi_2019,
    round(l.low_access_pop_1mi_2019, 1)                 AS low_access_pop_1mi_2019,
    round(l.low_access_no_vehicle_hh_1mi_2019, 1)       AS low_access_no_vehicle_hh_1mi_2019,

    -- USDA SRAM 2025 (any SNAP store), native 2020 tracts
    s.low_income_2025,
    s.sram_lila_1mi,
    s.sram_lila_half_mi,
    s.sram_low_access_half_mi,
    s.sram_low_access_pop_half_mi,

    -- Map geometry (WKT, lon/lat) and centroid for exports
    c.lon                                               AS centroid_lon,
    c.lat                                               AS centroid_lat
FROM clean.tracts t
LEFT JOIN clean.acs               a USING (geoid)
LEFT JOIN clean.places            p USING (geoid)
LEFT JOIN clean.tract_food_access f USING (geoid)
LEFT JOIN clean.lram_2020         l USING (geoid)
LEFT JOIN clean.sram_2025         s USING (geoid)
LEFT JOIN clean.tract_centroids   c USING (geoid)
ORDER BY t.geoid;

-- Full-service stores within reach of the city, for maps and Power BI.
CREATE OR REPLACE TABLE analysis.stores AS
SELECT
    store_id, store_name, store_type, store_category, is_full_service,
    address, city, zip_code, county, lat, lon,
    tract_geoid,
    tract_geoid IS NOT NULL AS in_baltimore_city
FROM clean.snap_stores
-- Keep stores inside the city plus anything within 3 miles of it (border stores).
WHERE tract_geoid IS NOT NULL
   OR ST_DWithin(geom_ft, (SELECT ST_Union_Agg(geom_ft) FROM clean.tracts), 3 * 5280);
