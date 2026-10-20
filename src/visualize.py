"""Phase 5b: static charts (outputs/charts/*.png) and the interactive map (outputs/map.html).

    python -m src.visualize

Every chart reads from the DuckDB results tables, so the figures always match
the report. Colors follow one small palette (validated for color-vision
deficiency): a single blue ramp for magnitude, and blue/orange for two-group
comparisons; text stays in neutral ink.
"""

from __future__ import annotations

import logging

import folium
import geopandas as gpd
import matplotlib

matplotlib.use("Agg")  # render to files, no window
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from branca.colormap import LinearColormap  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from src import config  # noqa: E402
from src.load import connect  # noqa: E402

log = logging.getLogger("visualize")
CHARTS = config.OUTPUTS_DIR / "charts"
MAP_PATH = config.OUTPUTS_DIR / "map.html"

# ---------------------------------------------------------------------------
# Palette (reference data-viz palette, light mode)
# ---------------------------------------------------------------------------
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
NEUTRAL = "#f0efec"
BLUE = "#2a78d6"     # categorical slot 1
ORANGE = "#eb6834"   # categorical slot 2
BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
SEQ_CMAP = LinearSegmentedColormap.from_list("need_blue", BLUE_RAMP)

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"], "font.size": 10,
    "text.color": INK, "axes.labelcolor": INK_2, "axes.edgecolor": AXIS,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.titlesize": 13, "axes.titleweight": "bold",
    "axes.titlelocation": "left", "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
    "legend.frameon": False,
})


def _titles(ax, title: str, subtitle: str) -> None:
    """Bold title with a one-line subtitle directly underneath, both left-aligned."""
    ax.set_title(title, pad=30)
    ax.annotate(subtitle, xy=(0, 1), xycoords="axes fraction", xytext=(0, 9),
                textcoords="offset points", color=INK_2, fontsize=9.5, va="bottom")


def _source(fig, text: str) -> None:
    # Placed just below the figure; bbox_inches="tight" expands the canvas to fit it.
    fig.text(0.01, -0.02, text, color=MUTED, fontsize=8, ha="left", va="top")


def _save(fig, name: str) -> None:
    CHARTS.mkdir(parents=True, exist_ok=True)
    fig.savefig(CHARTS / name, dpi=200, bbox_inches="tight")
    plt.close(fig)
    log.info("saved outputs/charts/%s", name)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def load_data(con):
    """Tract polygons + every attribute the charts and map need."""
    con.execute("LOAD spatial")
    tracts = con.execute("""
        SELECT a.*, n.need_score, n.need_score_0_100, n.need_rank, n.top_driver,
               ST_AsWKB(t.geom) AS wkb
        FROM analysis.tract_analysis a
        JOIN clean.tracts t USING (geoid)
        LEFT JOIN results.need_scores n USING (geoid)
    """).df()
    gdf = gpd.GeoDataFrame(tracts.drop(columns="wkb"),
                           geometry=gpd.GeoSeries.from_wkb(tracts["wkb"].apply(bytes)),
                           crs="EPSG:4269")
    stores = con.execute("SELECT * FROM analysis.stores").df()
    markets = con.execute("SELECT * FROM clean.farmers_markets").df()
    return gdf, stores, markets


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def chart_need_map(gdf, stores) -> None:
    """Choropleth of the need score with full-service grocery stores and the top 10 outlined."""
    g = gdf.to_crs("EPSG:2248")
    fs = stores[stores.is_full_service]
    fs = gpd.GeoDataFrame(fs, geometry=gpd.points_from_xy(fs.lon, fs.lat), crs="EPSG:4326").to_crs(g.crs)
    xmin, ymin, xmax, ymax = g.total_bounds
    pad = 2000

    fig, ax = plt.subplots(figsize=(8, 8.6))
    ax.grid(False)
    ax.set_axis_off()
    g[g.need_score_0_100.isna()].plot(ax=ax, color=NEUTRAL, edgecolor=SURFACE, linewidth=0.4, hatch="///")
    g[g.need_score_0_100.notna()].plot(ax=ax, column="need_score_0_100", cmap=SEQ_CMAP, vmin=0, vmax=100,
                                       edgecolor=SURFACE, linewidth=0.4)
    top = g[g.need_rank <= 10]
    top.boundary.plot(ax=ax, color=ORANGE, linewidth=1.8)
    for _, r in top.iterrows():
        p = r.geometry.representative_point()
        ax.text(p.x, p.y, str(int(r.need_rank)), ha="center", va="center", fontsize=8, fontweight="bold",
                color=INK, bbox=dict(boxstyle="circle,pad=0.2", fc=SURFACE, ec="none", alpha=0.9))
    fs.plot(ax=ax, color=INK, markersize=14, edgecolor=SURFACE, linewidth=0.8, zorder=5)
    ax.set_xlim(xmin - pad, xmax + pad)
    ax.set_ylim(ymin - pad, ymax + pad)

    sm = plt.cm.ScalarMappable(cmap=SEQ_CMAP, norm=plt.Normalize(0, 100))
    cb = fig.colorbar(sm, ax=ax, orientation="horizontal", fraction=0.035, pad=0.02, aspect=40)
    cb.outline.set_visible(False)
    cb.set_label("Need score (0 = lowest need in the city, 100 = highest)", color=INK_2)
    handles = [
        Line2D([], [], marker="o", ls="", color=INK, markeredgecolor=SURFACE, markersize=6,
               label="Full-service grocery store"),
        Line2D([], [], color=ORANGE, lw=2, label="Top 10 need tract (rank shown)"),
        plt.Rectangle((0, 0), 1, 1, fc=NEUTRAL, hatch="///", ec=MUTED, lw=0.4, label="Not scored"),
    ]
    ax.legend(handles=handles, loc="lower left", fontsize=8.5)
    _titles(ax, "Where new food resources would help most", "Baltimore City census tracts by composite need score")
    _source(fig, "Sources: USDA SNAP retailers (2026), CDC PLACES 2025, ACS 2020-24, Census TIGER/Line.")
    _save(fig, "01_need_score_map.png")


