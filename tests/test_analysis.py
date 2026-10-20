"""Consistency checks on the Phase 3 analysis results.

These don't test what the answers *are*; they test that the answers are
internally consistent (parts add up to wholes, subsets are smaller than sets),
which catches broken joins and double counting.
"""

import pytest

from src import config


def test_results_tables_exist(con):
    tables = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'results'").fetchall()}
    expected = {"q1_low_income_low_access", "q1_city_totals", "q2_tract_distances",
                "q2_distance_bands", "q2_summary", "q3_health_by_access", "q3_tests",
                "q4_model_data", "q4_regression", "q4_partial_corr", "q5_top_need_tracts"}
    missing = expected - tables
    assert not missing, f"missing results tables: {missing} - run `python -m src.analyze`"


def test_q1_flagged_never_exceeds_city(scalar):
    assert scalar("""
        SELECT count(*) FROM results.q1_low_income_low_access
        WHERE tracts_flagged > tracts_total
           OR residents > (SELECT residents FROM results.q1_city_totals)
    """) == 0


def test_q1_one_mile_is_subset_of_half_mile(con):
    """Anyone more than 1 mile away is also more than 1/2 mile away."""
    rows = dict(con.execute(
        "SELECT definition, tracts_flagged FROM results.q1_low_income_low_access").fetchall())
    assert (rows["Updated 2026: low-income & nearest full-service store > 1 mile"]
            <= rows["Updated 2026: low-income & nearest full-service store > 1/2 mile"])
    assert (rows["USDA 2019 (LRAM): low-income & low-access, 1 mile"]
            <= rows["USDA 2019 (LRAM): low-income & low-access, 1/2 mile"])


def test_q2_distance_bands_add_up_to_city(con):
    tracts, residents = con.execute(
        "SELECT sum(tracts), sum(residents) FROM results.q2_distance_bands").fetchone()
    city_tracts, city_res = con.execute(
        "SELECT tracts, residents FROM results.q1_city_totals").fetchone()
    assert tracts == city_tracts == config.EXPECTED_TRACT_COUNT
    assert residents == city_res


def test_q2_car_free_far_is_subset(scalar):
    assert scalar("""
        SELECT count(*) FROM results.q2_summary
        WHERE no_vehicle_hh_over_1_mi_current > no_vehicle_hh_over_half_mi_current
           OR no_vehicle_hh_over_half_mi_current > no_vehicle_hh_citywide
    """) == 0


def test_q3_groups_cover_all_tracts_with_health_data(con):
    n_health = con.execute(
        "SELECT count(*) FROM analysis.tract_analysis WHERE has_health_data").fetchone()[0]
    for comparison, total in con.execute(
            "SELECT comparison, sum(tracts) FROM results.q3_health_by_access GROUP BY 1").fetchall():
        assert total == n_health, comparison


def test_q4_model_sample_size(scalar):
    """Model uses every tract with health data and a published income."""
    expected = scalar("""SELECT count(*) FROM analysis.tract_analysis
                         WHERE has_health_data AND median_household_income IS NOT NULL""")
    assert scalar("SELECT max(n) FROM results.q4_regression") == expected
    assert scalar("SELECT min(n) FROM results.q4_regression") == expected


@pytest.mark.parametrize("col", ["p_value"])
def test_q4_p_values_valid(scalar, col):
    assert scalar(f"SELECT count(*) FROM results.q4_regression WHERE {col} NOT BETWEEN 0 AND 1") == 0


def test_q5_top10_is_ten_unique_tracts(con):
    n, distinct = con.execute(
        "SELECT count(*), count(DISTINCT geoid) FROM results.q5_top_need_tracts").fetchone()
    assert n == distinct == 10
