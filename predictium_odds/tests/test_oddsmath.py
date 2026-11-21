import math

import pytest

from predictium_odds.oddsmath import (
    american_to_decimal,
    american_to_implied,
    decimal_to_american,
    devig_multiplicative,
    devig_power,
    devig_proportional,
    devig_shin,
    expected_value_pct,
    kelly_fraction,
    prob_to_american,
    shin_z,
)


def test_american_decimal_round_trip():
    assert american_to_decimal(100) == 2.0
    assert american_to_decimal(-110) == 1 + 100 / 110
    assert american_to_decimal(None) is None
    assert decimal_to_american(2.0) == 100
    assert decimal_to_american(1.5) == -200
    for odds in (-350, -110, 100, 145, 900):
        assert decimal_to_american(american_to_decimal(odds)) == odds


def test_prob_to_american():
    assert prob_to_american(0.5) == -100
    assert prob_to_american(0.25) == 300
    assert prob_to_american(0.57) == -133


def test_devig_two_way_sums_to_one():
    for fn_args in ((devig_multiplicative, (-130, 100)),
                    (devig_proportional, (0.565, 0.5)),
                    (devig_shin, (0.565, 0.5))):
        fn, args = fn_args
        a, b = fn(*args)
        assert math.isclose(a + b, 1.0)
        assert a > b


def test_shin_shrinks_longshot_more_than_proportional():
    # heavy favorite market with overround: Shin gives the longshot LESS
    p_fav, p_dog = american_to_implied(-900), american_to_implied(550)
    _, dog_prop = devig_proportional(p_fav, p_dog)
    _, dog_shin = devig_shin(p_fav, p_dog)
    assert dog_shin < dog_prop


def test_shin_no_overround_falls_back():
    assert devig_shin(0.6, 0.4) == devig_proportional(0.6, 0.4)


def test_power_devig_sums_to_one_and_shrinks_tail():
    board = {"fav": 1.5, "mid": 3.0, "long": 8.0, "longer": 15.0}  # 119% round
    fair = devig_power(board)
    assert math.isclose(sum(fair.values()), 1.0, abs_tol=1e-6)
    # longshots shrink harder than the favorite under power de-vig
    assert fair["long"] / (1 / 8.0) < fair["fav"] / (1 / 1.5)


def test_ev_and_kelly():
    assert expected_value_pct(0.5, 100) == 0.0
    assert expected_value_pct(0.55, 100) > 0
    assert kelly_fraction(0.5, -110) == 0.0
    assert kelly_fraction(0.55, 100) > 0


# --- 0.3.5: Shin solved numerically (2026-11-21) ----------------------------
# The old closed form for z went negative on lopsided markets and clamped to
# 0, returning the PROPORTIONAL de-vig; on near-even markets it overshot.

@pytest.mark.parametrize("p_a,p_b,want_a", [
    (0.70, 0.35, 0.6750),     # old: 0.6667 (= proportional)
    (0.90, 0.15, 0.8750),     # old: 0.8571 (= proportional)
    (0.55, 0.50, 0.5250),     # old: 0.5289 (overshoot)
])
def test_shin_matches_known_values(p_a, p_b, want_a):
    a, b = devig_shin(p_a, p_b)
    assert math.isclose(a, want_a, abs_tol=1e-9)
    assert math.isclose(a + b, 1.0, abs_tol=1e-12)
    assert 0.04 < shin_z([p_a, p_b]) < 0.06


def test_shin_two_way_identity_over_real_prices():
    """For two outcomes Shin removes half the overround from each side:
    q_a = p_a - (S - 1) / 2 (Clarke, Kovalchik and Ingram 2017)."""
    prices = [(-110, -110), (-120, 100), (-250, 210), (-900, 550),
              (-2000, 1100), (150, -170), (-105, -115), (300, -380)]
    for fa, fb in prices:
        pa, pb = american_to_implied(fa), american_to_implied(fb)
        a, b = devig_shin(pa, pb)
        over = pa + pb - 1
        assert math.isclose(a, pa - over / 2, abs_tol=1e-9), (fa, fb)
        assert math.isclose(b, pb - over / 2, abs_tol=1e-9), (fa, fb)


def test_shin_derates_the_longshot_on_every_lopsided_market():
    """The bug made these equal; Shin must give the longshot less."""
    for fa, fb in [(-150, 130), (-300, 250), (-900, 550), (-5000, 1500)]:
        pa, pb = american_to_implied(fa), american_to_implied(fb)
        _, dog_prop = devig_proportional(pa, pb)
        _, dog_shin = devig_shin(pa, pb)
        assert dog_shin < dog_prop - 1e-6, (fa, fb)


def test_shin_on_a_symmetric_market_is_even():
    a, b = devig_shin(0.5238, 0.5238)
    assert math.isclose(a, 0.5) and math.isclose(b, 0.5)


def test_shin_z_three_way_matches_an_independent_solver():
    """soccer_model.backtest.devig.devig_shin (an independent numeric Shin)
    gives [0.4742, 0.2776, 0.2482] for implied [0.50, 0.30, 0.27]."""
    from predictium_odds.oddsmath import _shin_probs
    p = [0.50, 0.30, 0.27]
    q = _shin_probs(p, shin_z(p))
    assert math.isclose(sum(q), 1.0, abs_tol=1e-12)
    assert [round(x, 4) for x in q] == [0.4742, 0.2776, 0.2482]


@pytest.mark.parametrize("bad", [[0.0, 1.1], [1.0, 0.2], [-0.1, 1.2],
                                 [float("nan"), 0.6], [0.6]])
def test_shin_z_rejects_impossible_inputs(bad):
    with pytest.raises(ValueError):
        shin_z(bad)


def test_shin_z_needs_an_overround():
    with pytest.raises(ValueError):
        shin_z([0.6, 0.4])
