-- =============================================================================
-- 02_clean.sql
-- Turn staging tables into typed, Baltimore-only, documented "clean" tables.
--
-- Conventions:
--   * Tract IDs are 11-character text GEOIDs: state(2) + county(3) + tract(6).
--   * Census/USDA "missing" codes become NULL, never 0, unless the publisher
--     documents that blank means zero (noted where it happens).
--   * Every filter or value change writes a row to clean.dq_log, so nothing is
--     dropped or changed silently.
-- =============================================================================

LOAD spatial;
CREATE SCHEMA IF NOT EXISTS clean;

-- -----------------------------------------------------------------------------
-- Data-quality log: one row per filter / value change.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE TABLE clean.dq_log (
    step          VARCHAR,   -- which clean table produced the entry
    action        VARCHAR,   -- 'filtered' (rows removed), 'set_null', 'set_zero', 'flagged'
    rows_in       BIGINT,    -- rows before the step
    rows_affected BIGINT,    -- rows removed or values changed
    rows_out      BIGINT,    -- rows after the step
    reason        VARCHAR
);

-- Census API sentinel codes (-666666666, -999999999, -888888888, -222222222,
-- -555555555 ...) are all negative. None of our variables can legitimately be
-- negative, so any negative number means "not available".
CREATE OR REPLACE MACRO census_num(x) AS
    CASE WHEN TRY_CAST(x AS DOUBLE) < 0 THEN NULL ELSE TRY_CAST(x AS DOUBLE) END;

-- ERS files write missing values as the literal text 'NULL'.
CREATE OR REPLACE MACRO ers_num(x) AS
    CASE WHEN x IS NULL OR trim(x) IN ('', 'NULL', 'NA') THEN NULL ELSE TRY_CAST(x AS DOUBLE) END;

-- =============================================================================
-- 1. Tracts: the master list of 2020-vintage Baltimore City tracts
-- =============================================================================
CREATE OR REPLACE TABLE clean.tracts AS
SELECT
    GEOID                               AS geoid,
    NAMELSAD                            AS tract_name,
    TRACTCE                             AS tract_code,
    CAST(ALAND AS BIGINT)               AS land_area_m2,
    CAST(AWATER AS BIGINT)              AS water_area_m2,
    geom                                AS geom,          -- NAD83 lon/lat (EPSG:4269)
    -- Maryland State Plane (US feet): a flat projection where distances are accurate.
    ST_Transform(geom, 'EPSG:4269', 'EPSG:2248', always_xy := true) AS geom_ft
FROM staging.tiger_tracts
WHERE STATEFP = '24' AND COUNTYFP = '510';

INSERT INTO clean.dq_log
SELECT 'tracts', 'filtered',
       (SELECT count(*) FROM staging.tiger_tracts), (SELECT count(*) FROM staging.tiger_tracts) - count(*), count(*),
       'Kept only Baltimore City (COUNTYFP 510); other Maryland tracts removed'
FROM clean.tracts;

-- =============================================================================
-- 2. Tract centroids: population-weighted (2020 Census) with a fallback
-- =============================================================================
CREATE OR REPLACE TABLE clean.tract_centroids AS
WITH pop AS (
    SELECT STATEFP || COUNTYFP || TRACTCE AS geoid,
           CAST(POPULATION AS BIGINT)     AS pop_2020,
           CAST(LATITUDE  AS DOUBLE)      AS lat,
           CAST(LONGITUDE AS DOUBLE)      AS lon
    FROM staging.census_pop_centroids
)
SELECT
    t.geoid,
    p.pop_2020,
    -- Use the population-weighted centroid when the Census provides one;
    -- otherwise a point guaranteed to be inside the tract polygon.
    CASE WHEN p.lat IS NOT NULL THEN 'population_weighted' ELSE 'geometric' END AS centroid_type,
    coalesce(p.lon, ST_X(ST_PointOnSurface(t.geom))) AS lon,
    coalesce(p.lat, ST_Y(ST_PointOnSurface(t.geom))) AS lat
FROM clean.tracts t
LEFT JOIN pop p USING (geoid);

