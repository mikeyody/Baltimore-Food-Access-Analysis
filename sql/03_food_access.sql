-- =============================================================================
-- 03_food_access.sql
-- Measure current (2026) food access for every tract from the SNAP store list.
--
-- Distance = straight line from the tract's population-weighted centroid to the
-- nearest full-service grocery store (Supermarket or Super Store), measured in
-- Maryland State Plane feet (EPSG:2248) and converted to miles. Stores outside
-- the city count too, so tracts at the city line aren't penalised.
-- =============================================================================

LOAD spatial;

CREATE OR REPLACE TABLE clean.tract_food_access AS
WITH distances AS (
    -- Every tract x every full-service store in Maryland (199 x ~760 pairs: small).
    SELECT
        c.geoid,
        s.store_id,
        s.store_name,
        s.address,
        ST_Distance(c.geom_ft, s.geom_ft) / 5280.0 AS miles
    FROM clean.tract_centroids c
    CROSS JOIN clean.snap_stores s
    WHERE s.is_full_service
),
nearest AS (
    SELECT
        geoid,
        min(miles)                              AS dist_nearest_full_service_mi,
        arg_min(store_name, miles)              AS nearest_full_service_store,
        arg_min(address, miles)                 AS nearest_full_service_address,
        count(*) FILTER (WHERE miles <= 0.5)    AS full_service_within_half_mi,
        count(*) FILTER (WHERE miles <= 1.0)    AS full_service_within_1_mi
    FROM distances
    GROUP BY geoid
),
any_snap AS (
    -- Nearest SNAP store of any kind, for comparison (corner stores count here).
    SELECT c.geoid, min(ST_Distance(c.geom_ft, s.geom_ft)) / 5280.0 AS dist_nearest_any_snap_mi
    FROM clean.tract_centroids c
    CROSS JOIN clean.snap_stores s
    GROUP BY c.geoid
),
in_tract AS (
    -- What is physically inside each tract.
    SELECT
        tract_geoid AS geoid,
        count(*)                                                        AS snap_stores_in_tract,
        count(*) FILTER (WHERE store_category = 'Full-service grocery') AS full_service_in_tract,
        count(*) FILTER (WHERE store_category = 'Small grocery')        AS small_grocery_in_tract,
        count(*) FILTER (WHERE store_category = 'Convenience store')    AS convenience_in_tract
    FROM clean.snap_stores
    WHERE tract_geoid IS NOT NULL
    GROUP BY tract_geoid
)
SELECT
    c.geoid,
    c.centroid_type,
    round(n.dist_nearest_full_service_mi, 3)       AS dist_nearest_full_service_mi,
    n.nearest_full_service_store,
    n.nearest_full_service_address,
    n.full_service_within_half_mi,
    n.full_service_within_1_mi,
    round(a.dist_nearest_any_snap_mi, 3)           AS dist_nearest_any_snap_mi,
    -- Tracts with no stores at all have no row in in_tract, so 0 (not NULL) is correct here.
    coalesce(i.snap_stores_in_tract, 0)            AS snap_stores_in_tract,
    coalesce(i.full_service_in_tract, 0)           AS full_service_in_tract,
    coalesce(i.small_grocery_in_tract, 0)          AS small_grocery_in_tract,
    coalesce(i.convenience_in_tract, 0)            AS convenience_in_tract
FROM clean.tract_centroids c
LEFT JOIN nearest  n USING (geoid)
LEFT JOIN any_snap a USING (geoid)
LEFT JOIN in_tract i USING (geoid);
