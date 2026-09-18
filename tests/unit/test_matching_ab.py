"""A/B replay harness, boundary routing and hybrid fallback behaviour (stage 3B)."""

import random

import pytest

from app.config import Settings
from app.matching.ab.harness import percentile, replay_variant, run_ab
from app.matching.ab.hybrid import (
    PATH_BASELINE,
    PATH_BASELINE_FALLBACK,
    PATH_CHEAP,
    PATH_CHEAP_LLM_FAILED,
    PATH_HYBRID,
    hybrid_rank,
)
from app.matching.ab.routing import (
    MarginPoint,
    boundary_score,
    choose_margin,
    margin_curve,
    select_for_llm,
)
from app.matching.ltr.baseline import RankedItem, RankingGroup


def _group(gid: str, n: int, seed: int, noise: float = 0.0) -> RankingGroup:
    rng = random.Random(seed)
    items = []
    for i in range(n):
        gain = rng.choice([0, 0, 1, 2])
        items.append(
            RankedItem(
                vacancy_id=f"{gid}-{i:03d}",
                features={"signal": gain + rng.gauss(0, noise)},
                gain=gain,
                baseline_score=float(gain),
            )
        )
    return RankingGroup(gid, tuple(items))


def truth(group):  # an oracle standing in for the LLM path
    return [float(i.gain) for i in group.items]


def cheap_noisy(group):
    return [float(i.features["signal"] or 0.0) for i in group.items]


# -- harness -----------------------------------------------------------------


def test_percentile_interpolates_and_handles_empty() -> None:
    assert percentile([], 0.5) == 0.0
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile([5], 0.95) == 5


def test_replay_measures_quality_latency_and_memory() -> None:
    groups = [_group(f"g{i}", 30, i) for i in range(3)]

    result = replay_variant("oracle", truth, groups)

    assert result.ndcg_at_10 == pytest.approx(1.0)
    assert result.p95_ms >= result.p50_ms >= 0 and result.items_per_second > 0
    assert result.peak_memory_kib > 0 and result.errors == 0


def test_a_crashing_variant_is_counted_and_cannot_look_better_by_being_skipped() -> None:
    groups = [_group(f"g{i}", 30, i) for i in range(3)]

    def crashes(_group_):
        raise RuntimeError("boom")

    result = replay_variant("crash", crashes, groups)

    assert result.errors == 3
    assert result.ndcg_at_10 is not None and result.ndcg_at_10 < 1.0  # ranked in input order


def test_wrong_score_count_is_an_error() -> None:
    result = replay_variant("short", lambda g: [1.0], [_group("g", 20, 1)])

    assert result.errors == 1


def test_verdicts_accept_trade_off_and_insufficient_evidence() -> None:
    groups = [_group(f"g{i}", 40, i) for i in range(3)]
    variants = {
        "baseline": truth,
        "same": truth,
        "worse": lambda g: [random.Random(7).random() for _ in g.items],
    }

    report = run_ab(variants, groups, baseline="baseline")

    assert report.verdict["same"] == "accept" and report.ndcg_delta["same"] == 0
    assert report.verdict["worse"] == "trade_off" and report.ndcg_delta["worse"] < -0.01
    small = run_ab(variants, [_group("a", 10, 1), _group("b", 10, 2)], baseline="baseline")
    assert set(small.verdict.values()) == {"insufficient_evidence"}


def test_crashes_always_count_as_a_trade_off() -> None:
    groups = [_group(f"g{i}", 40, i) for i in range(3)]

    def flaky(group):
        if group.group_id == "g0":
            raise RuntimeError("boom")
        return truth(group)

    report = run_ab({"baseline": truth, "flaky": flaky}, groups, baseline="baseline")

    assert report.verdict["flaky"] == "trade_off"


def test_unknown_baseline_is_rejected() -> None:
    with pytest.raises(KeyError):
        run_ab({"a": truth}, [_group("g", 20, 1)], baseline="missing")


# -- routing -----------------------------------------------------------------


def test_boundary_score_is_the_midpoint_between_shortlist_and_the_rest() -> None:
    assert boundary_score([9, 8, 7, 3, 2, 1], top_k=3) == 5
    assert boundary_score([9, 8], top_k=3) is None


def test_only_items_near_the_boundary_are_routed_closest_first() -> None:
    ids = list("abcdef")
    scores = [9.0, 8.0, 5.2, 4.9, 1.0, 0.5]  # boundary between 5.2 and 4.9 = 5.05

    assert select_for_llm(ids, scores, top_k=3, margin=0.3) == ["c", "d"]
    assert select_for_llm(ids, scores, top_k=3, margin=0.0) == []
    assert select_for_llm(ids, scores, top_k=10, margin=5) == []  # all fit in the shortlist


