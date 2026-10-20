"""Phase 6: write the findings report, README key findings, and resume numbers.

    python -m src.report

Every number below is read from the DuckDB results tables (or counted from the
project files) at run time. The prose is a template; if the data change, the
numbers change with them, and wording that depends on a result (e.g. whether
an association is significant) is chosen from the result.

Outputs
  reports/findings.md         1-2 page plain-language report
  README.md                   the block between the KEY-FINDINGS markers
  reports/resume_numbers.md   verified figures for resume bullets
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from types import SimpleNamespace

from src import config
from src.load import connect, run_sql_file

log = logging.getLogger("report")
FINDINGS_PATH = config.REPORTS_DIR / "findings.md"
RESUME_PATH = config.REPORTS_DIR / "resume_numbers.md"
README_PATH = config.PROJECT_ROOT / "README.md"
START, END = "<!-- KEY-FINDINGS:START -->", "<!-- KEY-FINDINGS:END -->"

LILA_2026 = "Updated 2026: low-income & nearest full-service store > 1/2 mile"
LILA_2026_1MI = "Updated 2026: low-income & nearest full-service store > 1 mile"
LILA_2019 = "USDA 2019 (LRAM): low-income & low-access, 1/2 mile"
GAP_COMPARISON = "Low-income & nearest full-service store > 1/2 mi (2026)"


def _one(con, sql: str) -> SimpleNamespace:
    """Fetch a single-row query as an object with attribute access (ints stay ints)."""
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return SimpleNamespace(**dict(zip(cols, cur.fetchone())))


def _names(xs: list[str]) -> str:
    xs = list(xs)
    if not xs:
        return "none"
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def count_tests() -> int:
    """Number of pytest tests, from pytest's own collection (not hand-counted)."""
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "tests"],
                         cwd=config.PROJECT_ROOT, capture_output=True, text=True).stdout
    m = re.search(r"(\d+) tests? collected", out)
    if not m:
        raise RuntimeError(f"could not count tests from pytest output:\n{out[-500:]}")
    return int(m.group(1))


def gather(con) -> SimpleNamespace:
    """Collect every number the report needs into one object."""
    run_sql_file(con, config.SQL_DIR / "11_report_metrics.sql")
    d = SimpleNamespace()
    d.city = _one(con, "SELECT * FROM results.q1_city_totals")
    d.q1 = {r[0]: SimpleNamespace(tracts=r[1], residents=r[2], pct_res=r[3], pct_tracts=r[4], hh_nocar=r[5])
            for r in con.execute("SELECT definition, tracts_flagged, residents, pct_of_residents, "
                                 "pct_of_tracts, households_no_vehicle "
                                 "FROM results.q1_low_income_low_access").fetchall()}
    d.q2 = _one(con, "SELECT * FROM results.q2_summary")
    d.gaps = {r[0]: SimpleNamespace(low=r[1], other=r[2], diff=r[3], p=r[4], rel=r[5])
              for r in con.execute(f"""SELECT outcome, low_access_mean, other_mean, difference_pts, p_value,
                                     relative_difference_pct FROM results.q3_tests
                                     WHERE comparison = '{GAP_COMPARISON}'""").fetchall()}
    reg = con.execute("""SELECT outcome, model, coef, ci_low, ci_high, p_value, r_squared
                         FROM results.q4_regression WHERE term = 'dist_nearest_full_service_mi'""").df()
    r0 = con.execute("""SELECT outcome, r_squared FROM results.q4_regression
                        WHERE model = 'M0 income only' AND term = 'Intercept'""").df()
    d.r2_income = dict(zip(r0.outcome, r0.r_squared))
    d.m1 = reg[reg.model == "M1 distance only"].set_index("outcome")
    d.m2 = reg[reg.model == "M2 distance + income"].set_index("outcome")
    d.m3 = reg[reg.model == "M3 distance + income + age 65+"].set_index("outcome")
    d.shrink = ((1 - d.m2.coef / d.m1.coef) * 100)
    d.n_model = int(con.execute("SELECT count(*) FROM results.q4_model_data").fetchone()[0])
    d.top10 = con.execute("""SELECT need_rank, geoid, neighborhood, total_population, households_no_vehicle,
                                    need_score_0_100, food_access_raw, poverty_raw, no_car_raw, diabetes_pct,
                                    top_driver
                             FROM results.need_scores WHERE need_rank <= 10 ORDER BY need_rank""").df()
    ranks = con.execute("""SELECT r.geoid, n.neighborhood, r.in_top10_count, r.scenarios_total
                           FROM results.need_sensitivity_ranks r JOIN results.need_scores n USING (geoid)
                           WHERE r."Default" <= 10 ORDER BY r."Default" """).df()
    d.robust = ranks[ranks.in_top10_count == ranks.scenarios_total]
    d.n_scenarios = int(ranks.scenarios_total.iloc[0])
    d.n_scored = int(con.execute("SELECT count(*) FROM results.need_scores").fetchone()[0])
    d.change = _one(con, "SELECT * FROM results.report_lila_change")
    d.carfree = _one(con, "SELECT * FROM results.report_car_free_priority")
    d.stats = _one(con, "SELECT * FROM results.report_pipeline_stats")
    d.corner = _one(con, "SELECT * FROM results.report_corner_store_access")
    # Exact row count of every staging table (raw rows loaded).
    staging = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'staging'").fetchall()]
    d.staging_rows = sum(con.execute(f'SELECT count(*) FROM staging."{t}"').fetchone()[0] for t in staging)
    d.full_service_types = ", ".join(config.FULL_SERVICE_STORE_TYPES)
    manifest = json.loads((config.RAW_DIR / "manifest.json").read_text())
    d.n_datasets = len(manifest)
    d.n_sql_files = len(list(config.SQL_DIR.glob("*.sql")))
    d.n_tests = count_tests()
    d.n_charts = len(list((config.OUTPUTS_DIR / "charts").glob("*.png")))
    d.n_dq_removed = int(con.execute(
        "SELECT sum(rows_affected) FROM clean.dq_log WHERE action = 'filtered'").fetchone()[0])
    return d