ALTER TABLE clean.tract_centroids ADD COLUMN geom_ft GEOMETRY;
UPDATE clean.tract_centroids
SET geom_ft = ST_Transform(ST_Point(lon, lat), 'EPSG:4269', 'EPSG:2248', always_xy := true);

INSERT INTO clean.dq_log
SELECT 'tract_centroids', 'flagged', count(*), count(*) FILTER (WHERE centroid_type = 'geometric'), count(*),
       'Tracts with no population-weighted centroid; geometric point-on-surface used instead'
FROM clean.tract_centroids;

-- =============================================================================
-- 3. ACS 2020-2024: demographics and economics
-- =============================================================================
CREATE OR REPLACE TABLE clean.acs AS
WITH typed AS (
    SELECT
        state || county || tract                  AS geoid,
        CAST(census_num(B01003_001E) AS BIGINT)   AS total_population,
        census_num(B01003_001M)                   AS total_population_moe,
        census_num(B19013_001E)                   AS median_household_income,
        census_num(B19013_001M)                   AS median_household_income_moe,
        CAST(census_num(B17001_001E) AS BIGINT)   AS poverty_universe,
        CAST(census_num(B17001_002E) AS BIGINT)   AS below_poverty,
        CAST(census_num(B08201_001E) AS BIGINT)   AS households,
        CAST(census_num(B08201_002E) AS BIGINT)   AS households_no_vehicle,
        census_num(B08201_002M)                   AS households_no_vehicle_moe,
        CAST(census_num(B01001_020E) + census_num(B01001_021E) + census_num(B01001_022E)
          + census_num(B01001_023E) + census_num(B01001_024E) + census_num(B01001_025E)
          + census_num(B01001_044E) + census_num(B01001_045E) + census_num(B01001_046E)
          + census_num(B01001_047E) + census_num(B01001_048E) + census_num(B01001_049E) AS BIGINT)
                                                  AS population_65_plus,
        B19013_001E                               AS raw_income_code
    FROM staging.census_acs
)
SELECT
    * EXCLUDE (raw_income_code),
    -- Percentages use each measure's own universe; NULLIF avoids divide-by-zero
    -- in tracts with no households (e.g. group-quarters-only tracts).
    round(100.0 * below_poverty         / NULLIF(poverty_universe, 0), 2) AS pct_poverty,
    round(100.0 * households_no_vehicle / NULLIF(households, 0), 2)       AS pct_households_no_vehicle,
    round(100.0 * population_65_plus    / NULLIF(total_population, 0), 2) AS pct_65_plus,
    (median_household_income IS NULL)                                     AS income_suppressed
FROM typed;

INSERT INTO clean.dq_log
SELECT 'acs', 'set_null', count(*), count(*) FILTER (WHERE median_household_income IS NULL), count(*),
       'Median household income suppressed by Census (negative sentinel code) -> NULL'
FROM clean.acs;
INSERT INTO clean.dq_log
SELECT 'acs', 'set_null', count(*), count(*) FILTER (WHERE pct_households_no_vehicle IS NULL OR pct_poverty IS NULL), count(*),
       'Percent no-vehicle or poverty undefined because the tract has 0 households / 0 people in the poverty universe -> NULL'
FROM clean.acs;