def test_margin_curve_and_choice_come_from_data() -> None:
    groups = [_group(f"g{i}", 40, i, noise=0.35) for i in range(6)]

    curve = margin_curve(groups, cheap_noisy, [0.0, 0.25, 0.5, 1.0, 3.0], top_k=10)

    shares = [p.llm_share for p in curve]
    captures = [p.error_capture or 0 for p in curve]
    assert shares == sorted(shares) and captures == sorted(captures)  # monotone
    assert curve[0].llm_share == 0 and curve[-1].error_capture == 1.0
    chosen = choose_margin(curve, min_capture=0.9, max_share=1.0)
    assert chosen is not None and chosen.error_capture >= 0.9


def test_no_margin_within_budget_keeps_the_llm_in_charge() -> None:
    curve = [MarginPoint(0.5, 0.6, 0.85), MarginPoint(1.0, 0.9, 0.99)]

    assert choose_margin(curve, min_capture=0.8, max_share=0.3) is None
    assert choose_margin([MarginPoint(0.5, 0.1, None)]) is None  # nothing to capture


# -- hybrid ------------------------------------------------------------------


def _settings(**kw):
    return Settings(_env_file=None, **kw)


ON = {"matching_hybrid_routing_enabled": True, "matching_llm_margin": 0.6}


def test_flag_off_means_the_baseline_for_everything() -> None:
    group = _group("g", 40, 1)

    off = hybrid_rank(group, baseline=truth, cheap=cheap_noisy, settings=_settings(), top_k=10)
    no_margin = hybrid_rank(
        group,
        baseline=truth,
        cheap=cheap_noisy,
        settings=_settings(matching_hybrid_routing_enabled=True),
        top_k=10,
    )

    assert off.path == no_margin.path == PATH_BASELINE
    assert off.scores == truth(group) and off.llm_items == 40


def test_hybrid_sends_only_the_ambiguous_zone_to_the_llm() -> None:
    group = _group("g", 40, 2, noise=0.3)
    seen: list[int] = []

    def llm(sub):
        seen.append(len(sub.items))
        return truth(sub)

    result = hybrid_rank(group, baseline=llm, cheap=cheap_noisy, settings=_settings(**ON), top_k=10)

    assert result.path == PATH_HYBRID and seen == [result.llm_items]
    assert 2 <= result.llm_items <= int(40 * 0.3)  # the budget is a hard cap


def test_llm_only_reorders_within_the_slots_the_routed_items_already_held() -> None:
    group = _group("g", 40, 3, noise=0.3)
    cheap = cheap_noisy(group)

    result = hybrid_rank(
        group, baseline=truth, cheap=cheap_noisy, settings=_settings(**ON), top_k=10
    )

    assert result.path == PATH_HYBRID
    assert sorted(result.scores) == sorted(cheap)  # same multiset of scores: nothing invented
    changed = [i for i, (a, b) in enumerate(zip(result.scores, cheap, strict=True)) if a != b]
    assert len(changed) <= result.llm_items


def test_hybrid_is_at_least_as_good_as_cheap_when_the_llm_is_the_oracle() -> None:
    groups = [_group(f"g{i}", 40, i, noise=0.4) for i in range(6)]
    variants = {
        "cheap": cheap_noisy,
        "hybrid": lambda g: (
            hybrid_rank(
                g, baseline=truth, cheap=cheap_noisy, settings=_settings(**ON), top_k=10
            ).scores
        ),
    }

    report = run_ab(variants, groups, baseline="cheap")

    assert report.ndcg_delta["hybrid"] >= 0


def test_no_ambiguous_items_stays_on_the_cheap_path() -> None:
    group = _group("g", 40, 4)  # noise-free: a wide gap around the boundary is not guaranteed
    result = hybrid_rank(
        group,
        baseline=truth,
        cheap=lambda g: [float(i) for i in range(len(g.items))],
        settings=_settings(matching_hybrid_routing_enabled=True, matching_llm_margin=0.1),
        top_k=10,
    )

    assert result.path == PATH_CHEAP and result.llm_items == 0


def test_cheap_ranker_failure_falls_back_to_the_baseline() -> None:
    group = _group("g", 40, 5)

    def broken(_g):
        raise RuntimeError("model down")

    result = hybrid_rank(group, baseline=truth, cheap=broken, settings=_settings(**ON))

    assert result.path == PATH_BASELINE_FALLBACK and result.scores == truth(group)


def test_llm_failure_keeps_the_cheap_order() -> None:
    group = _group("g", 40, 6, noise=0.3)

    def llm_down(_g):
        raise RuntimeError("provider outage")

    result = hybrid_rank(group, baseline=llm_down, cheap=cheap_noisy, settings=_settings(**ON))

    assert result.path == PATH_CHEAP_LLM_FAILED
    assert result.scores == cheap_noisy(group)


def test_malformed_cheap_output_falls_back() -> None:
    group = _group("g", 40, 7)

    result = hybrid_rank(group, baseline=truth, cheap=lambda g: [1.0], settings=_settings(**ON))

    assert result.path == PATH_BASELINE_FALLBACK


def test_hybrid_flags_default_to_off() -> None:
    settings = _settings()

    assert settings.matching_hybrid_routing_enabled is False
    assert settings.matching_llm_margin is None