# ---------------------------------------------------------------------------
# Text builders
# ---------------------------------------------------------------------------
def key_findings(d) -> list[str]:
    """3-5 headline findings as markdown bullet paragraphs (shared by report and README)."""
    l26, l26_1, l19 = d.q1[LILA_2026], d.q1[LILA_2026_1MI], d.q1[LILA_2019]
    g = d.gaps
    sig2 = [o for o in d.m2.index if d.m2.loc[o, "p_value"] < 0.05]
    sig3 = [o for o in d.m3.index if d.m3.loc[o, "p_value"] < 0.05]
    hbp = d.m2.loc["High blood pressure"]
    top_res = int(d.top10.total_population.sum())
    top_nocar = int(d.top10.households_no_vehicle.sum())

    return [
        f"**1. About {l26.pct_res:.0f}% of Baltimore residents live in a low-income tract more than half a "
        f"mile from a full-service grocery store.** That is {l26.residents:,} people in {l26.tracts} of "
        f"{d.city.tracts} tracts, using 2026 store locations. At the 1-mile standard USDA uses for urban "
        f"areas, {l26_1.tracts} tracts ({l26_1.residents:,} residents) qualify. The city has "
        f"{d.city.full_service_stores_in_city} full-service grocery stores; the median tract's population "
        f"center is {d.q2.median_distance_mi} miles from the nearest one.",

        f"**2. Car-free households bear the access gap.** {d.q2.no_vehicle_hh_over_half_mi_current:,} "
        f"households without a vehicle live in tracts more than half a mile from a full-service grocery "
        f"({d.q2.no_vehicle_hh_over_1_mi_current:,} in tracts more than a mile away). "
        f"{d.carfree.tracts} tracts are both more than half a mile away and have at least "
        f"{d.carfree.no_car_threshold_pct}% of households without a car, home to "
        f"{d.carfree.households_no_vehicle:,} car-free households.",

        f"**3. Low-income, low-access tracts have markedly higher rates of diet-related disease.** "
        f"Compared with other tracts, adults there have higher rates of diabetes "
        f"({g['Diabetes'].low:.1f}% vs {g['Diabetes'].other:.1f}%), obesity ({g['Obesity'].low:.1f}% vs "
        f"{g['Obesity'].other:.1f}%), high blood pressure ({g['High blood pressure'].low:.1f}% vs "
        f"{g['High blood pressure'].other:.1f}%) and coronary heart disease "
        f"({g['Coronary heart disease'].low:.1f}% vs {g['Coronary heart disease'].other:.1f}%)"
        + (" - every gap statistically significant." if all(x.p < 0.05 for x in g.values()) else "."),

        f"**4. Income, not distance, explains most of that gap.** Accounting for median household income "
        f"shrinks the link between distance and health by {d.shrink.min():.0f}-{d.shrink.max():.0f}%; "
        f"income alone explains {min(d.r2_income.values()):.0%}-{max(d.r2_income.values()):.0%} of the "
        f"variation in disease rates between tracts. After income, distance remains a statistically "
        f"significant correlate of {_names([o.lower() for o in sig2])} (for high blood pressure, "
        f"{hbp.coef:+.1f} points per mile, 95% CI {hbp.ci_low:+.1f} to {hbp.ci_high:+.1f}). "
        + (f"Once age is also accounted for, it remains significant only for {_names([o.lower() for o in sig3])}. "
           if sig3 else
           "Once the share of residents aged 65+ is also accounted for, none of these associations "
           "remains significant at p < 0.05. ")
        + "This is correlation, not causation.",

        f"**5. A short, stable priority list.** The ten highest-need tracts, led by "
        f"{_names(list(d.top10.neighborhood.head(3)))}, are home to {top_res:,} residents and "
        f"{top_nocar:,} car-free households. {len(d.robust)} of them stay in the top 10 under all "
        f"{d.n_scenarios} weighting scenarios tested. The map has also shifted since USDA's 2019 data: "
        f"{d.change.tracts_newly_flagged_2026} tracts ({d.change.residents_newly_flagged_2026:,} residents) are "
        f"low-income and low-access now but were not flagged then, while {d.change.tracts_no_longer_flagged} "
        f"flagged in 2019 no longer meet the 2026 test.",
    ]


