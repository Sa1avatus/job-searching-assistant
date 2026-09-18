import pytest

from app.domain.annotation_dataset import (
    DEFAULT_RATIOS,
    EVAL_FOLDS,
    TEST,
    TRAIN,
    VALIDATION,
    InvalidSplit,
    LabelObservation,
    assign_folds,
    coverage_report,
    eval_vacancies,
    group_vacancies,
    validate_ratios,
)


def test_company_vacancies_and_compared_pairs_form_one_group() -> None:
    companies = {"v1": "Acme Inc.", "v2": "ACME inc", "v3": "Beta", "v4": "Gamma", "v5": ""}

    groups = group_vacancies(companies, pairs=[("v3", "v4")])

    assert groups["v1"] == groups["v2"]  # same company
    assert groups["v3"] == groups["v4"]  # judged head to head
    assert len({groups["v1"], groups["v3"], groups["v5"]}) == 3
    assert groups["v5"] == "v5"  # unknown company glues nothing


def test_group_ids_do_not_depend_on_input_order() -> None:
    a = group_vacancies({"v2": "X", "v1": "X", "v3": "Y"}, [("v3", "v1")])
    b = group_vacancies({"v3": "Y", "v1": "X", "v2": "X"}, [("v1", "v3")])

    assert a == b and set(a.values()) == {"v1"}


def test_folds_are_deterministic_and_group_consistent() -> None:
    companies = {f"v{i:03d}": f"Company {i % 40}" for i in range(400)}
    groups = group_vacancies(companies)

    first = assign_folds(groups, seed=7)
    second = assign_folds(dict(reversed(list(groups.items()))), seed=7)

    assert first == second
    for group in set(groups.values()):
        members = {first[v] for v, g in groups.items() if g == group}
        assert len(members) == 1  # no group straddles two folds -> no leakage
    assert {TRAIN, VALIDATION, TEST} == set(first.values())


def test_fold_proportions_roughly_follow_the_ratios() -> None:
    groups = {f"v{i:04d}": f"v{i:04d}" for i in range(3000)}

    folds = assign_folds(groups, seed=1, ratios=DEFAULT_RATIOS)
    share = {
        f: sum(1 for x in folds.values() if x == f) / len(folds) for f in (TRAIN, VALIDATION, TEST)
    }

    assert abs(share[TRAIN] - 0.7) < 0.04
    assert abs(share[VALIDATION] - 0.15) < 0.03 and abs(share[TEST] - 0.15) < 0.03


def test_frozen_eval_groups_never_enter_train_even_after_new_labels() -> None:
    old = {"v1": "Acme", "v2": "Beta", "v3": "Gamma"}
    folds_before = assign_folds(group_vacancies(old), seed=3)
    frozen = eval_vacancies(folds_before)

    # later: a new vacancy of a frozen company appears and a new pair links groups
    later = {**old, "v9": "Acme", "v10": "Delta"}
    groups = group_vacancies(later, pairs=[("v10", "v2")])
    folds_after = assign_folds(groups, seed=3, frozen_eval=frozen)

    for vacancy, fold in frozen.items():
        assert folds_after[vacancy] == fold
        for other, group in groups.items():
            if group == groups[vacancy]:
                assert folds_after[other] in EVAL_FOLDS  # neighbours are pulled out of train
    assert folds_after["v9"] == folds_before["v1"] if frozen.get("v1") else True


def test_test_outranks_validation_inside_a_merged_group() -> None:
    groups = group_vacancies({"a": "X", "b": "Y"}, pairs=[("a", "b")])

    folds = assign_folds(groups, frozen_eval={"a": VALIDATION, "b": TEST})

    assert folds == {"a": TEST, "b": TEST}


@pytest.mark.parametrize("ratios", [(0.5, 0.5), (1, 0, 0), (0.7, 0.2, 0.2), (-0.1, 0.6, 0.5)])
def test_invalid_ratios_are_rejected(ratios) -> None:
    with pytest.raises(InvalidSplit):
        validate_ratios(ratios)


def _obs(kind="pointwise", label="relevant", resume="r1", vacancies=("v1",), company="A", **kw):
    return LabelObservation(kind, label, resume, tuple(vacancies), company, **kw)


def test_empty_dataset_reports_no_labels_and_is_not_ready() -> None:
    report = coverage_report([], frozen=False)

    assert report.meaningful_labels == 0 and not report.ready
    assert "no labels yet" in report.warnings and report.labels_to_go == 200


def test_coverage_counts_classes_hard_cases_and_diversity() -> None:
    items = [
        _obs(label="relevant", vacancies=("v1",), company="A"),
        _obs(label="not_relevant", vacancies=("v2",), company="B", current_rank=3),
        _obs(label="maybe", vacancies=("v3",), company="C", sampling_reason="rank_disagreement"),
        _obs(kind="pair", label="decisive", vacancies=("v1", "v2"), company="A"),
    ]

    report = coverage_report(items, frozen=True)

    assert (report.pointwise, report.pairs, report.meaningful_labels) == (3, 1, 4)
    assert report.by_label == {"maybe": 1, "not_relevant": 1, "relevant": 1}
    assert (report.hard_negatives, report.model_disagreement) == (1, 1)
    assert report.vacancies == 3 and report.companies == 3


def test_a_full_healthy_dataset_becomes_ready() -> None:
    items = []
    for i in range(240):
        items.append(
            _obs(
                label=("relevant", "maybe", "not_relevant")[i % 3],
                resume=f"r{i % 2}",
                vacancies=(f"v{i}",),
                company=f"Company {i % 60}",
                current_rank=5 if i % 3 == 2 else None,
                sampling_reason="rank_disagreement" if i % 10 == 0 else None,
            )
        )
    items.append(_obs(kind="pair", label="decisive", vacancies=("v1", "v2"), company="Company 1"))

    report = coverage_report(items, frozen=True)

    assert report.ready and report.warnings == []


def test_warnings_name_single_resume_missing_freeze_and_imbalance() -> None:
    items = [_obs(label="relevant", vacancies=(f"v{i}",), company=f"C{i}") for i in range(30)]

    report = coverage_report(items, frozen=False)

    joined = " | ".join(report.warnings)
    assert "no frozen evaluation split" in joined
    assert "single resume" in joined
    assert "not_relevant" in joined and "both pointwise and pairwise" in joined
