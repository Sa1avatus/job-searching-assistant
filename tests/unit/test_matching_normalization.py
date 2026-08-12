from app.matching.normalization import SkillNormalizer


def test_known_alias_resolves_to_canonical() -> None:
    normalizer = SkillNormalizer()

    result = normalizer.normalize("Postgres")

    assert result.canonical == "postgresql"
    assert result.original == "Postgres"
    assert result.confidence == 1.0


def test_known_alias_case_insensitive() -> None:
    normalizer = SkillNormalizer()

    assert normalizer.normalize("K8S").canonical == "kubernetes"
    assert normalizer.normalize("k8s").canonical == "kubernetes"
    assert normalizer.normalize("Torch").canonical == "pytorch"
    assert normalizer.normalize("TF").canonical == "tensorflow"


def test_unknown_skill_preserved_as_canonical() -> None:
    normalizer = SkillNormalizer()

    result = normalizer.normalize("GraphQL")

    assert result.canonical == "GraphQL"
    assert result.original == "GraphQL"
    assert result.confidence == 0.8


def test_normalize_many_returns_tuple() -> None:
    normalizer = SkillNormalizer()

    results = normalizer.normalize_many(("Postgres", "K8s", "GraphQL"))

    assert len(results) == 3
    assert results[0].canonical == "postgresql"
    assert results[1].canonical == "kubernetes"
    assert results[2].canonical == "GraphQL"


def test_custom_alias_overrides_default() -> None:
    normalizer = SkillNormalizer(aliases={"pg": "cockroachdb"})

    result = normalizer.normalize("pg")

    assert result.canonical == "cockroachdb"


def test_whitespace_trimmed_before_lookup() -> None:
    normalizer = SkillNormalizer()

    result = normalizer.normalize("  Postgres  ")

    assert result.canonical == "postgresql"