def chart_distance_hist(gdf) -> None:
    d = gdf["dist_nearest_full_service_mi"]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    bins = np.arange(0, np.ceil(d.max() * 10) / 10 + 0.1, 0.1)
    ax.hist(d, bins=bins, color=BLUE, edgecolor=SURFACE, linewidth=2)
    top = ax.get_ylim()[1] * 1.18
    ax.set_ylim(0, top)
    for x, n in [(0.5, int((d > 0.5).sum())), (1.0, int((d > 1.0).sum()))]:
        ax.axvline(x, color=INK_2, linewidth=1)
        ax.text(x + 0.015, top * 0.98, f"{n} tracts beyond {x:g} mi", color=INK, fontsize=9, va="top",
                bbox=dict(fc=SURFACE, ec="none", pad=1))
    ax.set_xlabel("Straight-line miles from tract population center to nearest full-service grocery")
    ax.set_ylabel("Census tracts")
    ax.grid(axis="x", visible=False)
    _titles(ax, "Most tracts are within a mile of a supermarket, but not within a short walk", f"Distance to the nearest full-service grocery store, {len(d)} Baltimore tracts "
                  f"(median {d.median():.2f} mi)")
    _source(fig, "Sources: USDA SNAP Retailer Locator (2026), Census 2020 population centroids.")
    _save(fig, "02_distance_distribution.png")