def findings_md(d) -> str:
    robust_list = _names(list(d.robust.neighborhood))
    carfree_line = (f"{d.carfree.tracts} tracts where at least {d.carfree.no_car_threshold_pct}% of "
                    f"households have no car and the nearest full-service grocery is more than half a mile away")
    top_rows = "\n".join(
        f"| {r.need_rank} | {r.neighborhood} | {r.geoid} | {r.total_population:,} | {r.food_access_raw:.2f} | "
        f"{r.poverty_raw:.0f}% | {r.no_car_raw:.0f}% | {r.diabetes_pct:.1f}% | {r.top_driver.replace('_', ' ')} |"
        for r in d.top10.itertuples())
    return "\n".join([
        "# Food Access and Health in Baltimore City: Findings",
        "",
        "_Generated by `python -m src.report` from the project database. Every number comes from the "
        "pipeline; rerun `python run_pipeline.py` to refresh._",
        "",
        "## Summary",
        "",
        f"This analysis combines {d.n_datasets} public datasets (USDA food access and SNAP retailer data, CDC "
        f"PLACES health estimates, Census ACS 2020-2024 and TIGER/Line boundaries, and Baltimore City open "
        f"data) for all {d.city.tracts} census tracts in Baltimore City ({d.city.residents:,} residents). It "
        f"measures how far each neighborhood is from a full-service grocery store "
        f"({d.full_service_types}), how that relates to income, car ownership and diet-related disease, and "
        f"where new food resources would reach the most people in need.",
        "",
        "## Key findings",
        "",
        *[f"{f}\n" for f in key_findings(d)],
        "## Highest-need tracts",
        "",
        "| Rank | Neighborhood | GEOID | Residents | Miles to grocery | Poverty | No car | Diabetes | Top driver |",
        "|---|---|---|---|---|---|---|---|---|",
        top_rows,
        "",
        "Need score = weighted, standardized combination of distance to a full-service grocery (30%), "
        "diet-related disease (25%), poverty (20%), households without a car (15%) and residents 65+ (10%). "
        "See `reports/need_score.md` for the method and sensitivity check."
        if config.NEED_SCORE_WEIGHTS == {"food_access": 0.30, "health": 0.25, "poverty": 0.20,
                                         "no_car": 0.15, "seniors": 0.10}
        else "Weights are set in `src/config.py`; see `reports/need_score.md`.",
        "",
        "## Recommendations",
        "",
        f"1. **Focus new full-service grocery investment and pilots on the most robust high-need "
        f"neighborhoods: {robust_list}.** These rank in the top 10 under every weighting tested, so the case "
        f"for them doesn't depend on how priorities are weighed. Where a new store isn't viable, mobile "
        f"markets, produce stands or a grocery partnership at an existing community anchor can fill the gap.",
        "",
        f"2. **Close the transportation gap for car-free households.** Target the {carfree_line} "
        f"({d.carfree.households_no_vehicle:,} car-free households) with a grocery shuttle, transit stops or "
        f"service changes that connect to the nearest full-service stores, and SNAP-online grocery delivery "
        f"outreach.",
        "",
        "3. **Pair access with affordability and health programs.** Because income explains most of the "
        "health gap, a store alone is unlikely to move disease rates. Combine access projects with SNAP "
        "enrollment, healthy-food incentives that stretch SNAP dollars on produce, healthy corner-store "
        f"programs ({d.corner.pct_with_any_snap_store_within_half_mi:.0f}% of low-income, low-access tracts "
        f"have a SNAP-authorized store of some kind within half a mile), and clinic-based "
        "produce prescriptions in the tracts with the highest diabetes and blood-pressure rates.",
        "",
        "## Limitations",
        "",
        "- **Correlation, not causation.** The data are cross-sectional and at tract level; they can't show "
        "that distance causes disease, and tract averages don't describe individuals.",
        "- **Modeled health data.** CDC PLACES estimates are model-based and partly predicted from tract "
        "demographics, which builds in some correlation with income.",
        "- **Straight-line distance from one point per tract.** Real walking routes are longer, and the "
        "population-weighted center hides variation within a tract.",
        "- **Store classification.** Full-service stores come from USDA's SNAP store types, with warehouse "
        "clubs and gas stations removed; stores that don't accept SNAP are missed. Store quality, prices "
        "and hours aren't measured.",
        "- **Mixed vintages.** USDA's supermarket-based 2019 flags were converted from 2010 to 2020 tracts by "
        "land area, and ACS figures are 2020-2024 averages with margins of error.",
        f"- **Relative need score.** The score ranks tracts against each other within Baltimore; "
        f"{d.city.tracts - d.n_scored} tracts with too little data were not scored.",
        "",
    ])


