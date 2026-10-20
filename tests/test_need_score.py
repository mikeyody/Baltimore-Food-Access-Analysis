"""Tests for the Phase 4 need score: configuration, math, and output."""

import numpy as np
import pandas as pd
import pytest

from src import config
from src.need_score import COMPONENTS, score, standardize, validate_weights


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def test_default_weights_valid():
    validate_weights(config.NEED_SCORE_WEIGHTS)


@pytest.mark.parametrize("name", list(config.NEED_SCORE_SCENARIOS))
def test_scenario_weights_valid(name):
    validate_weights(config.NEED_SCORE_SCENARIOS[name])


def test_bad_weights_rejected():
    with pytest.raises(ValueError):
        validate_weights({**config.NEED_SCORE_WEIGHTS, "food_access": 0.9})   # sums > 1
    with pytest.raises(ValueError):
        validate_weights({"food_access": 1.0})                                # missing keys


# ---------------------------------------------------------------------------
# Math, on a tiny hand-checkable example
# ---------------------------------------------------------------------------
def test_zscore_standardization():
    z = standardize(pd.Series([1.0, 2.0, 3.0]), "zscore")
    assert z.mean() == pytest.approx(0)
    assert z.iloc[2] > z.iloc[1] > z.iloc[0]            # higher raw value = more need


def test_zscore_clipped():
    s = pd.Series([0.0] * 99 + [1000.0])
    assert standardize(s, "zscore").max() <= config.Z_CLIP


def test_minmax_standardization():
    m = standardize(pd.Series([10.0, 20.0, 30.0]), "minmax")
    assert list(m) == [0.0, 0.5, 1.0]


def test_worse_on_every_component_scores_higher():
    df = pd.DataFrame({
        "geoid": ["low", "mid", "high"],
        "food_access_raw": [0.2, 0.5, 1.0],
        "diabetes_pct": [10, 15, 20], "obesity_pct": [30, 35, 40],
        "high_bp_pct": [30, 40, 50], "chd_pct": [4, 6, 8],
        "poverty_raw": [5, 20, 40], "no_car_raw": [10, 30, 60], "seniors_raw": [10, 15, 20],
    })
    out = score(df, config.NEED_SCORE_WEIGHTS, "zscore").set_index("geoid")
    assert out.loc["high", "need_rank"] == 1
    assert out.loc["low", "need_rank"] == 3
    # Contributions add up to the score
    total = sum(out[f"{c}_contribution"] for c in COMPONENTS)
    assert np.allclose(total, out["need_score"])


# ---------------------------------------------------------------------------
# Pipeline output
# ---------------------------------------------------------------------------
def test_every_eligible_tract_scored_once(scalar):
    eligible = scalar("SELECT count(*) FROM results.need_components WHERE eligible")
    assert scalar("SELECT count(*) FROM results.need_scores") == eligible
    assert scalar("SELECT count(DISTINCT geoid) FROM results.need_scores") == eligible


def test_scored_plus_excluded_equals_all_tracts(scalar):
    assert scalar("SELECT count(*) FROM results.need_components") == config.EXPECTED_TRACT_COUNT


def test_score_0_100_range(scalar):
    lo, hi = scalar("SELECT min(need_score_0_100) FROM results.need_scores"), \
        scalar("SELECT max(need_score_0_100) FROM results.need_scores")
    assert lo == pytest.approx(0) and hi == pytest.approx(100)


def test_sensitivity_covers_all_scenarios(scalar):
    # +1 for the alternative-standardization run
    assert scalar("SELECT count(*) FROM results.need_sensitivity_summary") == \
        len(config.NEED_SCORE_SCENARIOS) + 1
