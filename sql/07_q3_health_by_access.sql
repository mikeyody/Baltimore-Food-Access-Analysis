-- =============================================================================
-- 07_q3_health_by_access.sql
-- Q3: How do diabetes, obesity, high blood pressure (and coronary heart
--     disease) rates compare between low-access tracts and other tracts?
--
-- Reported two ways:
--   * simple mean across tracts (each tract counts once)
--   * population-weighted mean (each resident counts once)
-- Only tracts with CDC PLACES estimates are included. Statistical tests are
-- added by src/analyze.py.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS results;

CREATE OR REPLACE TABLE results.q3_health_by_access AS
WITH groups AS (
    SELECT 1 AS sort, 'USDA 2019 low-income & low-access (1/2 mi)' AS comparison,
           geoid, lila_half_mi_2019 AS in_group
    FROM analysis.tract_analysis
    UNION ALL
    SELECT 2, 'Nearest full-service store > 1/2 mi (2026)', geoid, beyond_half_mi_full_service
    FROM analysis.tract_analysis
    UNION ALL
    SELECT 3, 'Low-income & nearest full-service store > 1/2 mi (2026)', geoid,
           low_income_2025 AND beyond_half_mi_full_service
    FROM analysis.tract_analysis
)
SELECT
    g.sort,
    g.comparison,
    CASE WHEN g.in_group THEN 'Low access' ELSE 'Other tracts' END        AS tract_group,
    count(*)                                                             AS tracts,
    sum(t.total_population)::BIGINT                                      AS residents,
    round(avg(t.diabetes_pct), 2)                                        AS diabetes_mean,
    round(avg(t.obesity_pct), 2)                                         AS obesity_mean,
    round(avg(t.high_bp_pct), 2)                                         AS high_bp_mean,
    round(avg(t.chd_pct), 2)                                             AS chd_mean,
    round(sum(t.diabetes_pct * t.total_population) / sum(t.total_population), 2) AS diabetes_pop_weighted,
    round(sum(t.obesity_pct  * t.total_population) / sum(t.total_population), 2) AS obesity_pop_weighted,
    round(sum(t.high_bp_pct  * t.total_population) / sum(t.total_population), 2) AS high_bp_pop_weighted,
    round(sum(t.chd_pct      * t.total_population) / sum(t.total_population), 2) AS chd_pop_weighted,
    round(median(t.median_household_income))                             AS median_of_tract_incomes
FROM groups g
JOIN analysis.tract_analysis t USING (geoid)
WHERE t.has_health_data
GROUP BY g.sort, g.comparison, g.in_group
ORDER BY g.sort, tract_group;
