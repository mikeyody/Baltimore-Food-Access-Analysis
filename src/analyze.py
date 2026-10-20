"""Phase 3 analysis: run the SQL question files, add statistics, write the results.

    python -m src.analyze

Steps
  1. Run sql/05-09, which create results.* tables in DuckDB (SQL does the counting).
  2. Q3: Welch t-tests comparing health rates in low-access vs. other tracts.
  3. Q4: OLS regressions and partial correlations relating distance to the
     nearest full-service grocery store to health, controlling for income.
  4. Export every results table to data/processed/results/ and write
     reports/analysis_results.md. Every number in that report is read from
     the results tables; nothing is typed by hand.

Statistical notes (for interviews)
  * Unit of analysis = census tract (n ~ 195). Results describe tracts, not
    individuals (avoid the "ecological fallacy").
  * Standard errors are HC3 heteroskedasticity-robust, because the spread of
    health rates differs between richer and poorer tracts.
  * This is cross-sectional observational data: associations, not causes.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from scipy import stats

from src import config
from src.load import connect, run_sql_file

log = logging.getLogger("analyze")

ANALYSIS_FILES = [
    "05_q1_low_income_low_access.sql",
    "06_q2_grocery_distance.sql",
    "07_q3_health_by_access.sql",
    "08_q5_top_need_tracts.sql",
    "09_q4_model_data.sql",
]

OUTCOMES = {
    "diabetes_pct": "Diabetes",
    "obesity_pct": "Obesity",
    "high_bp_pct": "High blood pressure",
    "chd_pct": "Coronary heart disease",
}

# Regression models, from simplest to most controlled.
MODELS = {
    "M0 income only": "{y} ~ log_income",
    "M1 distance only": "{y} ~ dist_nearest_full_service_mi",
    "M2 distance + income": "{y} ~ dist_nearest_full_service_mi + log_income",
    "M3 distance + income + age 65+": "{y} ~ dist_nearest_full_service_mi + log_income + pct_65_plus",
}

RESULTS_DIR = config.PROCESSED_DIR / "results"
REPORT_PATH = config.REPORTS_DIR / "analysis_results.md"


# ---------------------------------------------------------------------------
# Q3: group comparisons
# ---------------------------------------------------------------------------
def health_group_tests(tracts: pd.DataFrame) -> pd.DataFrame:
    """Welch's t-test (doesn't assume equal variances) for each comparison x outcome."""
    comparisons = {
        "USDA 2019 low-income & low-access (1/2 mi)": tracts["lila_half_mi_2019"],
        "Nearest full-service store > 1/2 mi (2026)": tracts["beyond_half_mi_full_service"],
        "Low-income & nearest full-service store > 1/2 mi (2026)":
            tracts["low_income_2025"] & tracts["beyond_half_mi_full_service"],
    }
    rows = []
    df = tracts[tracts["has_health_data"]]
    for name, flag in comparisons.items():
        flag = flag.loc[df.index].astype(bool)
        for col, label in OUTCOMES.items():
            low, other = df.loc[flag, col], df.loc[~flag, col]
            t, p = stats.ttest_ind(low, other, equal_var=False)
            diff = low.mean() - other.mean()
            # 95% CI of the difference (Welch-Satterthwaite degrees of freedom)
            v1, v2 = low.var(ddof=1) / len(low), other.var(ddof=1) / len(other)
            dof = (v1 + v2) ** 2 / (v1 ** 2 / (len(low) - 1) + v2 ** 2 / (len(other) - 1))
            half = stats.t.ppf(0.975, dof) * np.sqrt(v1 + v2)
            rows.append({
                "comparison": name, "outcome": label,
                "low_access_tracts": len(low), "other_tracts": len(other),
                "low_access_mean": round(low.mean(), 2), "other_mean": round(other.mean(), 2),
                "difference_pts": round(diff, 2),
                "diff_ci_low": round(diff - half, 2), "diff_ci_high": round(diff + half, 2),
                "relative_difference_pct": round(100 * diff / other.mean(), 1),
                "t_stat": round(t, 2), "p_value": p,
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Q4: regression and partial correlation
# ---------------------------------------------------------------------------
def run_regressions(data: pd.DataFrame) -> pd.DataFrame:
    """Fit every model for every outcome; return one row per coefficient."""
    rows = []
    for y, label in OUTCOMES.items():
        for model_name, formula in MODELS.items():
            fit = smf.ols(formula.format(y=y), data=data).fit(cov_type="HC3")
            ci = fit.conf_int()
            for term in fit.params.index:
                rows.append({
                    "outcome": label, "outcome_col": y, "model": model_name, "term": term,
                    "coef": fit.params[term], "std_err": fit.bse[term],
                    "ci_low": ci.loc[term, 0], "ci_high": ci.loc[term, 1],
                    "p_value": fit.pvalues[term], "r_squared": fit.rsquared, "n": int(fit.nobs),
                })
    return pd.DataFrame(rows)


def partial_corr(data: pd.DataFrame, x: str, y: str, controls: list[str]) -> tuple[float, float]:
    """Correlation between x and y after removing the linear effect of the controls
    from both (regress each on the controls, correlate the residuals)."""
    if controls:
        rx = smf.ols(f"{x} ~ {' + '.join(controls)}", data=data).fit().resid
        ry = smf.ols(f"{y} ~ {' + '.join(controls)}", data=data).fit().resid
    else:
        rx, ry = data[x], data[y]
    r = float(np.corrcoef(rx, ry)[0, 1])
    # t-test for a partial correlation uses n - 2 - (number of controls) degrees of freedom
    dof = len(data) - 2 - len(controls)
    t = r * np.sqrt(dof / (1 - r ** 2))
    p = float(2 * stats.t.sf(abs(t), dof))
    return r, p


def partial_correlations(data: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for y, label in OUTCOMES.items():
        for name, controls in [("none (raw correlation)", []),
                               ("income", ["log_income"]),
                               ("income + age 65+", ["log_income", "pct_65_plus"])]:
            r, p = partial_corr(data, "dist_nearest_full_service_mi", y, controls)
            rows.append({"outcome": label, "controlling_for": name,
                         "r": round(r, 3), "p_value": p, "n": len(data)})
        r_inc, p_inc = partial_corr(data, "log_income", y, ["dist_nearest_full_service_mi"])
        rows.append({"outcome": label, "controlling_for": "INCOME vs outcome, controlling for distance",
                     "r": round(r_inc, 3), "p_value": p_inc, "n": len(data)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def fmt_p(p: float) -> str:
    """Format a p-value for a table cell."""
    return "< 0.001" if p < 0.001 else f"{p:.3f}"


def p_text(p: float) -> str:
    """Format a p-value for a sentence: 'p < 0.001' or 'p = 0.012'."""
    return "p < 0.001" if p < 0.001 else f"p = {p:.3f}"


def evidence(p: float) -> str:
    """Conventional wording for strength of evidence against 'no association'."""
    if p < 0.05:
        return "significant"
    if p < 0.10:
        return "borderline"
    return "not significant"


def md_table(df: pd.DataFrame) -> str:
    """Render a DataFrame as a GitHub markdown table (no extra dependency)."""
    def cell(v):
        if isinstance(v, float):
            return f"{v:,.2f}" if abs(v) < 1000 else f"{v:,.0f}"
        if isinstance(v, (int, np.integer)):
            return f"{v:,}"
        return str(v)
    head = "| " + " | ".join(df.columns) + " |"
    sep = "|" + "|".join("---" for _ in df.columns) + "|"
    body = ["| " + " | ".join(cell(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join([head, sep, *body])


def explain_regression(reg: pd.DataFrame, pcor: pd.DataFrame) -> list[str]:
    """Plain-English interpretation, generated from the fitted numbers."""
    lines = []
    for label in OUTCOMES.values():
        r = reg[reg.outcome == label]
        dist = r.term == "dist_nearest_full_service_mi"
        d1 = r[(r.model == "M1 distance only") & dist].iloc[0]
        d2 = r[(r.model == "M2 distance + income") & dist].iloc[0]
        d3 = r[(r.model == "M3 distance + income + age 65+") & dist].iloc[0]
        inc = r[(r.model == "M2 distance + income") & (r.term == "log_income")].iloc[0]
        r2_income = r[r.model == "M0 income only"].r_squared.iloc[0]
        direction = "higher" if d2.coef > 0 else "lower"
        income_10pct = inc.coef * np.log(1.10)
        pc = pcor[(pcor.outcome == label) & (pcor.controlling_for == "income")].iloc[0]
        verdict = {
            "significant": f"a statistically significant association: tracts farther from a store "
                           f"have {direction} rates than tracts with similar incomes.",
            "borderline": "borderline evidence (just above or near the usual p < 0.05 cut-off), so "
                          "the association is suggestive rather than clear.",
            "not significant": "not statistically distinguishable from zero: once income is known, "
                               "distance adds little.",
        }[evidence(d2.p_value)]
        lines.append(
            f"- **{label}.** On its own, each extra mile between a tract's population center and "
            f"the nearest full-service grocery store goes with a {d1.coef:+.2f}-point difference in "
            f"the {label.lower()} rate ({p_text(d1.p_value)}). After accounting for income, the "
            f"association shrinks to {d2.coef:+.2f} points per mile (95% CI {d2.ci_low:+.2f} to "
            f"{d2.ci_high:+.2f}, {p_text(d2.p_value)}): {verdict}"
            f" Income alone explains {r2_income:.0%} of the variation between tracts; adding distance "
            f"brings that to {d2.r_squared:.0%}. A tract with 10% higher median income has a "
            f"{income_10pct:+.2f}-point difference in the {label.lower()} rate at the same distance. "
            f"Also controlling for the share of residents 65+, the distance association is "
            f"{d3.coef:+.2f} points per mile ({p_text(d3.p_value)}, {evidence(d3.p_value)})."
            f" Partial correlation of distance with {label.lower()}, controlling for income: "
            f"r = {pc.r:+.2f} ({p_text(pc.p_value)})."
        )
    return lines


def overall_takeaway(reg: pd.DataFrame) -> str:
    """One-paragraph summary across all outcomes, built from the fitted results."""
    dist = reg[reg.term == "dist_nearest_full_service_mi"]
    m1 = dist[dist.model == "M1 distance only"].set_index("outcome")
    m2 = dist[dist.model == "M2 distance + income"].set_index("outcome")
    m3 = dist[dist.model == "M3 distance + income + age 65+"].set_index("outcome")
    shrink = (1 - m2.coef / m1.coef) * 100
    sig2 = [o for o in m2.index if m2.loc[o, "p_value"] < 0.05]
    sig3 = [o for o in m3.index if m3.loc[o, "p_value"] < 0.05]
    def names(xs: list[str]) -> str:
        xs = [x.lower() for x in xs]
        if not xs:
            return "none of the outcomes"
        return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]
    return (
        f"**Overall:** distance to a full-service grocery store is associated with worse health on "
        f"its own, but accounting for income shrinks those associations by "
        f"{shrink.min():.0f}-{shrink.max():.0f}%. Income is by far the stronger predictor. After "
        f"income, the distance association remains statistically significant (p < 0.05) for "
        f"{names(sig2)}; after also accounting for age, it is significant for {names(sig3)}. "
        + ("Food access is therefore a modest, secondary correlate of health in Baltimore, layered "
           "on top of a much larger income gradient."
           if len(sig3) < len(m3) else
           "Distance stays associated with every outcome even after income and age, though income "
           "remains the stronger predictor.")
    )


def write_report(con, tests: pd.DataFrame, reg: pd.DataFrame, pcor: pd.DataFrame,
                 n_model: int) -> None:
    q = lambda sql: con.execute(sql).df()  # noqa: E731
    # Single-row tables: read as a namespace so integers stay integers
    # (a pandas row would silently turn them into floats like 13.0).
    one = lambda sql: SimpleNamespace(**q(sql).to_dict("records")[0])  # noqa: E731
    city = one("SELECT * FROM results.q1_city_totals")
    q1 = q("SELECT definition, tracts_flagged, pct_of_tracts, residents, pct_of_residents, "
           "households_no_vehicle FROM results.q1_low_income_low_access ORDER BY sort")
    q2s = one("SELECT * FROM results.q2_summary")
    q2b = q("SELECT * FROM results.q2_distance_bands")
    q3 = q("SELECT comparison, tract_group, tracts, residents, diabetes_mean, obesity_mean, "
           "high_bp_mean, chd_mean FROM results.q3_health_by_access ORDER BY sort, tract_group")
    q5 = q("SELECT need_rank, geoid, tract_name, total_population, dist_nearest_full_service_mi, "
           "pct_poverty, pct_households_no_vehicle, diabetes_pct, combined_need "
           "FROM results.q5_top_need_tracts ORDER BY need_rank")
    excluded = q("SELECT * FROM results.q5_excluded_tracts")

    t = tests.copy()
    t["p_value"] = t["p_value"].map(fmt_p)
    t = t[["comparison", "outcome", "low_access_mean", "other_mean", "difference_pts",
           "diff_ci_low", "diff_ci_high", "p_value"]]

    m2 = reg[(reg.model == "M2 distance + income")].copy()
    m2 = m2[m2.term != "Intercept"][["outcome", "term", "coef", "ci_low", "ci_high", "p_value",
                                     "r_squared", "n"]]
    m2["p_value"] = m2["p_value"].map(fmt_p)
    m3 = reg[(reg.model == "M3 distance + income + age 65+")
             & (reg.term == "dist_nearest_full_service_mi")][["outcome", "coef", "ci_low", "ci_high",
                                                                "p_value", "r_squared"]].copy()
    m3["p_value"] = m3["p_value"].map(fmt_p)
    pc = pcor.copy()
    pc["p_value"] = pc["p_value"].map(fmt_p)

    lines = [
        "# Analysis Results (Phase 3)",
        "",
        "_Generated by `python -m src.analyze` from the `results` tables in DuckDB. "
        "Do not edit by hand; rerun the pipeline instead._",
        "",
        f"Baltimore City: {city.tracts:,} census tracts, {city.residents:,} residents and "
        f"{city.households:,} households (ACS 2020-2024), of which {city.households_no_vehicle:,} "
        f"have no vehicle. {city.full_service_stores_in_city:,} full-service grocery stores and "
        f"{city.snap_stores_in_city:,} SNAP-authorized stores of all kinds are inside the city.",
        "",
        "## Q1. How many tracts and residents are both low-income and low-access?",
        "",
        "SQL: `sql/05_q1_low_income_low_access.sql`",
        "",
        md_table(q1),
        "",
        "The USDA 2019 definition counts only supermarkets; the 2026 update uses the current "
        "supermarket list with USDA's current low-income flag. The USDA 2025 SRAM definition counts "
        "any SNAP store (including corner and dollar stores), which is why it flags far fewer tracts.",
        "",
        "## Q2. Distance to the nearest full-service grocery store",
        "",
        "SQL: `sql/06_q2_grocery_distance.sql` (per-tract table: `results.q2_tract_distances`)",
        "",
        f"- Median tract: **{q2s.median_distance_mi} miles**; farthest tract: {q2s.max_distance_mi} "
        f"miles; population-weighted mean: {q2s.pop_weighted_mean_distance_mi} miles.",
        f"- **{q2s.no_vehicle_hh_over_1_mi_current:,} households without a car** live in the "
        f"{q2s.tracts_over_1_mi} tracts whose population center is more than 1 mile from a "
        f"full-service store (2026 stores). {q2s.no_vehicle_hh_over_half_mi_current:,} car-free "
        f"households live in tracts more than 1/2 mile away.",
        f"- USDA's finer-grained 2019 estimate (1/2-km population grid, 2019 stores) puts "
        f"**{q2s.no_vehicle_hh_over_1_mi_usda_2019:,}** car-free households more than 1 mile from a "
        f"supermarket. The two methods differ because USDA measures each grid cell, while our "
        f"tract method places everyone at the tract's population center.",
        "",
        md_table(q2b),
        "",
        "## Q3. Health rates in low-access vs. other tracts",
        "",
        "SQL: `sql/07_q3_health_by_access.sql`; tests: Welch's t-test (unequal variances). "
        "Rates are CDC PLACES crude prevalence among adults (%).",
        "",
        md_table(q3),
        "",
        "Differences (low-access minus other, percentage points) with 95% confidence intervals:",
        "",
        md_table(t),
        "",
        "## Q4. Does food access relate to health once income is accounted for?",
        "",
        f"Method: ordinary least squares regression across {n_model} tracts with complete data, "
        "HC3 robust standard errors. Outcome = tract health rate (%); predictors = distance (miles) "
        "from the population-weighted center to the nearest full-service grocery store, and the log "
        "of median household income. Partial correlations remove the linear effect of income from "
        "both distance and the health rate, then correlate what is left. (Partial-correlation "
        "p-values use classical standard errors and regression p-values use robust ones, so the two "
        "can differ slightly near the 0.05 line.) Census caps median income at $250,000+, which "
        "affects only the richest few tracts.",
        "",
        "### In plain English",
        "",
        overall_takeaway(reg),
        "",
        *explain_regression(reg, pcor),
        "",
        "**This is correlation, not causation.** Tracts far from supermarkets differ in many ways "
        "(age, race, housing, transit, smoking, healthcare access) that this model doesn't capture, "
        "and CDC PLACES health estimates are themselves partly modeled from tract demographics, which "
        "builds in a link to income. A significant association would mean distance and health move "
        "together after accounting for income, not that opening a store would change health rates.",
        "",
        "### Model M2: outcome ~ distance + log(income)",
        "",
        md_table(m2),
        "",
        "### Robustness: adding share of residents 65+ (M3), distance coefficient",
        "",
        md_table(m3),
        "",
        "### Partial correlations",
        "",
        md_table(pc),
        "",
        "## Q5. Top 10 tracts by combined need (equal-weight first pass)",
        "",
        "SQL: `sql/08_q5_top_need_tracts.sql`. Combined need = average of the tract's percentile "
        "ranks (0-1) for distance to a full-service store, health burden (diabetes, obesity, high "
        "blood pressure), poverty rate and share of households without a car. Phase 4 replaces this "
        "with a configurable weighted score and a sensitivity check.",
        "",
        md_table(q5),
        "",
        f"Excluded from ranking ({len(excluded)} tracts):",
        "",
        md_table(excluded) if len(excluded) else "_none_",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    log.info("wrote %s", REPORT_PATH.relative_to(config.PROJECT_ROOT))


def main() -> None:
    con = connect()
    try:
        for name in ANALYSIS_FILES:
            run_sql_file(con, config.SQL_DIR / name)

        tracts = con.execute("SELECT * FROM analysis.tract_analysis").df()
        model_data = con.execute("SELECT * FROM results.q4_model_data").df()

        tests = health_group_tests(tracts)
        reg = run_regressions(model_data)
        pcor = partial_correlations(model_data)

        # Store the Python results back in DuckDB next to the SQL results.
        for name, df in {"q3_tests": tests, "q4_regression": reg, "q4_partial_corr": pcor}.items():
            con.register("tmp_df", df)
            con.execute(f"CREATE OR REPLACE TABLE results.{name} AS SELECT * FROM tmp_df")
            con.unregister("tmp_df")

        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        tables = [r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'results' "
            "ORDER BY table_name").fetchall()]
        for table in tables:
            dest = RESULTS_DIR / f"{table}.csv"
            con.execute(f"COPY results.{table} TO '{dest.as_posix()}' (HEADER)")
        log.info("exported %d results tables to %s", len(tables),
                 RESULTS_DIR.relative_to(config.PROJECT_ROOT))

        write_report(con, tests, reg, pcor, n_model=len(model_data))
    finally:
        con.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    main()
