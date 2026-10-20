# Power BI Dashboard Guide

All files are in `outputs/powerbi/` and are rebuilt by `python run_pipeline.py`.

| File | Grain | Use |
|------|-------|-----|
| `fact_tract.csv` | 1 row per census tract (199) | Main fact table: demographics, health, access, flags, need score |
| `dim_store.csv` | 1 row per store / market | SNAP retailers in and within 3 miles of the city, plus farmers markets |
| `summary_metrics.csv` | 1 row per headline metric | KPI cards (each metric has a definition and source table) |
| `need_sensitivity.csv` | 1 row per scored tract | Need rank under each weighting scenario |
| `tracts.geojson` | tract polygons | Shape/filled-map visual, keyed on `GEOID` |

## Loading

1. **Get Data -> Text/CSV** for each CSV. In Power Query, set **GEOID** (and `Store ID`,
   `ZIP`) to **Text** before loading, or Power BI will treat them as numbers.
2. **Model view:** relate `fact_tract[GEOID]` 1-to-many to `dim_store[GEOID]` and 1-to-1 to
   `need_sensitivity[GEOID]`. Stores outside the city have a blank GEOID, which is expected.
3. **Map visual:** use the *Shape map* (Preview features) with `tracts.geojson` as a custom
   map (convert to TopoJSON at mapshaper.org if Power BI asks), location = `GEOID`. Or use
   the ArcGIS/Azure map with `Centroid Latitude` / `Centroid Longitude`. Store points use
   `Latitude` / `Longitude` from `dim_store`.
4. Set **Population**, **Households Without Vehicle** to *Sum*, and every percentage and
   distance column to *Don't summarize* (or *Average* where an average of tracts is meant).

Useful DAX measures:

```DAX
Residents = SUM(fact_tract[Population])
Car-free Households = SUM(fact_tract[Households Without Vehicle])
Pop-weighted Diabetes % =
    DIVIDE(SUMX(fact_tract, fact_tract[Diabetes (% adults)] * fact_tract[Population]),
           SUM(fact_tract[Population]))
LILA Residents (2026) =
    CALCULATE([Residents], fact_tract[Low Income & >1/2 mi From Grocery (2026)] = TRUE())
```

## Suggested pages

**1. Overview**
- KPI cards from `summary_metrics.csv`: residents, low-income/low-access residents,
  car-free households > 1/2 mile from a grocery, full-service stores in the city.
- Filled map of tracts colored by `Need Score (0-100)` with full-service stores as points.
- Slicer: `Distance Band`, `Low Income (USDA 2025)`.

**2. Food access**
- Histogram/column chart of `Distance to Full-Service Grocery (mi)` by `Distance Band`.
- Table of tracts sorted by distance: Neighborhood, distance, nearest store, % without a car.
- Map of `dim_store` points colored by `Store Category` (full-service vs. corner stores).

**3. Health gap**
- Clustered column: average Diabetes / Obesity / High BP / CHD, legend =
  `Low Income & >1/2 mi From Grocery (2026)`.
- Scatter: x = `Median Household Income ($)`, y = `High Blood Pressure (% adults)`, size =
  Population, legend = `Distance Band`. Shows that income is the dominant pattern.

**4. Priority neighborhoods**
- Bar chart of the top 10 by `Need Score (0-100)` (filter `Top 10 Need Tract` = True).
- Stacked bar of the five `Contribution:` columns per tract, showing why each tract ranks high.
- Matrix from `need_sensitivity.csv`: GEOID x scenario rank, conditional formatting, to show
  how stable each tract's ranking is.
- Tooltip page with the tract's key stats (mirrors the popup in `outputs/map.html`).

**Design tips:** one blue sequential color scale for the need score; a single accent color
for "low access" vs. a neutral gray for "other"; label units in every title.