-- =============================================================================
-- 4. CDC PLACES: pivot long (tract x measure) to wide (one row per tract)
-- =============================================================================
CREATE OR REPLACE TABLE clean.places AS
SELECT
    locationname AS geoid,
    max(year)    AS brfss_year,
    max(TRY_CAST(totalpopulation AS BIGINT))                                          AS places_population,
    max(TRY_CAST(data_value AS DOUBLE))            FILTER (WHERE measureid = 'DIABETES') AS diabetes_pct,
    max(TRY_CAST(low_confidence_limit AS DOUBLE))  FILTER (WHERE measureid = 'DIABETES') AS diabetes_ci_low,
    max(TRY_CAST(high_confidence_limit AS DOUBLE)) FILTER (WHERE measureid = 'DIABETES') AS diabetes_ci_high,
    max(TRY_CAST(data_value AS DOUBLE))            FILTER (WHERE measureid = 'OBESITY')  AS obesity_pct,
    max(TRY_CAST(low_confidence_limit AS DOUBLE))  FILTER (WHERE measureid = 'OBESITY')  AS obesity_ci_low,
    max(TRY_CAST(high_confidence_limit AS DOUBLE)) FILTER (WHERE measureid = 'OBESITY')  AS obesity_ci_high,
    max(TRY_CAST(data_value AS DOUBLE))            FILTER (WHERE measureid = 'BPHIGH')   AS high_bp_pct,
    max(TRY_CAST(low_confidence_limit AS DOUBLE))  FILTER (WHERE measureid = 'BPHIGH')   AS high_bp_ci_low,
    max(TRY_CAST(high_confidence_limit AS DOUBLE)) FILTER (WHERE measureid = 'BPHIGH')   AS high_bp_ci_high,
    max(TRY_CAST(data_value AS DOUBLE))            FILTER (WHERE measureid = 'CHD')      AS chd_pct,
    max(TRY_CAST(low_confidence_limit AS DOUBLE))  FILTER (WHERE measureid = 'CHD')      AS chd_ci_low,
    max(TRY_CAST(high_confidence_limit AS DOUBLE)) FILTER (WHERE measureid = 'CHD')      AS chd_ci_high
FROM staging.cdc_places
WHERE countyfips = '24510'
  AND data_value_type = 'Crude prevalence'   -- the tract file has crude rates only; guard anyway
GROUP BY locationname;

INSERT INTO clean.dq_log
SELECT 'places', 'flagged', (SELECT count(*) FROM clean.tracts),
       (SELECT count(*) FROM clean.tracts t ANTI JOIN clean.places p USING (geoid)),
       count(*),
       'Baltimore tracts with no PLACES estimate (CDC does not model very-low-population tracts); kept with NULL health values'
FROM clean.places;

-- =============================================================================
-- 5. Tract crosswalk 2010 -> 2020 (Census relationship file)
-- =============================================================================
-- Each row is a piece of land shared by one 2010 tract and one 2020 tract.
--   share_of_2020 = piece land / 2020 tract land -> used to average flags & rates
--   share_of_2010 = piece land / 2010 tract land -> used to split counts
CREATE OR REPLACE TABLE clean.tract_crosswalk AS
SELECT
    GEOID_TRACT_20                         AS geoid_2020,
    GEOID_TRACT_10                         AS geoid_2010,
    CAST(AREALAND_PART AS DOUBLE)          AS land_part_m2,
    CAST(AREALAND_PART AS DOUBLE) / NULLIF(CAST(AREALAND_TRACT_20 AS DOUBLE), 0) AS share_of_2020,
    CAST(AREALAND_PART AS DOUBLE) / NULLIF(CAST(AREALAND_TRACT_10 AS DOUBLE), 0) AS share_of_2010
FROM staging.tract_relationship
WHERE GEOID_TRACT_20 LIKE '24510%'
  AND CAST(AREALAND_PART AS DOUBLE) > 0;   -- water-only touches carry no people

INSERT INTO clean.dq_log
SELECT 'tract_crosswalk', 'filtered',
       (SELECT count(*) FROM staging.tract_relationship WHERE GEOID_TRACT_20 LIKE '24510%'),
       (SELECT count(*) FROM staging.tract_relationship WHERE GEOID_TRACT_20 LIKE '24510%') - count(*),
       count(*),
       'Removed relationship pieces with zero land area (water-only overlaps); non-Baltimore 2020 tracts excluded'
FROM clean.tract_crosswalk;