def chart_health_gap(con) -> None:
    t = con.execute("""
        SELECT outcome, low_access_mean, other_mean, difference_pts, p_value
        FROM results.q3_tests
        WHERE comparison = 'Low-income & nearest full-service store > 1/2 mi (2026)'
    """).df()
    counts = con.execute("""SELECT tract_group, tracts FROM results.q3_health_by_access
        WHERE comparison = 'Low-income & nearest full-service store > 1/2 mi (2026)'""").df()
    n = dict(zip(counts.tract_group, counts.tracts))
    x = np.arange(len(t))
    w = 0.38
    fig, ax = plt.subplots(figsize=(8, 4.8))
    b1 = ax.bar(x - w / 2 - 0.01, t.low_access_mean, w, color=BLUE,
                label=f"Low-income & > 1/2 mi from a supermarket ({n['Low access']} tracts)")
    b2 = ax.bar(x + w / 2 + 0.01, t.other_mean, w, color=AXIS, label=f"All other tracts ({n['Other tracts']} tracts)")
    for bars in (b1, b2):
        for bar in bars:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.6, f"{bar.get_height():.1f}%",
                    ha="center", va="bottom", fontsize=8.5, color=INK)
    for i, r in t.iterrows():
        ax.text(x[i], max(r.low_access_mean, r.other_mean) + 4.2, f"+{r.difference_pts:.1f} pts",
                ha="center", fontsize=9, fontweight="bold", color=INK)
    ax.set_xticks(x, t.outcome, color=INK_2)
    ax.set_ylabel("Share of adults (%)")
    ax.set_ylim(0, t[["low_access_mean", "other_mean"]].max().max() + 16)
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper left", fontsize=8.5)
    _titles(ax, "Low-income, low-access tracts carry a heavier chronic-disease burden", "Average tract rate of each condition; all gaps significant (Welch t-test p < 0.05)"
              if (t.p_value < 0.05).all() else "Average tract rate of each condition")
    _source(fig, "Sources: CDC PLACES 2025 release (BRFSS 2023), USDA, SNAP retailers 2026.")
    _save(fig, "03_health_gap.png")


def chart_income_scatter(gdf, con) -> None:
    d = gdf[gdf.has_health_data & gdf.median_household_income.notna()]
    far = d.dist_nearest_full_service_mi > 0.5
    r_raw = con.execute("""SELECT r FROM results.q4_partial_corr WHERE outcome = 'High blood pressure'
        AND controlling_for = 'INCOME vs outcome, controlling for distance'""").fetchone()[0]
    fig, ax = plt.subplots(figsize=(8, 5))
    for mask, color, label in [(~far, BLUE, "Within 1/2 mi of a full-service grocery"),
                               (far, ORANGE, "More than 1/2 mi away")]:
        ax.scatter(d.loc[mask, "median_household_income"], d.loc[mask, "high_bp_pct"], s=34,
                   color=color, edgecolor=SURFACE, linewidth=1, label=f"{label} ({mask.sum()} tracts)")
    ax.set_xscale("log")
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v / 1000:,.0f}k"))
    ax.set_xticks([25000, 50000, 100000, 200000])
    ax.xaxis.set_minor_formatter(plt.NullFormatter())
    ax.set_xlabel("Median household income (log scale)")
    ax.set_ylabel("Adults with high blood pressure (%)")
    ax.legend(loc="upper right", fontsize=8.5)
    _titles(ax, "Income, not distance, is the dominant pattern", f"Each dot is a tract. Partial correlation of income with high blood pressure, "
                  f"holding distance fixed: r = {r_raw:.2f}")
    _source(fig, "Sources: ACS 2020-24, CDC PLACES 2025, USDA SNAP retailers 2026.")
    _save(fig, "04_income_vs_blood_pressure.png")


def chart_top10(con) -> None:
    t = con.execute("""
        SELECT need_rank, geoid, neighborhood, need_score_0_100, top_driver, total_population
        FROM results.need_scores WHERE need_rank <= 10 ORDER BY need_rank DESC
    """).df()
    labels = [f"{r.neighborhood}  ({r.geoid[-6:]})" for r in t.itertuples()]
    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.barh(labels, t.need_score_0_100, color=BLUE, height=0.62)
    for bar, r in zip(bars, t.itertuples()):
        ax.text(bar.get_width() + 1, bar.get_y() + bar.get_height() / 2,
                f"{bar.get_width():.0f}  ·  driver: {r.top_driver.replace('_', ' ')}  ·  {r.total_population:,} residents",
                va="center", fontsize=8.5, color=INK_2)
    ax.set_xlim(0, 160)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.tick_params(axis="y", colors=INK, labelsize=9)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Need score (0-100)")
    _titles(ax, "Top 10 tracts by combined need", "Default weights: access 30%, health 25%, poverty 20%, no car 15%, age 65+ 10%"
              if config.NEED_SCORE_WEIGHTS == {"food_access": 0.30, "health": 0.25, "poverty": 0.20,
                                               "no_car": 0.15, "seniors": 0.10}
              else "Weights from src/config.py")
    _source(fig, "Tract code in parentheses. Score is relative within Baltimore City.")
    _save(fig, "05_top10_need.png")


