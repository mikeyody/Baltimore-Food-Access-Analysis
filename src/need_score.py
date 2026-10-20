"""Phase 4: composite neighborhood need score and weighting sensitivity check.

    python -m src.need_score

How the score works (for every eligible tract)
  1. Five components, each oriented so higher = more need:
       food_access  miles to the nearest full-service grocery store
       health       average of the standardized diabetes, obesity,
                    high blood pressure and coronary heart disease rates
       poverty      % of residents below the poverty line
       no_car       % of households without a vehicle
       seniors      % of residents aged 65+
  2. Each component is standardized across Baltimore tracts (z-score by
     default, clipped to +/-3; or min-max 0-1), so components measured in
     miles and percents become comparable.
  3. need_score = sum(weight x standardized component), weights from
     config.NEED_SCORE_WEIGHTS. The score is relative: 0 = an average
     Baltimore tract (for z-scores), positive = above-average need.
  4. need_score_0_100 rescales it so the lowest-need tract = 0 and the
     highest = 100, which is easier to read on a map.

The sensitivity check re-runs steps 2-3 with the alternative weightings in
config.NEED_SCORE_SCENARIOS (and with min-max standardization) and reports
how much the top 10 changes.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from src import config
from src.load import connect, run_sql_file

log = logging.getLogger("need_score")

COMPONENTS = ["food_access", "health", "poverty", "no_car", "seniors"]
HEALTH_MEASURES = ["diabetes_pct", "obesity_pct", "high_bp_pct", "chd_pct"]
TOP_N = 10
REPORT_PATH = config.REPORTS_DIR / "need_score.md"


def validate_weights(weights: dict[str, float]) -> None:
    """Fail loudly on a typo or weights that don't add to 1."""
    if set(weights) != set(COMPONENTS):
        raise ValueError(f"weights must have exactly these keys: {COMPONENTS}, got {list(weights)}")
    if not np.isclose(sum(weights.values()), 1.0):
        raise ValueError(f"weights must sum to 1, got {sum(weights.values()):.3f}")
    if any(w < 0 for w in weights.values()):
        raise ValueError("weights can't be negative")


def standardize(s: pd.Series, method: str) -> pd.Series:
    """Put a component on a common scale. Higher stays higher."""
    if method == "zscore":
        z = (s - s.mean()) / s.std(ddof=0)
        return z.clip(-config.Z_CLIP, config.Z_CLIP)
    if method == "minmax":
        return (s - s.min()) / (s.max() - s.min())
    raise ValueError(f"unknown standardization method: {method!r}")


def standardized_components(df: pd.DataFrame, method: str) -> pd.DataFrame:
    """Return one standardized column per component for the eligible tracts."""
    out = pd.DataFrame(index=df.index)
    out["food_access"] = standardize(df["food_access_raw"], method)
    # Health: standardize each disease rate, average them, then re-standardize
    # so the health component is on the same scale as the others.
    health = pd.concat([standardize(df[m], "zscore") for m in HEALTH_MEASURES], axis=1).mean(axis=1)
    out["health"] = standardize(health, method)
    out["poverty"] = standardize(df["poverty_raw"], method)
    out["no_car"] = standardize(df["no_car_raw"], method)
    out["seniors"] = standardize(df["seniors_raw"], method)
    return out


def score(df: pd.DataFrame, weights: dict[str, float], method: str) -> pd.DataFrame:
    """Compute the need score, 0-100 version, rank and per-component contributions."""
    validate_weights(weights)
    comps = standardized_components(df, method)
    result = df[["geoid"]].copy()
    for c in COMPONENTS:
        result[f"{c}_std"] = comps[c]
        result[f"{c}_contribution"] = comps[c] * weights[c]
    result["need_score"] = sum(result[f"{c}_contribution"] for c in COMPONENTS)
    lo, hi = result["need_score"].min(), result["need_score"].max()
    result["need_score_0_100"] = 100 * (result["need_score"] - lo) / (hi - lo)
    result["need_rank"] = result["need_score"].rank(ascending=False, method="min").astype(int)
    return result