-- =============================================================================
-- 6. USDA LRAM 2019 on its native 2010 tracts
-- =============================================================================
-- Keep every 2010 tract that overlaps a Baltimore 2020 tract (a few are in
-- Baltimore County, at the city line), not just the 24510 ones.
CREATE OR REPLACE TABLE clean.lram_2010 AS
SELECT
    CensusTract                              AS geoid_2010,
    ers_num(Urban)::INT                      AS urban,
    ers_num(Pop2010)                         AS pop_2010,
    ers_num(LowIncomeTracts)::INT            AS low_income,
    ers_num(LILATracts_1And10)::INT          AS lila_1mi,
    ers_num(LILATracts_halfAnd10)::INT       AS lila_half_mi,
    ers_num(LATracts1)::INT                  AS low_access_1mi,
    ers_num(LATracts_half)::INT              AS low_access_half_mi,
    -- ERS leaves lapop/lalowi/lahunv blank ('NULL') when nobody in the tract is
    -- beyond the distance (only happens when the low-access flag is 0), so
    -- blank means zero here. Logged below.
    coalesce(ers_num(lapop1), 0)             AS low_access_pop_1mi,
    coalesce(ers_num(lapophalf), 0)          AS low_access_pop_half_mi,
    coalesce(ers_num(lalowi1), 0)            AS low_access_lowinc_pop_1mi,
    coalesce(ers_num(lahunv1), 0)            AS low_access_no_vehicle_hh_1mi,
    coalesce(ers_num(lahunvhalf), 0)         AS low_access_no_vehicle_hh_half_mi
FROM staging.usda_lram_2019
WHERE CensusTract IN (SELECT DISTINCT geoid_2010 FROM clean.tract_crosswalk);

INSERT INTO clean.dq_log
SELECT 'lram_2010', 'filtered',
       (SELECT count(*) FROM staging.usda_lram_2019),
       (SELECT count(*) FROM staging.usda_lram_2019) - count(*), count(*),
       'Kept 2010 tracts that overlap a Baltimore City 2020 tract; all others removed'
FROM clean.lram_2010;

INSERT INTO clean.dq_log
SELECT 'lram_2010', 'set_zero', count(*),
       count(*) FILTER (WHERE trim(s.lapop1) = 'NULL'), count(*),
       'lapop1 blank in source (tract not low-access at 1 mile, per ERS) -> 0 people beyond 1 mile'
FROM clean.lram_2010 l JOIN staging.usda_lram_2019 s ON s.CensusTract = l.geoid_2010;

INSERT INTO clean.dq_log
SELECT 'lram_2010', 'flagged', (SELECT count(DISTINCT geoid_2010) FROM clean.tract_crosswalk),
       (SELECT count(DISTINCT geoid_2010) FROM clean.tract_crosswalk) - count(*), count(*),
       '2010 tracts in the crosswalk that are missing from LRAM (should be 0)'
FROM clean.lram_2010;

-- =============================================================================
-- 7. USDA LRAM crosswalked to 2020 tracts
-- =============================================================================
CREATE OR REPLACE TABLE clean.lram_2020 AS
SELECT
    c.geoid_2020                                                 AS geoid,
    count(*)                                                     AS n_source_2010_tracts,
    sum(c.share_of_2020)                                         AS crosswalk_coverage,  -- should be ~1
    -- Share of the 2020 tract's land that was in a flagged 2010 tract
    sum(c.share_of_2020 * l.lila_1mi)           / sum(c.share_of_2020) AS lila_1mi_share,
    sum(c.share_of_2020 * l.lila_half_mi)       / sum(c.share_of_2020) AS lila_half_mi_share,
    sum(c.share_of_2020 * l.low_access_1mi)     / sum(c.share_of_2020) AS low_access_1mi_share,
    sum(c.share_of_2020 * l.low_access_half_mi) / sum(c.share_of_2020) AS low_access_half_mi_share,
    sum(c.share_of_2020 * l.low_income)         / sum(c.share_of_2020) AS low_income_share,
    -- Counts: give each 2020 tract its land share of every 2010 tract's count
    sum(c.share_of_2010 * l.low_access_pop_1mi)            AS low_access_pop_1mi_2019,
    sum(c.share_of_2010 * l.low_access_pop_half_mi)        AS low_access_pop_half_mi_2019,
    sum(c.share_of_2010 * l.low_access_lowinc_pop_1mi)     AS low_access_lowinc_pop_1mi_2019,
    sum(c.share_of_2010 * l.low_access_no_vehicle_hh_1mi)  AS low_access_no_vehicle_hh_1mi_2019
FROM clean.tract_crosswalk c
JOIN clean.lram_2010 l USING (geoid_2010)
GROUP BY c.geoid_2020;

