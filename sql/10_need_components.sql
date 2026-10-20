-- =============================================================================
-- 10_need_components.sql
-- Raw inputs for the composite need score (Phase 4). One row per tract.
-- Standardizing and weighting happen in src/need_score.py so the weights can
-- be changed in one place (src/config.py) and re-run under alternative
-- scenarios.
--
-- Every component is oriented so that HIGHER = MORE NEED.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS results;

CREATE OR REPLACE TABLE results.need_components AS
SELECT
    geoid,
    tract_name,
    neighborhood,
    total_population,
    households_no_vehicle,
    median_household_income,
    nearest_full_service_store,
    -- Food access: straight-line miles to the nearest full-service grocery store
    dist_nearest_full_service_mi                 AS food_access_raw,
    -- Health burden inputs (combined into one component in Python)
    diabetes_pct,
    obesity_pct,
    high_bp_pct,
    chd_pct,
    pct_poverty                                  AS poverty_raw,
    pct_households_no_vehicle                    AS no_car_raw,
    pct_65_plus                                  AS seniors_raw,
    low_income_2025,
    lila_half_mi_2019,
    -- A tract can only be scored if every input exists and it has enough
    -- residents for percentages to be meaningful.
    (has_health_data
        AND pct_poverty IS NOT NULL
        AND pct_households_no_vehicle IS NOT NULL
        AND pct_65_plus IS NOT NULL
        AND dist_nearest_full_service_mi IS NOT NULL
        AND total_population >= 100)             AS eligible,
    concat_ws('; ',
        CASE WHEN NOT has_health_data THEN 'no CDC PLACES health estimates' END,
        CASE WHEN pct_poverty IS NULL THEN 'no poverty universe' END,
        CASE WHEN pct_households_no_vehicle IS NULL THEN 'no households (group quarters)' END,
        CASE WHEN total_population < 100 THEN 'fewer than 100 residents' END
    )                                            AS ineligible_reason
FROM analysis.tract_analysis;
