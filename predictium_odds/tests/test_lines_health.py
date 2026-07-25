from predictium_odds.health import CoverageSpec, SourceReport, evaluate
from predictium_odds.lines import align_main_line, best_line, consensus
from predictium_odds.schema import Quote


def q(book, side, price, line=None, event="BUF@KC_2026-09-13",
      market="spread"):
    return Quote("nfl", event, book, market, side, price, line=line)


def test_best_line_picks_highest_payout_per_line():
    quotes = [q("bovada", "home", -110, -3.5), q("fanduel", "home", -105, -3.5),
              q("espn_dk", "home", -115, -3.0)]
    best = best_line(quotes)
    assert best[("BUF@KC_2026-09-13", "spread", "home", -3.5)].book == "fanduel"
    # a different line is a different key, never maxed together
    assert ("BUF@KC_2026-09-13", "spread", "home", -3.0) in best


def test_consensus_median_line_and_mean_price():
    quotes = [q("bovada", "home", -110, -3.5), q("fanduel", "home", -105, -3.5),
              q("espn_dk", "home", -120, -3.0)]
    c = consensus(quotes)[("BUF@KC_2026-09-13", "spread", "home")]
    assert c["line"] == -3.5
    assert c["n_books"] == 2  # only books at the median line price it
    assert -110 <= c["price_american"] <= -105


def test_align_main_line_exact_or_nothing():
    ladder = {9.5: "a", 10.5: "b", 11.5: "c"}
    assert align_main_line(ladder, 10.5) == "b"
    assert align_main_line(ladder, 12.5) is None
    assert align_main_line(ladder, 10.0) is None  # integer line: no equivalent
    assert align_main_line({}, 10.5) is None
    assert align_main_line(ladder, None) is None


def test_health_evaluate_flags_and_summary():
    reports = [SourceReport("bovada", "nfl", "*", 96, True),
               SourceReport("fanduel", "nfl", "*", 0, True),
               SourceReport("kalshi", "nfl", "wins", 170, True)]
    specs = [CoverageSpec("bovada", "nfl", "*", 30, required=True),
             CoverageSpec("fanduel", "nfl", "*", 30, required=True),
             CoverageSpec("kalshi", "nfl", "wins", 100)]
    res = evaluate(reports, specs)
    assert "bovada:*=96" in res.summary_line
    assert len(res.warnings) == 1 and "fanduel" in res.warnings[0]
    assert not res.all_required_down


def test_health_all_required_down():
    reports = [SourceReport("bovada", "nfl", "*", 0, False, "HTTP 500"),
               SourceReport("fanduel", "nfl", "*", 0, True)]
    specs = [CoverageSpec("bovada", "nfl", "*", 1, required=True),
             CoverageSpec("fanduel", "nfl", "*", 1, required=True)]
    res = evaluate(reports, specs)
    assert res.all_required_down
    assert len(res.warnings) == 2
    assert "HTTP 500" in res.warnings[0]


def test_health_unspecced_source_still_visible():
    res = evaluate([SourceReport("pinnacle", "nfl", "*", 40, True)], [])
    assert "pinnacle:*=40" in res.summary_line
