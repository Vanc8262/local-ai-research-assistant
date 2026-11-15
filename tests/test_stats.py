from assistant.stats import mcnemar_exact, paired, wilson


def test_wilson_matches_the_review_numbers():
    low, high = wilson(6, 10)
    assert (round(low, 2), round(high, 2)) == (0.31, 0.83)
    low, high = wilson(10, 10)
    assert round(low, 2) == 0.72 and high == 1.0
    assert wilson(0, 0) is None


def test_mcnemar_exact_matches_the_review():
    assert round(mcnemar_exact(0, 4), 3) == 0.125    # 4 discordant pairs, all one way
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(3, 3) == 1.0


def test_paired_counts_only_items_both_modes_finished():
    rows = [{"question": "a", "run": 1, "mode": "local", "outcome": "ok", "gate": "fail"},
            {"question": "a", "run": 1, "mode": "hybrid", "outcome": "ok", "gate": "pass"},
            {"question": "b", "run": 1, "mode": "local", "outcome": "ok", "gate": "pass"},
            {"question": "b", "run": 1, "mode": "hybrid", "outcome": "ok", "gate": "pass"},
            {"question": "c", "run": 1, "mode": "local", "outcome": "no_sources"},
            {"question": "c", "run": 1, "mode": "hybrid", "outcome": "ok", "gate": "pass"}]
    assert paired(rows, "local", "hybrid") == {"pairs": 2, "both": 1, "neither": 0, "only_a": 0, "only_b": 1, "p": 1.0}
