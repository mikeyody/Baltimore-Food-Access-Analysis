"""Central configuration: paths, geography, and data source definitions.

Everything that might change between runs (URLs, dataset IDs, years) lives
here so the rest of the code never hardcodes it. If a source moves, this is
the only file that needs editing.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
SQL_DIR = PROJECT_ROOT / "sql"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
REPORTS_DIR = PROJECT_ROOT / "reports"
DB_PATH = DATA_DIR / "baltimore_food_access.duckdb"

# ---------------------------------------------------------------------------
# Geography
# ---------------------------------------------------------------------------
STATE_FIPS = "24"          # Maryland
COUNTY_FIPS = "510"        # Baltimore City (an independent city, not Baltimore County = 005)
COUNTY_GEOID = STATE_FIPS + COUNTY_FIPS  # "24510"
# Baltimore City has 199 census tracts in the 2020 Census tract vintage
# (200 in 2010). Used by the tests as an independent check on the data.
EXPECTED_TRACT_COUNT = 199

# ---------------------------------------------------------------------------
# HTTP behaviour
# ---------------------------------------------------------------------------
HTTP_TIMEOUT_SECONDS = 120
HTTP_MAX_RETRIES = 4
HTTP_BACKOFF_FACTOR = 2    # waits 2s, 4s, 8s, 16s between retries
# Some federal sites (fna.usda.gov) return 403 to clients without a browser-like UA.
HTTP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "baltimore-food-access-portfolio/1.0 (+local research project)"
)

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------
# 1. USDA ERS Food Access Research Atlas.
#    In July 2026 ERS split the Atlas into two products:
#      - SRAM (SNAP-authorized Retailer Access Map), 2025 data, 2020 tracts
#      - LRAM (Large Retailer Access Map, formerly "FARA"), 2019 data, 2010 tracts
#    We download both: LRAM has the classic supermarket-based low-income /
#    low-access flags; SRAM is newer and already on 2020 tracts.
ERS_BASE = "https://www.ers.usda.gov"
USDA_SRAM_2025_URL = (
    ERS_BASE + "/media/29395/2025-snap-authorized-retailer-access-map-sram-data.zip?v=49497"
)
USDA_LRAM_2019_URL = (
    ERS_BASE + "/media/5627/2019-large-retailer-access-map-lram-formerly-known-as-the-"
    "food-access-research-atlas-fara-data.zip?v=28511"
)

# 2. CDC PLACES, census tract data, 2025 release (Socrata dataset ID cwsq-ngmh).
PLACES_DATASET_ID = "cwsq-ngmh"
PLACES_RELEASE = "2025"
PLACES_URL = f"https://data.cdc.gov/resource/{PLACES_DATASET_ID}.csv"
PLACES_MEASURES = {
    "DIABETES": "Diagnosed diabetes among adults",
    "OBESITY": "Obesity among adults",
    "BPHIGH": "High blood pressure among adults",
    "CHD": "Coronary heart disease among adults",
}

# 3. Census ACS 5-year estimates via the Census Data API (key required since 2025).
ACS_YEAR = 2024            # the 2020-2024 5-year release (published Jan 29, 2026)
ACS_URL = f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5"
# Variable code -> readable name. "E" = estimate, "M" = margin of error.
ACS_VARIABLES = {
    "B01003_001E": "total_population",
    "B01003_001M": "total_population_moe",
    "B19013_001E": "median_household_income",
    "B19013_001M": "median_household_income_moe",
    # Poverty status: universe = people for whom poverty status is determined
    "B17001_001E": "poverty_universe",
    "B17001_002E": "below_poverty",
    "B17001_002M": "below_poverty_moe",
    # Vehicles available: universe = occupied housing units (households)
    "B08201_001E": "households",
    "B08201_002E": "households_no_vehicle",
    "B08201_002M": "households_no_vehicle_moe",
    # Age 65+ = sum of the 65+ age bins in table B01001 (sex by age)
    "B01001_020E": "male_65_66",
    "B01001_021E": "male_67_69",
    "B01001_022E": "male_70_74",
    "B01001_023E": "male_75_79",
    "B01001_024E": "male_80_84",
    "B01001_025E": "male_85_plus",
    "B01001_044E": "female_65_66",
    "B01001_045E": "female_67_69",
    "B01001_046E": "female_70_74",
    "B01001_047E": "female_75_79",
    "B01001_048E": "female_80_84",
    "B01001_049E": "female_85_plus",
}

# 4. USDA FNA (formerly FNS) SNAP Retailer Locator: public ArcGIS Feature Service.
SNAP_FEATURE_SERVICE = (
    "https://services1.arcgis.com/RLQu0rK7h4kbsBq5/arcgis/rest/services/"
    "snap_retailer_location_data/FeatureServer/0"
)
# We pull every Maryland retailer (not just Baltimore City) so the nearest-store
# distance isn't biased at the city line: a store across the border in
# Baltimore County can still be the closest one for a city resident.
SNAP_WHERE = "State = 'MD'"
FULL_SERVICE_STORE_TYPES = ["Supermarket", "Super Store"]

# 5. Census TIGER/Line tract boundaries + population-weighted centroids.
TIGER_YEAR = 2024          # matches the ACS 2020-2024 geography (2020 tract vintage)
TIGER_TRACTS_URL = (
    f"https://www2.census.gov/geo/tiger/TIGER{TIGER_YEAR}/TRACT/"
    f"tl_{TIGER_YEAR}_{STATE_FIPS}_tract.zip"
)
CENPOP_TRACT_URL = (
    "https://www2.census.gov/geo/docs/reference/cenpop2020/tract/"
    f"CenPop2020_Mean_TR{STATE_FIPS}.txt"
)

# Official 2020 <-> 2010 tract relationship file (Maryland), for the crosswalk.
TRACT_RELATIONSHIP_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/rel2020/tract/"
    f"tab20_tract20_tract10_st{STATE_FIPS}.txt"
)

# 6. Optional: Baltimore City open data (Open Baltimore ArcGIS Hub).
OPEN_BALTIMORE_LAYERS = {
    "farmers_markets": (
        "https://services1.arcgis.com/UWYHeuuJISiGmgXx/arcgis/rest/services/"
        "Farmers_Market/FeatureServer/0"
    ),
    "city_grocery_stores": (
        "https://services1.arcgis.com/UWYHeuuJISiGmgXx/arcgis/rest/services/"
        "Grocery_Store/FeatureServer/0"
    ),
}
# Neighborhood Statistical Area (NSA) polygons from the city's GIS server, used
# only to give tracts recognizable neighborhood names in reports and maps.
NEIGHBORHOODS_LAYER = (
    "https://baltegis.baltimorecity.gov/server/rest/services/CityView/Neighborhoods/FeatureServer/0"
)


# ---------------------------------------------------------------------------
# Need score (Phase 4) - the ONE place to change weights
# ---------------------------------------------------------------------------
# Each component is standardized across Baltimore tracts (higher = more need),
# multiplied by its weight, and summed. Weights must add to 1.
#
# Why these defaults:
#   food_access 0.30  The project's core question is access, so distance to the
#                     nearest full-service grocery store gets the largest weight.
#   health      0.25  Diet-related disease (diabetes, obesity, high blood
#                     pressure, heart disease) is what better food access aims
#                     to improve, so it is the second-largest weight.
#   poverty     0.20  Low income limits what people can buy even when a store is
#                     near; it also drives eligibility for SNAP/WIC programs.
#   no_car      0.15  Distance matters most for households that must walk or
#                     take transit; this multiplies the access problem.
#   seniors     0.10  Older residents face mobility limits, but the share of
#                     65+ residents is a weaker, indirect signal, so it gets the
#                     smallest weight.
NEED_SCORE_WEIGHTS = {
    "food_access": 0.30,
    "health": 0.25,
    "poverty": 0.20,
    "no_car": 0.15,
    "seniors": 0.10,
}
# "zscore" (default) or "minmax". Z-scores are clipped to +/-3 so one extreme
# tract can't dominate a component.
NEED_SCORE_STANDARDIZATION = "zscore"
Z_CLIP = 3.0

# Alternative weightings for the sensitivity check (Phase 4).
NEED_SCORE_SCENARIOS = {
    "Equal weights": {"food_access": 0.20, "health": 0.20, "poverty": 0.20,
                      "no_car": 0.20, "seniors": 0.20},
    "Health-focused": {"food_access": 0.20, "health": 0.40, "poverty": 0.20,
                       "no_car": 0.10, "seniors": 0.10},
    "Mobility-focused": {"food_access": 0.35, "health": 0.10, "poverty": 0.10,
                         "no_car": 0.30, "seniors": 0.15},
}
