import math

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