-- Majority rule: a 2020 tract carries the flag if >= 50% of its land was flagged.
ALTER TABLE clean.lram_2020 ADD COLUMN lila_1mi_2019 BOOLEAN;
ALTER TABLE clean.lram_2020 ADD COLUMN lila_half_mi_2019 BOOLEAN;
ALTER TABLE clean.lram_2020 ADD COLUMN low_access_1mi_2019 BOOLEAN;
ALTER TABLE clean.lram_2020 ADD COLUMN low_access_half_mi_2019 BOOLEAN;
ALTER TABLE clean.lram_2020 ADD COLUMN low_income_2019 BOOLEAN;
UPDATE clean.lram_2020 SET
    lila_1mi_2019           = lila_1mi_share >= 0.5,
    lila_half_mi_2019       = lila_half_mi_share >= 0.5,
    low_access_1mi_2019     = low_access_1mi_share >= 0.5,
    low_access_half_mi_2019 = low_access_half_mi_share >= 0.5,
    low_income_2019         = low_income_share >= 0.5;

INSERT INTO clean.dq_log
SELECT 'lram_2020', 'flagged', count(*),
       count(*) FILTER (WHERE lila_half_mi_share NOT IN (0, 1)), count(*),
       '2020 tracts built from 2010 tracts with mixed LILA (1/2 mile) flags; majority-of-land rule applied'
FROM clean.lram_2020;

-- =============================================================================
-- 8. USDA SRAM 2025 (already on 2020 tracts) - comparison measure
-- =============================================================================
CREATE OR REPLACE TABLE clean.sram_2025 AS
SELECT
    g.CensusTract20                                    AS geoid,
    ers_num(g.Urban)::INT                              AS urban,
    ers_num(g.POP2020)                                 AS pop_2020_usda,
    ers_num(g.LowIncomeTracts)::INT = 1                AS low_income_2025,
    ers_num(g.PovertyRate)                             AS usda_poverty_rate,
    ers_num(g.MedianFamilyIncome)                      AS usda_median_family_income,
    ers_num(s.SD_SRAM_LILATracts_1And10)::INT = 1      AS sram_lila_1mi,
    ers_num(s.SD_SRAM_LILATracts_halfAnd10)::INT = 1   AS sram_lila_half_mi,
    ers_num(s.SD_SRAM_LATracts_half)::INT = 1          AS sram_low_access_half_mi,
    coalesce(ers_num(s.SD_SRAM_lapophalf), 0)          AS sram_low_access_pop_half_mi
FROM staging.usda_sram_general g
JOIN staging.usda_sram_straight_line s USING (CensusTract20)
WHERE g.CensusTract20 LIKE '24510%';

INSERT INTO clean.dq_log
SELECT 'sram_2025', 'filtered', (SELECT count(*) FROM staging.usda_sram_general),
       (SELECT count(*) FROM staging.usda_sram_general) - count(*), count(*),
       'Kept only Baltimore City tracts (24510)'
FROM clean.sram_2025;

-- =============================================================================
-- 9. SNAP retailers: typed, categorised, located
-- =============================================================================
CREATE OR REPLACE TABLE clean.snap_stores AS
WITH typed AS (
    SELECT
        CAST(Record_ID AS BIGINT)                  AS store_id,
        trim(Store_Name)                           AS store_name,
        trim(Store_Type)                           AS store_type,
        trim(Store_Street_Address)                 AS address,
        trim(City)                                 AS city,
        trim(Zip_Code)                             AS zip_code,
        upper(trim(County))                        AS county,
        TRY_CAST(Latitude AS DOUBLE)               AS lat,
        TRY_CAST(Longitude AS DOUBLE)              AS lon
    FROM staging.snap_retailers
)
, reviewed AS (
    -- USDA's Store_Type has a few obvious misclassifications. Following the USDA
    -- LRAM definition of a full-service store, we exclude:
    --   * gas stations / convenience chains labelled as supermarkets
    --   * membership warehouse clubs (BJ's, Costco, Sam's Club), which need a paid
    --     membership and are excluded by USDA's own supermarket list
    -- Counted in clean.dq_log below.
    SELECT
        *,
        store_type IN ('Supermarket', 'Super Store')
          AND regexp_matches(store_name,
                '(?i)\b(shell|exxon|sunoco|citgo|mobil|bp|valero|7-eleven|wawa|royal farms|sheetz|'
                || 'bjs|bj''s|costco|sam''s club|sams club)\b') AS excluded_from_full_service
    FROM typed
)
SELECT
    *,
    CASE
        WHEN excluded_from_full_service                   THEN 'Other'
        WHEN store_type IN ('Supermarket', 'Super Store') THEN 'Full-service grocery'
        WHEN store_type = 'Grocery Store'                 THEN 'Small grocery'
        WHEN store_type = 'Convenience Store'             THEN 'Convenience store'
        WHEN store_type = 'Farmers and Markets'           THEN 'Farmers market'
        WHEN store_type = 'Specialty Store'               THEN 'Specialty store'
        ELSE 'Other'
    END                                                    AS store_category,
    store_type IN ('Supermarket', 'Super Store')
      AND NOT excluded_from_full_service                   AS is_full_service,
    ST_Point(lon, lat)                                     AS geom,
    ST_Transform(ST_Point(lon, lat), 'EPSG:4326', 'EPSG:2248', always_xy := true) AS geom_ft
