-- =============================================================================
-- 08_q5_top_need_tracts.sql
-- Q5: Which 10 tracts have the greatest combined need?
--
-- A transparent first pass using equal-weight percentile ranks (0 = lowest in
-- the city, 1 = highest) on four dimensions:
--   access  = distance to nearest full-service grocery store
--   health  = average of diabetes, obesity and high-blood-pressure percentiles
--   poverty = % of people below the poverty line
--   no car  = % of households without a vehicle
-- Phase 4 replaces this with a configurable, documented need score and tests
-- how sensitive the top 10 is to the weights.
--
-- Tracts missing any input (no health data, no households, or < 100 residents)
-- can't be ranked fairly and are listed in results.q5_excluded_tracts.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS results;

CREATE OR REPLACE TABLE results.q5_excluded_tracts AS
SELECT geoid, tract_name, total_population,
       concat_ws('; ',
           CASE WHEN NOT has_health_data THEN 'no CDC PLACES health estimates' END,
           CASE WHEN pct_households_no_vehicle IS NULL THEN 'no households (group quarters)' END,
           CASE WHEN pct_poverty IS NULL THEN 'no poverty universe' END,
           CASE WHEN total_population < 100 THEN 'fewer than 100 residents' END) AS reason
FROM analysis.tract_analysis
WHERE NOT has_health_data
   OR pct_households_no_vehicle IS NULL
   OR pct_poverty IS NULL
   OR total_population < 100;

CREATE OR REPLACE TABLE results.q5_top_need_tracts AS
WITH eligible AS (
    SELECT * FROM analysis.tract_analysis
    WHERE geoid NOT IN (SELECT geoid FROM results.q5_excluded_tracts)
),
ranked AS (
    SELECT
        geoid, tract_name, total_population, dist_nearest_full_service_mi, nearest_full_service_store,
        pct_poverty, pct_households_no_vehicle, diabetes_pct, obesity_pct, high_bp_pct,
        median_household_income,
        percent_rank() OVER (ORDER BY dist_nearest_full_service_mi)  AS access_pctile,
        percent_rank() OVER (ORDER BY pct_poverty)                   AS poverty_pctile,
        percent_rank() OVER (ORDER BY pct_households_no_vehicle)     AS no_car_pctile,
        (percent_rank() OVER (ORDER BY diabetes_pct)
         + percent_rank() OVER (ORDER BY obesity_pct)
         + percent_rank() OVER (ORDER BY high_bp_pct)) / 3           AS health_pctile
    FROM eligible
)
SELECT
    rank() OVER (ORDER BY (access_pctile + health_pctile + poverty_pctile + no_car_pctile) DESC) AS need_rank,
    geoid,
    tract_name,
    total_population,
    round(dist_nearest_full_service_mi, 2)      AS dist_nearest_full_service_mi,
    nearest_full_service_store,
    pct_poverty,
    pct_households_no_vehicle,
    diabetes_pct,
    obesity_pct,
    high_bp_pct,
    median_household_income,
    round(access_pctile, 2)                     AS access_pctile,
    round(health_pctile, 2)                     AS health_pctile,
    round(poverty_pctile, 2)                    AS poverty_pctile,
    round(no_car_pctile, 2)                     AS no_car_pctile,
    round((access_pctile + health_pctile + poverty_pctile + no_car_pctile) / 4, 3) AS combined_need
FROM ranked
ORDER BY need_rank
LIMIT 10;