def chart_sensitivity(con) -> None:
    r = con.execute("""
        SELECT s.*, n.neighborhood FROM results.need_sensitivity_ranks s
        JOIN results.need_scores n USING (geoid)
        WHERE s.best_rank <= 10 ORDER BY s."Default" DESC
    """).df()
    scen = [c for c in r.columns if c not in ("geoid", "neighborhood", "best_rank", "worst_rank",
                                              "in_top10_count", "scenarios_total")]
    y = np.arange(len(r))
    fig, ax = plt.subplots(figsize=(8, 0.42 * len(r) + 1.6))
    ax.hlines(y, r.best_rank, r.worst_rank, color=AXIS, linewidth=3)
    others = [c for c in scen if c != "Default"]
    ax.scatter(r[others].values.ravel(), np.repeat(y, len(others)), s=22, color=MUTED, zorder=3,
               label="Alternative weightings")
    ax.scatter(r["Default"], y, s=48, color=BLUE, edgecolor=SURFACE, linewidth=1.2, zorder=4,
               label="Default weights")
    ax.axvline(10.5, color=INK_2, linewidth=1)
    ax.text(10.7, len(r) - 0.6, "top-10 cut-off", color=INK_2, fontsize=8.5)
    ax.set_yticks(y, [f"{n}  ({g[-6:]})" for n, g in zip(r.neighborhood, r.geoid)], color=INK, fontsize=8.5)
    ax.invert_xaxis()
    ax.set_xlabel("Need rank (1 = highest need)")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="upper left", fontsize=8.5)
    _titles(ax, "The top of the list is stable across weightings", f"Rank of every tract that reaches the top 10 under any of {len(scen)} weighting scenarios")
    _source(fig, "Scenarios defined in src/config.py (NEED_SCORE_SCENARIOS) plus min-max scaling.")
    _save(fig, "06_sensitivity.png")


# ---------------------------------------------------------------------------
# Interactive map
# ---------------------------------------------------------------------------
def _fmt(v, kind):
    if pd.isna(v):
        return "n/a"
    return {"pct": f"{v:.1f}%", "mi": f"{v:.2f} mi", "usd": f"${v:,.0f}", "int": f"{int(v):,}",
            "score": f"{v:.0f}"}[kind]


