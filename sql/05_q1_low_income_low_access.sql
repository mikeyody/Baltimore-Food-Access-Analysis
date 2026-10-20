-- =============================================================================
-- 05_q1_low_income_low_access.sql
-- Q1: How many tracts and residents are both low-income and low-access?
--
-- There is no single "right" definition, so we report several side by side:
--   * USDA LRAM 2019 (official, supermarket-based, crosswalked to 2020 tracts)
--   * An updated 2026 version: USDA's current low-income flag (2020-24 ACS) +
--     our own distance from the population-weighted centroid to the nearest
--     full-service grocery store (2026 SNAP retailer list)
--   * USDA SRAM 2025 (counts any SNAP store, incl. convenience stores) for contrast
-- Residents = ACS 2020-2024 total population of the flagged tracts.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS results;

CREATE OR REPLACE TABLE results.q1_low_income_low_access AS
WITH defs AS (
    SELECT 1 AS sort, 'USDA 2019 (LRAM): low-income & low-access, 1/2 mile' AS definition,
           'USDA official; 2019 supermarket list; crosswalked 2010->2020' AS basis,
           geoid, lila_half_mi_2019 AS flagged
    FROM analysis.tract_analysis
    UNION ALL
    SELECT 2, 'USDA 2019 (LRAM): low-income & low-access, 1 mile',
           'USDA official; 2019 supermarket list; crosswalked 2010->2020',
           geoid, lila_1mi_2019
    FROM analysis.tract_analysis
    UNION ALL
    SELECT 3, 'Updated 2026: low-income & nearest full-service store > 1/2 mile',
           'USDA 2025 low-income flag + 2026 SNAP supermarkets, distance from population-weighted centroid',
           geoid, low_income_2025 AND beyond_half_mi_full_service
    FROM analysis.tract_analysis
    UNION ALL
    SELECT 4, 'Updated 2026: low-income & nearest full-service store > 1 mile',
           'USDA 2025 low-income flag + 2026 SNAP supermarkets, distance from population-weighted centroid',
           geoid, low_income_2025 AND beyond_1_mi_full_service
    FROM analysis.tract_analysis
    UNION ALL
    SELECT 5, 'USDA 2025 (SRAM): low-income & low-access, 1/2 mile (any SNAP store)',
           'USDA official; counts convenience/dollar stores as access',
           geoid, sram_lila_half_mi
    FROM analysis.tract_analysis
)
SELECT
    d.sort,
    d.definition,
    d.basis,
    count(*) FILTER (WHERE d.flagged)                                        AS tracts_flagged,
    count(*)                                                                 AS tracts_total,
    round(100.0 * count(*) FILTER (WHERE d.flagged) / count(*), 1)           AS pct_of_tracts,
    sum(t.total_population) FILTER (WHERE d.flagged)::BIGINT                 AS residents,
    round(100.0 * sum(t.total_population) FILTER (WHERE d.flagged)
                / sum(t.total_population), 1)                                AS pct_of_residents,
    sum(t.households_no_vehicle) FILTER (WHERE d.flagged)::BIGINT            AS households_no_vehicle,
    sum(t.population_65_plus) FILTER (WHERE d.flagged)::BIGINT               AS residents_65_plus
FROM defs d
JOIN analysis.tract_analysis t USING (geoid)
GROUP BY d.sort, d.definition, d.basis
ORDER BY d.sort;

-- Citywide totals, for context in the report.
CREATE OR REPLACE TABLE results.q1_city_totals AS
SELECT
    count(*)                                             AS tracts,
    sum(total_population)::BIGINT                        AS residents,
    sum(households)::BIGINT                              AS households,
    sum(households_no_vehicle)::BIGINT                   AS households_no_vehicle,
    count(*) FILTER (WHERE low_income_2025)              AS low_income_tracts_2025,
    (SELECT count(*) FROM analysis.stores
      WHERE in_baltimore_city AND is_full_service)       AS full_service_stores_in_city,
    (SELECT count(*) FROM analysis.stores
      WHERE in_baltimore_city)                           AS snap_stores_in_city
FROM analysis.tract_analysis;
