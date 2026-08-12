from app.services.email_entity_extraction import EmailEntities, extract_email_entities


def test_extracts_company_from_subject() -> None:
    result = extract_email_entities("From Google: Your application status")

    assert result.company == "Google"
    assert result.confidence == 0.5


def test_extracts_vacancy_title_from_subject() -> None:
    result = extract_email_entities("Re: Senior Python Developer position")

    assert result.vacancy_title is not None
    assert "Python" in result.vacancy_title or "Developer" in result.vacancy_title


def test_extracts_both_company_and_vacancy() -> None:
    result = extract_email_entities(
        "From Yandex: Re: Backend Engineer vacancy"
    )

    assert result.company is not None
    assert result.vacancy_title is not None
    assert result.confidence == 1.0


def test_returns_none_for_unrelated_email() -> None:
    result = extract_email_entities("Newsletter", "Weekly hiring digest")

    assert result.company is None
    assert result.vacancy_title is None
    assert result.confidence == 0.0


def test_extracts_russian_company() -> None:
    result = extract_email_entities("От Яндекс: Обновление по заявке")

    assert result.company is not None
    assert "Яндекс" in result.company or "яндекс" in result.company.lower()


def test_extracts_russian_vacancy() -> None:
    result = extract_email_entities("По вакансии: Разработчик Python")

    assert result.vacancy_title is not None
    assert "Python" in result.vacancy_title or "Разработчик" in result.vacancy_title


def test_falls_back_to_body_for_vacancy() -> None:
    result = extract_email_entities(
        "Application update",
        "Regarding the position: Senior Data Engineer at our company.",
    )

    assert result.vacancy_title is not None


def test_returns_dataclass_with_all_fields() -> None:
    result = extract_email_entities("Test subject")

    assert isinstance(result, EmailEntities)
    assert hasattr(result, "company")
    assert hasattr(result, "vacancy_title")
    assert hasattr(result, "confidence")
