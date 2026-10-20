-- =============================================================================
-- 09_q4_model_data.sql
-- Q4 input: the modelling dataset for "does food access relate to health once
-- income is accounted for?" The regression itself runs in src/analyze.py
-- (statsmodels), because SQL has no built-in regression with standard errors.
--
-- Complete cases only: tracts need a health estimate and a (non-suppressed)
-- median income. Excluded tracts are counted in the report.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS results;

CREATE OR REPLACE TABLE results.q4_model_data AS
SELECT
    geoid,
    total_population,
    dist_nearest_full_service_mi,
    beyond_half_mi_full_service::INT          AS beyond_half_mi,
    median_household_income,
    ln(median_household_income)               AS log_income,   -- income effects are proportional, so use logs
    median_household_income >= 250000         AS income_top_coded,  -- Census caps at "250,000+"
    pct_65_plus,
    diabetes_pct,
    obesity_pct,
    high_bp_pct,
    chd_pct
FROM analysis.tract_analysis
WHERE has_health_data
  AND median_household_income IS NOT NULL
  AND pct_65_plus IS NOT NULL;
