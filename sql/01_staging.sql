-- =============================================================================
-- 01_staging.sql
-- Load every raw file into a staging table exactly as published.
--
-- Rules for staging:
--   * Every column is loaded as text (all_varchar) so nothing is silently
--     coerced or lost on load. Types are assigned in 02_clean.sql.
--   * No filtering here: staging = the full raw file.
--   * {raw} is replaced with the data/raw path by src/load.py.
-- =============================================================================

INSTALL spatial;
LOAD spatial;

CREATE SCHEMA IF NOT EXISTS staging;

-- 1. USDA LRAM 2019 (2010 tracts). ERS files are Windows-1252 encoded; latin-1
--    decodes every character that appears in this file (e.g. "Doña Ana").
CREATE OR REPLACE TABLE staging.usda_lram_2019 AS
SELECT * FROM read_csv('{raw}/usda/lram_2019/Food Access Research Atlas.csv',
                       all_varchar = true, encoding = 'latin-1', header = true);

-- 2. USDA SRAM 2025 (2020 tracts): general characteristics + straight-line distance tables.
CREATE OR REPLACE TABLE staging.usda_sram_general AS
SELECT * FROM read_csv('{raw}/usda/sram_2025/SRAM General Tract Characteristics Data.csv',
                       all_varchar = true, encoding = 'latin-1', header = true);

CREATE OR REPLACE TABLE staging.usda_sram_straight_line AS
SELECT * FROM read_csv('{raw}/usda/sram_2025/SRAM Straight Line Distance Data.csv',
                       all_varchar = true, encoding = 'latin-1', header = true);

-- 3. CDC PLACES 2025 (long format: one row per tract x measure).
CREATE OR REPLACE TABLE staging.cdc_places AS
SELECT * FROM read_csv('{raw}/places/places_2025_baltimore.csv', all_varchar = true, header = true);

-- 4. Census ACS 2020-2024 5-year estimates.
CREATE OR REPLACE TABLE staging.census_acs AS
SELECT * FROM read_csv('{raw}/acs/acs5_2024_baltimore_tracts.csv', all_varchar = true, header = true);

-- 5. SNAP-authorized retailers (all of Maryland).
CREATE OR REPLACE TABLE staging.snap_retailers AS
SELECT * FROM read_csv('{raw}/snap/snap_retailers_md.csv', all_varchar = true, header = true);

-- 6. TIGER/Line 2024 tract polygons for Maryland (read straight from the zip via GDAL).
CREATE OR REPLACE TABLE staging.tiger_tracts AS
SELECT * FROM ST_Read('/vsizip/{raw}/tiger/tl_2024_24_tract.zip/tl_2024_24_tract.shp');

-- 7. 2020 Census population-weighted tract centroids.
CREATE OR REPLACE TABLE staging.census_pop_centroids AS
SELECT * FROM read_csv('{raw}/tiger/CenPop2020_Mean_TR24.txt', all_varchar = true, header = true);

-- 8. Official 2020 <-> 2010 tract relationship file (pipe-delimited).
CREATE OR REPLACE TABLE staging.tract_relationship AS
SELECT * FROM read_csv('{raw}/crosswalk/tab20_tract20_tract10_st24.txt',
                       all_varchar = true, delim = '|', header = true);

-- 9-10. Optional Open Baltimore layers.
CREATE OR REPLACE TABLE staging.farmers_markets AS
SELECT * FROM read_csv('{raw}/open_baltimore/farmers_markets.csv', all_varchar = true, header = true);

CREATE OR REPLACE TABLE staging.city_grocery_stores AS
SELECT * FROM read_csv('{raw}/open_baltimore/city_grocery_stores.csv', all_varchar = true, header = true);
