-- =============================================================================
-- 11_report_metrics.sql
-- Extra figures used in reports/findings.md (Phase 6).
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS results;

-- How has the low-income / low-access picture changed since USDA's 2019 data?
-- Compares USDA 2019 (1/2 mile, supermarkets, crosswalked) with the 2026 update
-- (USDA 2025 low-income flag + 2026 supermarket list, centroid > 1/2 mile).
CREATE OR REPLACE TABLE results.report_lila_change AS
SELECT
    count(*) FILTER (WHERE lila_half_mi_2019 AND low_income_2025 AND beyond_half_mi_full_service)
        AS tracts_flagged_both,
    count(*) FILTER (WHERE NOT lila_half_mi_2019 AND low_income_2025 AND beyond_half_mi_full_service)
        AS tracts_newly_flagged_2026,
    count(*) FILTER (WHERE lila_half_mi_2019 AND NOT (low_income_2025 AND beyond_half_mi_full_service))
        AS tracts_no_longer_flagged,
    sum(total_population) FILTER (WHERE NOT lila_half_mi_2019 AND low_income_2025
                                    AND beyond_half_mi_full_service)::BIGINT
        AS residents_newly_flagged_2026
FROM analysis.tract_analysis;

-- Where car-free households and distance overlap: tracts > 1/2 mile from a
-- full-service grocery where at least 40% of households have no vehicle.
CREATE OR REPLACE TABLE results.report_car_free_priority AS
SELECT
    40                                            AS no_car_threshold_pct,
    count(*)                                      AS tracts,
    sum(households_no_vehicle)::BIGINT            AS households_no_vehicle,
    sum(total_population)::BIGINT                 AS residents
FROM analysis.tract_analysis
WHERE beyond_half_mi_full_service
  AND pct_households_no_vehicle >= 40;

-- Do low-access tracts still have *some* SNAP store (e.g. a corner store) close by?
CREATE OR REPLACE TABLE results.report_corner_store_access AS
SELECT
    count(*)                                                       AS lila_tracts_2026,
    count(*) FILTER (WHERE dist_nearest_any_snap_mi <= 0.5)        AS with_any_snap_store_within_half_mi,
    round(100.0 * count(*) FILTER (WHERE dist_nearest_any_snap_mi <= 0.5) / count(*), 0)
                                                                   AS pct_with_any_snap_store_within_half_mi
FROM analysis.tract_analysis
WHERE low_income_2025 AND beyond_half_mi_full_service;

-- Size of the database, for the README / resume.
CREATE OR REPLACE TABLE results.report_pipeline_stats AS
SELECT
    (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'staging') AS staging_tables,
    (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'clean')   AS clean_tables,
    (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'results') AS results_tables,
    (SELECT count(*) FROM clean.snap_stores)                                         AS stores_geolocated,
    (SELECT count(*) FROM clean.dq_log)                                              AS dq_log_entries;