def sensitivity(df: pd.DataFrame, base: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Re-score under each alternative and compare against the default ranking."""
    scenarios = {f"{name}": (w, config.NEED_SCORE_STANDARDIZATION)
                 for name, w in config.NEED_SCORE_SCENARIOS.items()}
    other_method = "minmax" if config.NEED_SCORE_STANDARDIZATION == "zscore" else "zscore"
    scenarios[f"Default weights, {other_method} scaling"] = (config.NEED_SCORE_WEIGHTS, other_method)

    base_top = set(base.nsmallest(TOP_N, "need_rank")["geoid"])
    ranks = base[["geoid", "need_rank"]].rename(columns={"need_rank": "Default"})
    summary = []
    for name, (weights, method) in scenarios.items():
        alt = score(df, weights, method)
        ranks = ranks.merge(alt[["geoid", "need_rank"]].rename(columns={"need_rank": name}), on="geoid")
        alt_top = set(alt.nsmallest(TOP_N, "need_rank")["geoid"])
        rho = spearmanr(base["need_rank"], alt.set_index("geoid").loc[base["geoid"], "need_rank"])[0]
        summary.append({
            "scenario": name,
            "standardization": method,
            "weights": ", ".join(f"{k} {v:.2f}" for k, v in weights.items()),
            "top10_overlap_with_default": len(base_top & alt_top),
            "entered_top10": ", ".join(sorted(alt_top - base_top)) or "-",
            "left_top10": ", ".join(sorted(base_top - alt_top)) or "-",
            "spearman_rank_correlation": round(float(rho), 3),
        })
    # How stable is each default top-10 tract? Best / worst rank across scenarios.
    scen_cols = [c for c in ranks.columns if c != "geoid"]
    ranks["best_rank"] = ranks[scen_cols].min(axis=1)
    ranks["worst_rank"] = ranks[scen_cols].max(axis=1)
    ranks["in_top10_count"] = (ranks[scen_cols] <= TOP_N).sum(axis=1)
    ranks["scenarios_total"] = len(scen_cols)
    return pd.DataFrame(summary), ranks.sort_values("Default")


def md_table(df: pd.DataFrame) -> str:
    from src.analyze import md_table as _md
    return _md(df)


def write_report(con, scored: pd.DataFrame, summary: pd.DataFrame, ranks: pd.DataFrame,
                 excluded: pd.DataFrame) -> None:
    top = con.execute("""
        SELECT need_rank, geoid, neighborhood, total_population, round(need_score, 2) AS need_score,
               round(need_score_0_100, 1) AS score_0_100,
               round(food_access_raw, 2) AS miles_to_store, round(poverty_raw, 1) AS pct_poverty,
               round(no_car_raw, 1) AS pct_no_car, round(seniors_raw, 1) AS pct_65_plus,
               diabetes_pct, high_bp_pct, top_driver
        FROM results.need_scores WHERE need_rank <= 10 ORDER BY need_rank
    """).df()
    top_pop, top_no_car = con.execute("""
        SELECT sum(total_population), sum(households_no_vehicle)
        FROM results.need_scores WHERE need_rank <= 10
    """).fetchone()
    stable = ranks[ranks["Default"] <= TOP_N][["geoid", "Default", "best_rank", "worst_rank",
                                                "in_top10_count", "scenarios_total"]]
    always = int((stable["in_top10_count"] == stable["scenarios_total"]).sum())
    weights = pd.DataFrame([{"component": k, "weight": v} for k, v in config.NEED_SCORE_WEIGHTS.items()])

    lines = [
        "# Neighborhood Need Score (Phase 4)",
        "",
        "_Generated by `python -m src.need_score`. Weights live in `src/config.py` "
        "(`NEED_SCORE_WEIGHTS`); change them there and rerun._",
        "",
        "## Method",
        "",
        "Each component is oriented so that higher = more need, then standardized across "
        f"Baltimore's scored tracts ({config.NEED_SCORE_STANDARDIZATION}"
        + (f", clipped to +/-{config.Z_CLIP:g}" if config.NEED_SCORE_STANDARDIZATION == "zscore" else "")
        + "). The need score is the weighted sum. It is **relative**: it ranks tracts against each "
        "other within Baltimore, not against a national standard.",
        "",
        "| Component | Measure (source) |",
        "|---|---|",
        "| food_access | Miles from population-weighted centroid to nearest full-service grocery store (2026 SNAP list) |",
        "| health | Average standardized rate of diabetes, obesity, high blood pressure, coronary heart disease (CDC PLACES 2025) |",
        "| poverty | % of residents below the poverty line (ACS 2020-24) |",
        "| no_car | % of households without a vehicle (ACS 2020-24) |",
        "| seniors | % of residents aged 65+ (ACS 2020-24) |",
        "",
        "Default weights (rationale in `src/config.py`): access is the core question (largest "
        "weight); diet-related disease is what better access aims to improve; poverty limits what "
        "people can buy; no car multiplies the distance problem; the 65+ share is a weaker, indirect "
        "mobility signal (smallest weight).",
        "",
        md_table(weights),
        "",
        f"{len(scored)} tracts were scored; {len(excluded)} could not be scored:",
        "",
        md_table(excluded) if len(excluded) else "_none_",
        "",
        "## Top 10 tracts by need",
        "",
        f"These 10 tracts are home to {top_pop:,} residents and {top_no_car:,} households without "
        "a car. `top_driver` is the component contributing most to each tract's score.",
        "",
        md_table(top),
        "",
        "## Sensitivity check",
        "",
        "How much does the top 10 change under other reasonable weightings?",
        "",
        md_table(summary),
        "",
        f"**{always} of the default top 10 tracts stay in the top 10 under every scenario.** "
        "Rank range of each default top-10 tract across all scenarios (including the default):",
        "",
        md_table(stable),
        "",
        "Spearman rank correlation compares the full ranking of all scored tracts (1 = identical "
        "order). A high correlation with some top-10 turnover means the overall picture is robust, "
        "while tracts near the cut-off swap places depending on priorities.",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    log.info("wrote %s", REPORT_PATH.relative_to(config.PROJECT_ROOT))


def main() -> None:
    validate_weights(config.NEED_SCORE_WEIGHTS)
    for name, w in config.NEED_SCORE_SCENARIOS.items():
        validate_weights(w)

    con = connect()
    try:
        run_sql_file(con, config.SQL_DIR / "10_need_components.sql")
        comps = con.execute("SELECT * FROM results.need_components").df()
        eligible = comps[comps["eligible"]].reset_index(drop=True)
        excluded = comps.loc[~comps["eligible"], ["geoid", "tract_name", "total_population",
                                                 "ineligible_reason"]]
        log.info("scoring %d tracts (%d excluded)", len(eligible), len(excluded))

        base = score(eligible, config.NEED_SCORE_WEIGHTS, config.NEED_SCORE_STANDARDIZATION)
        contrib_cols = [f"{c}_contribution" for c in COMPONENTS]
        base["top_driver"] = base[contrib_cols].idxmax(axis=1).str.replace("_contribution", "")
        scored = eligible.merge(base, on="geoid")
        summary, ranks = sensitivity(eligible, base)

        for name, df in {"need_scores": scored, "need_sensitivity_summary": summary,
                         "need_sensitivity_ranks": ranks}.items():
            con.register("tmp_df", df)
            con.execute(f"CREATE OR REPLACE TABLE results.{name} AS SELECT * FROM tmp_df")
            con.unregister("tmp_df")
            dest = config.PROCESSED_DIR / "results" / f"{name}.csv"
            dest.parent.mkdir(parents=True, exist_ok=True)
            con.execute(f"COPY results.{name} TO '{dest.as_posix()}' (HEADER)")

        write_report(con, scored, summary, ranks, excluded)
    finally:
        con.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    main()