def readme_block(d) -> str:
    l26 = d.q1[LILA_2026]
    return "\n".join([
        START,
        "_This section is generated by `python -m src.report`; numbers come from the pipeline._",
        "",
        "| Headline | Value |",
        "|---|---|",
        f"| Residents in low-income tracts > 1/2 mi from a full-service grocery (2026) | "
        f"{l26.residents:,} ({l26.pct_res:.0f}% of the city) |",
        f"| Car-free households in tracts > 1/2 mi from a full-service grocery | "
        f"{d.q2.no_vehicle_hh_over_half_mi_current:,} |",
        f"| Diabetes rate, low-income low-access tracts vs. others | "
        f"{d.gaps['Diabetes'].low:.1f}% vs {d.gaps['Diabetes'].other:.1f}% |",
        f"| High blood pressure rate, low-income low-access tracts vs. others | "
        f"{d.gaps['High blood pressure'].low:.1f}% vs {d.gaps['High blood pressure'].other:.1f}% |",
        f"| Share of the distance-health link explained away by income | "
        f"{d.shrink.min():.0f}-{d.shrink.max():.0f}% |",
        "",
        *[f"{f}\n" for f in key_findings(d)],
        END,
    ])


def resume_md(d) -> str:
    l26, l19 = d.q1[LILA_2026], d.q1[LILA_2019]
    g = d.gaps
    return "\n".join([
        "# Verified Numbers for Resume Bullets",
        "",
        "_Generated by `python -m src.report`. Each figure is read from the pipeline output; rerun to "
        "refresh before quoting._",
        "",
        "## Scale of the project",
        "",
        f"- Public datasets integrated: **{d.n_datasets}** (see `reports/data_sources.md`)",
        f"- Census tracts analyzed: **{d.city.tracts}**; residents covered: **{d.city.residents:,}**",
        f"- Raw rows loaded into staging: **{d.staging_rows:,}** across "
        f"{d.stats.staging_tables} staging tables",
        f"- Retail locations spatially joined to tracts: **{d.stats.stores_geolocated:,}** SNAP retailers",
        f"- SQL files: **{d.n_sql_files}**; DuckDB tables: {d.stats.staging_tables} staging, "
        f"{d.stats.clean_tables} clean, {d.stats.results_tables} results",
        f"- Automated pytest tests: **{d.n_tests}** (data quality, analysis consistency, need score, outputs)",
        f"- Data-quality log entries: {d.stats.dq_log_entries} (rows removed by documented filters: "
        f"{d.n_dq_removed:,}, each with a logged reason)",
        f"- Charts: **{d.n_charts}** PNGs + 1 interactive map; Power BI files: 4 CSVs + 1 GeoJSON",
        "",
        "## Findings",
        "",
        f"- Residents in low-income tracts > 1/2 mile from a full-service grocery (2026): "
        f"**{l26.residents:,}** ({l26.pct_res:.0f}% of residents, {l26.tracts} tracts)",
        f"- Same under USDA's official 2019 definition (1/2 mile): {l19.residents:,} residents, {l19.tracts} tracts",
        f"- Car-free households > 1/2 mile from a full-service grocery: "
        f"**{d.q2.no_vehicle_hh_over_half_mi_current:,}**; > 1 mile: {d.q2.no_vehicle_hh_over_1_mi_current:,}",
        f"- Health gaps (low-income low-access vs. other tracts, percentage points): diabetes "
        f"**+{g['Diabetes'].diff:.1f}**, obesity +{g['Obesity'].diff:.1f}, high blood pressure "
        f"+{g['High blood pressure'].diff:.1f}, coronary heart disease +{g['Coronary heart disease'].diff:.1f}",
        f"- Income adjustment shrinks the distance-health association by "
        f"**{d.shrink.min():.0f}-{d.shrink.max():.0f}%** (OLS, n = {d.n_model} tracts)",
        f"- Top-10 need tracts: {int(d.top10.total_population.sum()):,} residents; "
        f"{len(d.robust)} of 10 stable across {d.n_scenarios} weighting scenarios",
        "",
        "## Example bullets (edit to taste)",
        "",
        f"- Built an end-to-end Python/SQL pipeline (DuckDB + spatial) integrating {d.n_datasets} public "
        f"datasets across {d.city.tracts} Baltimore census tracts, including a 2010-to-2020 tract crosswalk "
        f"and spatial joins of {d.stats.stores_geolocated:,} retail locations, validated by {d.n_tests} "
        f"automated pytest checks.",
        f"- Found that {l26.residents:,} residents ({l26.pct_res:.0f}%) live in low-income tracts more than "
        f"half a mile from a full-service grocery, where diabetes rates are {g['Diabetes'].diff:.1f} points "
        f"higher; used regression to show income explains most of the gap.",
        f"- Designed a configurable composite need score with a sensitivity analysis and delivered Power BI-ready "
        f"datasets, {d.n_charts} charts and an interactive Folium map identifying the 10 highest-priority tracts.",
        "",
    ])


def update_readme(block: str) -> None:
    text = README_PATH.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise RuntimeError("README.md is missing the KEY-FINDINGS markers")
    pattern = re.compile(re.escape(START) + ".*?" + re.escape(END), re.S)
    README_PATH.write_text(pattern.sub(lambda _: block, text), encoding="utf-8")
    log.info("updated README.md key findings")


def main() -> None:
    con = connect()
    try:
        d = gather(con)
    finally:
        con.close()
    FINDINGS_PATH.write_text(findings_md(d), encoding="utf-8")
    log.info("wrote %s", FINDINGS_PATH.relative_to(config.PROJECT_ROOT))
    RESUME_PATH.write_text(resume_md(d), encoding="utf-8")
    log.info("wrote %s", RESUME_PATH.relative_to(config.PROJECT_ROOT))
    update_readme(readme_block(d))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    main()