def build_map(gdf, stores, markets) -> None:
    g = gdf.to_crs("EPSG:4326").copy()
    g["geometry"] = g.geometry.simplify(0.00002, preserve_topology=True)
    center = [g.geometry.union_all().centroid.y, g.geometry.union_all().centroid.x]
    m = folium.Map(location=center, zoom_start=12, tiles="OpenStreetMap", control_scale=True)

    cmap = LinearColormap(BLUE_RAMP, vmin=0, vmax=100,
                          caption="Need score (0 = lowest need in Baltimore, 100 = highest)")

    def popup_html(r) -> str:
        rank = "not scored" if pd.isna(r.need_rank) else f"#{int(r.need_rank)} of {int(gdf.need_rank.max())}"
        rows = [
            ("Need score", f"{_fmt(r.need_score_0_100, 'score')} ({rank})"),
            ("Top driver", (r.top_driver or "n/a").replace("_", " ") if isinstance(r.top_driver, str) else "n/a"),
            ("Population", _fmt(r.total_population, "int")),
            ("Nearest full-service grocery", f"{r.nearest_full_service_store} ({_fmt(r.dist_nearest_full_service_mi, 'mi')})"),
            ("Median household income", _fmt(r.median_household_income, "usd")),
            ("Poverty rate", _fmt(r.pct_poverty, "pct")),
            ("Households without a car", f"{_fmt(r.pct_households_no_vehicle, 'pct')} ({_fmt(r.households_no_vehicle, 'int')})"),
            ("Residents 65+", _fmt(r.pct_65_plus, "pct")),
            ("Diabetes / Obesity", f"{_fmt(r.diabetes_pct, 'pct')} / {_fmt(r.obesity_pct, 'pct')}"),
            ("High blood pressure", _fmt(r.high_bp_pct, "pct")),
            ("USDA low-income & low-access (2019, 1/2 mi)", "Yes" if r.lila_half_mi_2019 else "No"),
        ]
        body = "".join(f"<tr><td style='color:{INK_2};padding:2px 8px 2px 0'>{k}</td>"
                       f"<td style='color:{INK}'><b>{v}</b></td></tr>" for k, v in rows)
        return (f"<div style='font-family:Segoe UI,sans-serif;font-size:12px'>"
                f"<div style='font-size:14px;font-weight:600'>{r.neighborhood}</div>"
                f"<div style='color:{MUTED};margin-bottom:6px'>{r.tract_name} · GEOID {r.geoid}</div>"
                f"<table>{body}</table></div>")

    tract_layer = folium.FeatureGroup(name="Tracts by need score", show=True)
    for _, r in g.iterrows():
        fill = NEUTRAL if pd.isna(r.need_score_0_100) else cmap(r.need_score_0_100)
        is_top = (not pd.isna(r.need_rank)) and r.need_rank <= 10
        folium.GeoJson(
            r.geometry.__geo_interface__,
            style_function=lambda _, fill=fill, top=is_top: {
                "fillColor": fill, "fillOpacity": 0.75,
                "color": ORANGE if top else "#ffffff", "weight": 2.5 if top else 0.6},
            highlight_function=lambda _: {"weight": 3, "color": INK},
            tooltip=f"{r.neighborhood}: need score {_fmt(r.need_score_0_100, 'score')}",
            popup=folium.Popup(popup_html(r), max_width=360),
        ).add_to(tract_layer)
    tract_layer.add_to(m)
    cmap.add_to(m)

    fs_layer = folium.FeatureGroup(name="Full-service grocery stores", show=True)
    for s in stores[stores.is_full_service].itertuples():
        folium.CircleMarker([s.lat, s.lon], radius=5, color="#ffffff", weight=1.5, fill=True,
                            fill_color=INK, fill_opacity=1,
                            tooltip=f"{s.store_name} ({s.store_type})<br>{s.address}").add_to(fs_layer)
    fs_layer.add_to(m)

    small_layer = folium.FeatureGroup(name="Small grocery & convenience stores (SNAP)", show=False)
    for s in stores[stores.store_category.isin(["Small grocery", "Convenience store"])].itertuples():
        folium.CircleMarker([s.lat, s.lon], radius=2.5, color=MUTED, weight=0, fill=True, fill_opacity=0.8,
                            tooltip=f"{s.store_name} ({s.store_category})").add_to(small_layer)
    small_layer.add_to(m)

    fm_layer = folium.FeatureGroup(name="Farmers markets (Open Baltimore, 2023)", show=False)
    for f in markets.itertuples():
        folium.CircleMarker([f.lat, f.lon], radius=5, color="#ffffff", weight=1.5, fill=True,
                            fill_color=ORANGE, fill_opacity=1,
                            tooltip=f"{f.market_name}<br>{f.day_time or ''}").add_to(fm_layer)
    fm_layer.add_to(m)

    title = ("<div style='position:fixed;bottom:40px;left:10px;z-index:9999;background:#fcfcfb;"
             "padding:8px 12px;border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.2);"
             "font-family:Segoe UI,sans-serif'><b>Baltimore City food access need</b><br>"
             f"<span style='color:{INK_2};font-size:12px'>Click a tract for details · "
             "orange outline = top 10 need</span></div>")
    m.get_root().html.add_child(folium.Element(title))
    folium.LayerControl(collapsed=False).add_to(m)
    m.save(str(MAP_PATH))
    log.info("saved outputs/map.html")


def main() -> None:
    con = connect(read_only=True)
    try:
        gdf, stores, markets = load_data(con)
        chart_need_map(gdf, stores)
        chart_distance_hist(gdf)
        chart_health_gap(con)
        chart_income_scatter(gdf, con)
        chart_top10(con)
        chart_sensitivity(con)
        build_map(gdf, stores, markets)
    finally:
        con.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    main()
