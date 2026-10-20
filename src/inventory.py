"""Generate reports/data_sources.md from the files actually on disk.

Descriptive facts (publisher, release year, tract vintage, limitations) are
written below by hand because they come from the publishers' documentation.
Every *number* (row counts, Baltimore rows, column counts, download dates)
is measured from the downloaded files, so the inventory can't drift from
the data.

    python -m src.inventory
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import pandas as pd

from src import config

MANIFEST_PATH = config.RAW_DIR / "manifest.json"
OUT_PATH = config.REPORTS_DIR / "data_sources.md"


@dataclass
class Source:
    """Documentation for one dataset plus a function that measures it."""
    key: str                      # manifest key
    name: str
    publisher: str
    url: str
    release: str
    tract_vintage: str
    key_columns: list[str]
    limitations: list[str]
    loader: callable = field(repr=False)   # returns (total_rows, baltimore_rows, n_columns)


# ---------------------------------------------------------------------------
# Loaders: each returns (rows in file, Baltimore City rows, number of columns)
# ---------------------------------------------------------------------------
def _usda_csv(path: Path, tract_col: str) -> tuple[int, int, int]:
    # ERS files are Windows-1252 encoded (e.g. "Doña Ana County"), not UTF-8.
    df = pd.read_csv(path, encoding="cp1252", dtype={tract_col: str}, low_memory=False)
    balt = df[tract_col].str.zfill(11).str.startswith(config.COUNTY_GEOID)
    return len(df), int(balt.sum()), df.shape[1]


def load_sram() -> tuple[int, int, int]:
    d = config.RAW_DIR / "usda" / "sram_2025"
    rows, balt, _ = _usda_csv(d / "SRAM Straight Line Distance Data.csv", "CensusTract20")
    # Report total columns across the three SRAM tables (they share the tract key).
    ncols = sum(
        len(pd.read_csv(d / f, encoding="cp1252", nrows=0).columns)
        for f in ["SRAM General Tract Characteristics Data.csv",
                  "SRAM Straight Line Distance Data.csv",
                  "SRAM Driving Distance Data.csv"]
    )
    return rows, balt, ncols


def load_lram() -> tuple[int, int, int]:
    return _usda_csv(config.RAW_DIR / "usda" / "lram_2019" / "Food Access Research Atlas.csv",
                     "CensusTract")


def load_places() -> tuple[int, int, int]:
    df = pd.read_csv(config.RAW_DIR / "places" / f"places_{config.PLACES_RELEASE}_baltimore.csv",
                     dtype=str)
    # Already filtered to Baltimore at download time; long format = tract x measure.
    return len(df), len(df), df.shape[1]


def load_acs() -> tuple[int, int, int]:
    df = pd.read_csv(config.RAW_DIR / "acs" / f"acs5_{config.ACS_YEAR}_baltimore_tracts.csv",
                     dtype=str)
    return len(df), len(df), df.shape[1]


def load_snap() -> tuple[int, int, int]:
    df = pd.read_csv(config.RAW_DIR / "snap" / "snap_retailers_md.csv", dtype=str)
    balt = df["County"].str.upper().str.strip().eq("BALTIMORE CITY")
    return len(df), int(balt.sum()), df.shape[1]


def load_tiger() -> tuple[int, int, int]:
    path = config.RAW_DIR / "tiger" / f"tl_{config.TIGER_YEAR}_{config.STATE_FIPS}_tract.zip"
    gdf = gpd.read_file(path)
    balt = gdf["COUNTYFP"].eq(config.COUNTY_FIPS)
    return len(gdf), int(balt.sum()), gdf.shape[1]


def load_cenpop() -> tuple[int, int, int]:
    df = pd.read_csv(config.RAW_DIR / "tiger" / Path(config.CENPOP_TRACT_URL).name,
                     dtype=str, encoding="utf-8-sig")
    balt = df["COUNTYFP"].eq(config.COUNTY_FIPS)
    return len(df), int(balt.sum()), df.shape[1]


def load_relationship() -> tuple[int, int, int]:
    df = pd.read_csv(config.RAW_DIR / "crosswalk" / Path(config.TRACT_RELATIONSHIP_URL).name,
                     sep="|", dtype=str, encoding="utf-8-sig")
    balt = df["GEOID_TRACT_20"].str.startswith(config.COUNTY_GEOID)
    return len(df), int(balt.sum()), df.shape[1]


def _open_baltimore(name: str) -> tuple[int, int, int]:
    df = pd.read_csv(config.RAW_DIR / "open_baltimore" / f"{name}.csv", dtype=str)
    return len(df), len(df), df.shape[1]


def load_neighborhoods() -> tuple[int, int, int]:
    gdf = gpd.read_file(config.RAW_DIR / "open_baltimore" / "neighborhoods.geojson")
    return len(gdf), len(gdf), gdf.shape[1]


SOURCES = [
    Source(
        key="usda_lram_2019",
        name="USDA Food Access Research Atlas - Large Retailer Access Map (LRAM, formerly FARA), 2019",
        publisher="USDA Economic Research Service",
        url=config.USDA_LRAM_2019_URL,
        release="2019 data (store list as of June 2019; 2014-18 ACS); published April 2021",
        tract_vintage="**2010** census tracts -> crosswalked to 2020 tracts in Phase 2",
        key_columns=["CensusTract", "Urban", "Pop2010", "LowIncomeTracts", "LILATracts_1And10",
                     "LILATracts_halfAnd10", "LATracts_half", "LATracts1", "lapop1", "lalowi1",
                     "lahunv1", "TractHUNV"],
        limitations=[
            "Store list is from 2019; stores have opened and closed since.",
            "Uses 2010 tract boundaries, so it must be crosswalked to 2020 tracts (see below).",
            "Only counts supermarkets, supercenters and large grocery stores - the classic "
            "'food desert' definition. This is why it's our primary low-income/low-access flag.",
            "Straight-line distances only (no street network).",
        ],
        loader=load_lram,
    ),
    Source(
        key="usda_sram_2025",
        name="USDA Food Access Research Atlas - SNAP-authorized Retailer Access Map (SRAM), 2025",
        publisher="USDA Economic Research Service",
        url=config.USDA_SRAM_2025_URL,
        release="2025 data (STARS store list June 2025; 2020 Census; 2020-24 ACS); published July 2026",
        tract_vintage="**2020** census tracts (no crosswalk needed)",
        key_columns=["CensusTract20", "Urban", "POP2020", "LowIncomeTracts", "PovertyRate",
                     "SD_SRAM_LILATracts_1And10", "SD_SRAM_LILATracts_halfAnd10",
                     "SD_SRAM_LATracts_half", "SD_SRAM_lapophalf", "SD_SRAM_lahunvhalf",
                     "DD_* (driving-distance equivalents)"],
        limitations=[
            "Counts **every** SNAP-authorized store (convenience, dollar, specialty stores "
            "included), so 'access' here does not mean access to a full-service grocery store. "
            "In dense Baltimore almost every tract is near some SNAP store, so SRAM flags very "
            "few tracts. Used as a secondary/comparison measure only.",
            "Three separate tables (general, straight-line, driving) joined on CensusTract20.",
            "Brand new product (July 2026); ERS documentation may still be evolving.",
        ],
        loader=load_sram,
    ),
    Source(
        key="cdc_places",
        name=f"CDC PLACES: Local Data for Better Health, Census Tract Data, {config.PLACES_RELEASE} release",
        publisher="Centers for Disease Control and Prevention",
        url=f"https://data.cdc.gov/d/{config.PLACES_DATASET_ID} (API: {config.PLACES_URL})",
        release="2025 release (BRFSS 2023 for these measures; data updated Dec 2025). Newest available.",
        tract_vintage="**2020** census tracts",
        key_columns=["locationname (tract GEOID)", "measureid", "data_value",
                     "low_confidence_limit", "high_confidence_limit", "totalpopulation", "year"],
        limitations=[
            "Model-based small-area estimates, not direct measurements. Tract values are partly "
            "predicted from demographics (age, sex, race, poverty), which builds in some "
            "correlation with income - relevant for the Phase 3 regression.",
            "CDC cautions against using PLACES to evaluate local programs or policies.",
            "Long format (one row per tract x measure); pivoted to wide in Phase 2.",
            "Tracts with no/very low population have no estimates.",
        ],
        loader=load_places,
    ),
    Source(
        key="census_acs",
        name=f"U.S. Census Bureau American Community Survey 5-year estimates, {config.ACS_YEAR - 4}-{config.ACS_YEAR}",
        publisher="U.S. Census Bureau (Census Data API)",
        url=config.ACS_URL,
        release=f"{config.ACS_YEAR - 4}-{config.ACS_YEAR} 5-year (released Jan 29, 2026). Newest available.",
        tract_vintage="**2020** census tracts",
        key_columns=list(config.ACS_VARIABLES)[:10] + ["B01001_020E..049E (65+ age bins)"],
        limitations=[
            "Survey estimates with margins of error; tract-level MOEs can be large.",
            "Values pooled over 5 years (2020-2024), not a single-year snapshot.",
            "The Census API returns sentinel values like -666666666 for suppressed/unavailable "
            "estimates; these are converted to NULL in Phase 2.",
            "Requires a free API key.",
        ],
        loader=load_acs,
    ),
    Source(
        key="usda_snap_retailers",
        name="USDA SNAP Retailer Locator (current authorized retailers)",
        publisher="USDA Food and Nutrition Administration (renamed from FNS on June 1, 2026)",
        url=config.SNAP_FEATURE_SERVICE,
        release="Live data; snapshot taken on the download date below (service updated Sept 2026)",
        tract_vintage="Point locations (lat/lon) - spatially joined to 2020 tracts in Phase 2",
        key_columns=["Record_ID", "Store_Name", "Store_Type", "Store_Street_Address", "City",
                     "County", "Zip_Code", "Latitude", "Longitude"],
        limitations=[
            "Only SNAP-authorized stores. Nearly all supermarkets accept SNAP, but a few "
            "full-service grocers may be missing.",
            "Store_Type is self-reported/assigned by USDA; 'Supermarket' and 'Super Store' are "
            "treated as full-service grocery stores.",
            "All of Maryland is downloaded so stores just outside the city line count toward "
            "nearest-store distance.",
        ],
        loader=load_snap,
    ),
    Source(
        key="tiger_tracts",
        name=f"Census TIGER/Line Shapefiles, census tracts, {config.TIGER_YEAR} (Maryland)",
        publisher="U.S. Census Bureau",
        url=config.TIGER_TRACTS_URL,
        release=f"TIGER/Line {config.TIGER_YEAR} (2025 also exists; 2024 chosen to match ACS 2020-2024)",
        tract_vintage="**2020** census tracts (Maryland tracts did not change between 2020 and 2024)",
        key_columns=["GEOID", "STATEFP", "COUNTYFP", "TRACTCE", "NAMELSAD", "ALAND", "AWATER",
                     "INTPTLAT", "INTPTLON", "geometry"],
        limitations=[
            "TIGER/Line polygons include water area (harbor); maps may look better clipped to land.",
        ],
        loader=load_tiger,
    ),
    Source(
        key="census_pop_centroids",
        name="Census 2020 Centers of Population by Census Tract (Maryland)",
        publisher="U.S. Census Bureau",
        url=config.CENPOP_TRACT_URL,
        release="2020 Census",
        tract_vintage="**2020** census tracts",
        key_columns=["STATEFP", "COUNTYFP", "TRACTCE", "POPULATION", "LATITUDE", "LONGITUDE"],
        limitations=[
            "Population-weighted centroid from 2020 block populations; doesn't reflect "
            "population shifts since 2020.",
            "One point per tract - distance from a single point understates variation within "
            "large tracts.",
        ],
        loader=load_cenpop,
    ),
    Source(
        key="tract_relationship_2020_2010",
        name="Census 2020 Tract to 2010 Tract Relationship File (Maryland)",
        publisher="U.S. Census Bureau",
        url=config.TRACT_RELATIONSHIP_URL,
        release="2020 Census relationship files",
        tract_vintage="Links 2010 and 2020 tracts",
        key_columns=["GEOID_TRACT_20", "GEOID_TRACT_10", "AREALAND_TRACT_20", "AREALAND_TRACT_10",
                     "AREALAND_PART"],
        limitations=[
            "Provides land-area overlap, not population overlap. Area weights assume people are "
            "spread evenly within each piece; documented as a limitation of the crosswalk.",
        ],
        loader=load_relationship,
    ),
    Source(
        key="open_baltimore_farmers_markets",
        name="Open Baltimore - Farmers Market (optional)",
        publisher="City of Baltimore (Open Baltimore ArcGIS Hub)",
        url=config.OPEN_BALTIMORE_LAYERS["farmers_markets"],
        release="Layer last edited June 2023",
        tract_vintage="Point locations",
        key_columns=["name", "address", "x_coord", "y_coord", "day_time", "dates_open"],
        limitations=["Last updated 2023; seasonal markets; may be incomplete. Map context only."],
        loader=lambda: _open_baltimore("farmers_markets"),
    ),
    Source(
        key="open_baltimore_city_grocery_stores",
        name="Open Baltimore - Grocery Stores (optional)",
        publisher="City of Baltimore (Open Baltimore ArcGIS Hub)",
        url=config.OPEN_BALTIMORE_LAYERS["city_grocery_stores"],
        release="Layer last edited June 2023",
        tract_vintage="Point locations",
        key_columns=["storename", "address", "latitude", "longitude", "status"],
        limitations=["City says it 'may not be a complete list'. Used only to cross-check the "
                     "SNAP-based full-service store list."],
        loader=lambda: _open_baltimore("city_grocery_stores"),
    ),
    Source(
        key="baltimore_neighborhoods",
        name="Baltimore City Neighborhood Statistical Area (NSA) boundaries",
        publisher="City of Baltimore (CityView GIS)",
        url=config.NEIGHBORHOODS_LAYER,
        release="Live layer; service updated 2026",
        tract_vintage="Neighborhood polygons (not tracts)",
        key_columns=["Name", "geometry"],
        limitations=["Neighborhoods don't nest inside tracts; used only to label each tract with "
                     "the neighborhoods covering >= 20% of its area."],
        loader=load_neighborhoods,
    ),
]


def build_markdown() -> str:
    manifest = json.loads(MANIFEST_PATH.read_text()) if MANIFEST_PATH.exists() else {}
    lines = [
        "# Data Sources Inventory",
        "",
        "_Generated by `python -m src.inventory`. Row counts and dates are measured from the "
        "downloaded files; descriptive fields come from each publisher's documentation._",
        "",
        "| # | Dataset | Release | Tract vintage | Rows in file | Baltimore City rows | Columns | Downloaded |",
        "|---|---------|---------|---------------|-------------:|--------------------:|--------:|------------|",
    ]
    details = []
    for i, src in enumerate(SOURCES, 1):
        entry = manifest.get(src.key)
        try:
            total, balt, ncols = src.loader()
            counts = (f"{total:,}", f"{balt:,}", f"{ncols:,}")
            downloaded = entry["downloaded_utc"][:10] if entry else "?"
        except FileNotFoundError:
            counts = ("not downloaded", "-", "-")
            downloaded = "**missing**"
        lines.append(f"| {i} | {src.name} | {src.release} | {src.tract_vintage} | "
                     f"{counts[0]} | {counts[1]} | {counts[2]} | {downloaded} |")
        details += [
            f"## {i}. {src.name}",
            "",
            f"- **Publisher:** {src.publisher}",
            f"- **URL:** {src.url}",
            f"- **Release:** {src.release}",
            f"- **Tract vintage:** {src.tract_vintage}",
            f"- **Rows:** {counts[0]} total, {counts[1]} in Baltimore City; {counts[2]} columns",
            f"- **Key columns:** {', '.join(f'`{c}`' for c in src.key_columns)}",
            "- **Known limitations:**",
            *[f"  - {lim}" for lim in src.limitations],
            "",
        ]
    lines += ["", "Rows for PLACES and ACS were already filtered to Baltimore City by the API "
              "query. PLACES is long format (tract x measure).", ""]
    lines += [CROSSWALK_NOTE, "", *details]
    return "\n".join(lines)


CROSSWALK_NOTE = """## Tract vintage summary and crosswalk plan

| Source | Vintage | Action |
|--------|---------|--------|
| USDA LRAM 2019 | 2010 | Crosswalk to 2020 using the Census relationship file |
| USDA SRAM 2025 | 2020 | None |
| CDC PLACES 2025 | 2020 | None |
| ACS 2020-2024 | 2020 | None |
| TIGER/Line 2024 | 2020 | None (this is the master tract list) |
| SNAP retailers, Open Baltimore | points | Spatial join to 2020 tracts |

**Crosswalk method (implemented in Phase 2):** the relationship file lists every piece
where a 2010 tract overlaps a 2020 tract, with the land area of that piece
(`AREALAND_PART`). For each 2020 tract we weight each overlapping 2010 tract by
`AREALAND_PART / AREALAND_TRACT_20` (share of the 2020 tract's land that came from that
2010 tract). Counts (e.g. low-access population) are allocated by the share of the
*2010* tract's land; flags become a weighted share, and a 2020 tract is flagged when
at least 50% of its land comes from flagged 2010 tracts. Area weighting assumes
people are spread evenly within each tract piece; this is documented as a limitation."""


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(build_markdown(), encoding="utf-8")
    print(f"Wrote {OUT_PATH.relative_to(config.PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