FROM reviewed
-- Maryland's bounding box (with margin). Anything outside is a bad coordinate.
WHERE lat BETWEEN 37.8 AND 39.8 AND lon BETWEEN -79.6 AND -75.0;

INSERT INTO clean.dq_log
SELECT 'snap_stores', 'filtered', (SELECT count(*) FROM staging.snap_retailers),
       (SELECT count(*) FROM staging.snap_retailers) - count(*), count(*),
       'Removed retailers with missing or out-of-Maryland coordinates'
FROM clean.snap_stores;

INSERT INTO clean.dq_log
SELECT 'snap_stores', 'flagged', count(*), count(*) FILTER (WHERE excluded_from_full_service), count(*),
       'USDA Supermarket/Super Store reclassified as Other: gas stations or membership warehouse clubs ('
       || coalesce(string_agg(store_name, '; ') FILTER (WHERE excluded_from_full_service), '') || ')'
FROM clean.snap_stores;

-- Assign each store to the Baltimore tract it sits in (NULL = outside the city).
-- TIGER is NAD83 and SNAP is WGS84; the two differ by about a metre here, which
-- is negligible at tract scale.
ALTER TABLE clean.snap_stores ADD COLUMN tract_geoid VARCHAR;
UPDATE clean.snap_stores s
SET tract_geoid = t.geoid
FROM clean.tracts t
WHERE ST_Contains(t.geom, s.geom);

INSERT INTO clean.dq_log
SELECT 'snap_stores', 'flagged', count(*),
       count(*) FILTER (WHERE county = 'BALTIMORE CITY' AND tract_geoid IS NULL), count(*),
       'Stores labelled Baltimore City whose coordinates fall outside the city boundary (kept; used for distance only)'
FROM clean.snap_stores;

-- =============================================================================
-- 10. Optional: farmers markets and the city's grocery list
-- =============================================================================
CREATE OR REPLACE TABLE clean.farmers_markets AS
SELECT
    trim(name)                         AS market_name,
    trim(address)                      AS address,
    trim(day_time)                     AS day_time,
    trim(dates_open)                   AS dates_open,
    TRY_CAST(geom_y AS DOUBLE)         AS lat,
    TRY_CAST(geom_x AS DOUBLE)         AS lon
FROM staging.farmers_markets
WHERE TRY_CAST(geom_y AS DOUBLE) IS NOT NULL;

INSERT INTO clean.dq_log
SELECT 'farmers_markets', 'filtered', (SELECT count(*) FROM staging.farmers_markets),
       (SELECT count(*) FROM staging.farmers_markets) - count(*), count(*),
       'Removed markets with no coordinates'
FROM clean.farmers_markets;

CREATE OR REPLACE TABLE clean.city_grocery_stores AS
SELECT
    trim(storename)                                         AS store_name,
    trim(address)                                           AS address,
    trim(status)                                            AS status,
    coalesce(TRY_CAST(latitude AS DOUBLE),  TRY_CAST(geom_y AS DOUBLE)) AS lat,
    coalesce(TRY_CAST(longitude AS DOUBLE), TRY_CAST(geom_x AS DOUBLE)) AS lon
FROM staging.city_grocery_stores;
