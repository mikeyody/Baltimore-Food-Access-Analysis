-- =============================================================================
-- 06_q2_grocery_distance.sql
-- Q2: How far is each tract from the nearest full-service grocery store, and
--     how many households without a car live more than 1 mile from one?
--
-- Two estimates of car-free households far from a store:
--   * Current (2026 stores, ACS 2020-24): all car-free households in tracts whose
--     population-weighted centroid is > 1 mile from a full-service store.
--     Tract-level approximation: assumes everyone lives at the centroid.
--   * USDA 2019: USDA's own count of car-free households > 1 mile away, computed
--     on a 1/2-km population grid (more precise geography, older store list).
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS results;

-- Per-tract distance table (one row per tract)
CREATE OR REPLACE TABLE results.q2_tract_distances AS
SELECT
    geoid,
    tract_name,
    dist_nearest_full_service_mi,
    nearest_full_service_store,
    full_service_within_1_mi,
    dist_nearest_any_snap_mi,
    CASE
        WHEN dist_nearest_full_service_mi <= 0.5 THEN '0-0.5 mi'
        WHEN dist_nearest_full_service_mi <= 1.0 THEN '0.5-1 mi'
        ELSE 'over 1 mi'
    END                                     AS distance_band,
    total_population,
    households,
    households_no_vehicle,
    pct_households_no_vehicle,
    low_income_2025
FROM analysis.tract_analysis
ORDER BY dist_nearest_full_service_mi DESC;

-- Residents and car-free households by distance band
CREATE OR REPLACE TABLE results.q2_distance_bands AS
SELECT
    distance_band,
    count(*)                                       AS tracts,
    sum(total_population)::BIGINT                  AS residents,
    sum(households)::BIGINT                        AS households,
    sum(households_no_vehicle)::BIGINT             AS households_no_vehicle,
    count(*) FILTER (WHERE low_income_2025)        AS low_income_tracts
FROM results.q2_tract_distances
GROUP BY distance_band
ORDER BY distance_band;

-- Headline numbers
CREATE OR REPLACE TABLE results.q2_summary AS
SELECT
    round(median(dist_nearest_full_service_mi), 2)                       AS median_distance_mi,
    round(avg(dist_nearest_full_service_mi), 2)                          AS mean_distance_mi,
    round(max(dist_nearest_full_service_mi), 2)                          AS max_distance_mi,
    -- Population-weighted: the distance the average resident's tract centroid sits from a store
    round(sum(dist_nearest_full_service_mi * total_population) / sum(total_population), 2)
                                                                         AS pop_weighted_mean_distance_mi,
    count(*) FILTER (WHERE dist_nearest_full_service_mi > 1)             AS tracts_over_1_mi,
    sum(households_no_vehicle) FILTER (WHERE dist_nearest_full_service_mi > 1)::BIGINT
                                                                         AS no_vehicle_hh_over_1_mi_current,
    sum(households_no_vehicle) FILTER (WHERE dist_nearest_full_service_mi > 0.5)::BIGINT
                                                                         AS no_vehicle_hh_over_half_mi_current,
    (SELECT round(sum(low_access_no_vehicle_hh_1mi_2019))::BIGINT FROM analysis.tract_analysis)
                                                                         AS no_vehicle_hh_over_1_mi_usda_2019,
    sum(households_no_vehicle)::BIGINT                                   AS no_vehicle_hh_citywide
FROM results.q2_tract_distances;
