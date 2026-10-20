# Methodology: Cleaning and Database (Phase 2)

All counts referred to below are recorded by the pipeline in `clean.dq_log`
(exported to `data/processed/data_quality_log.csv`); none are typed by hand.

## Database layers (DuckDB + spatial extension)

| Layer | Schema | Built by | Purpose |
|-------|--------|----------|---------|
| Staging | `staging.*` | `sql/01_staging.sql` | Raw files loaded as-is, every column as text, no filtering |
| Clean | `clean.*` | `sql/02_clean.sql`, `sql/03_food_access.sql` | Typed, Baltimore-only, missing values handled, one row per tract or store |
| Analysis | `analysis.*` | `sql/04_analysis_table.sql` | `tract_analysis` (one row per 2020 tract) and `stores` |

The database file is `data/baltimore_food_access.duckdb`. `python run_pipeline.py`
rebuilds it from scratch.

## Tract IDs

Every table uses an 11-character text GEOID (`24` Maryland + `510` Baltimore City +
6-digit tract). IDs are never stored as numbers, which would drop leading zeros.
The master tract list is TIGER/Line 2024 (2020 tract vintage). Every other table
is LEFT JOINed onto it, so no tract can drop out of the analysis.

## Crosswalk: USDA LRAM 2019 (2010 tracts) to 2020 tracts

The supermarket-based USDA data (LRAM 2019) uses 2010 tract boundaries; everything
else uses 2020 boundaries. We use the Census Bureau's official
2020-to-2010 tract relationship file, which lists every piece of land shared by a
2010 tract and a 2020 tract, with the land area of that piece.

For each piece we compute two weights:

- `share_of_2020` = piece land area / 2020 tract land area
- `share_of_2010` = piece land area / 2010 tract land area

Then, for each 2020 tract:

- **Flags** (low-income, low-access, LILA) become a land-weighted share:
  `sum(share_of_2020 * flag)`. The 2020 tract is flagged when that share is >= 0.5
  (most of its land was in a flagged 2010 tract). The share itself is also kept.
- **Counts** (low-access population, households without a vehicle beyond 1 mile)
  are split by `share_of_2010`: each 2020 tract receives the fraction of a 2010
  tract's count that matches the fraction of that 2010 tract's land it contains.

Pieces with zero land area (harbor water) are dropped and logged. The tests check
that the shares for each 2020 tract add to exactly 1 and that the citywide
low-access population is the same before and after the crosswalk.

**Limitation:** land-area weighting assumes people are spread evenly within each
piece. Population-weighted (block-level) crosswalks would be more precise, but
most Baltimore tracts were unchanged or simply split between 2010 and 2020,
where the two methods give the same answer.

## Missing and suppressed values

| Source | Situation | Handling |
|--------|-----------|----------|
| ACS | Negative sentinel codes (e.g. `-666666666` = estimate not available) | -> NULL, `income_suppressed` flag set |
| ACS | Tract with 0 households or 0 people in the poverty universe | Percentages -> NULL (no divide-by-zero) |
| PLACES | Very-low-population tracts have no estimates | Kept with NULL health values, `has_health_data = false` |
| USDA LRAM | `lapop1` etc. blank when nobody is beyond that distance (only when the low-access flag is 0) | -> 0, logged |
| USDA ERS files | Windows-1252 encoding | Read as latin-1 |

## Store classification

Full-service grocery stores = USDA SNAP `Store_Type` of **Supermarket** or
**Super Store**, minus gas stations and membership warehouse clubs (Costco, Sam's
Club, BJ's). Warehouse clubs are excluded because USDA's own supermarket-based
access measure excludes them and they require a paid membership. Every
reclassified store is named in the data-quality log.

Stores are not geocoded by us: USDA publishes latitude/longitude for every
retailer. Each store is assigned to the tract polygon that contains it (a spatial
join), which the tests re-check with GeoPandas. The USDA `County` field is
sometimes wrong (some stores labeled "Baltimore City" are in Anne Arundel or
Baltimore County), so the spatial join, not the label, decides whether a store is
in the city.

## Distance to the nearest full-service grocery store

- **Origin:** the tract's 2020 Census population-weighted centroid (where the
  average resident lives). If one were missing, a point inside the tract polygon
  would be used instead (`centroid_type` records which).
- **Destination:** the nearest full-service store anywhere in Maryland, so a
  supermarket just over the city line counts.
- **Measure:** straight-line distance in Maryland State Plane (EPSG:2248, US
  feet), converted to miles. The tests recompute it independently with GeoPandas.

**Limitation:** straight-line distance understates walking distance (street
networks, highways, the harbor), and one centroid per tract hides variation
within a tract.
