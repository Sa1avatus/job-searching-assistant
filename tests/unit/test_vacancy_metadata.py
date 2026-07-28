import pytest

from app.services.vacancy_metadata import (
    detect_work_format,
    extract_key_skills,
    summarize_vacancy,
)


class TestSummarizeVacancy:
    def test_empty_description_returns_empty_string(self) -> None:
        assert summarize_vacancy("") == ""

    def test_whitespace_only_returns_empty_string(self) -> None:
        assert summarize_vacancy("   \n\t  ") == ""

    def test_informative_first_sentence_returned_even_when_whole_fits(self) -> None:
        text = (
            "We are looking for a skilled Python developer to join our backend team."
            " Must have 3+ years of experience with Django."
        )
        result = summarize_vacancy(text)
        assert result == "We are looking for a skilled Python developer to join our backend team."

    def test_uninformative_short_first_sentence_returns_whole_text(self) -> None:
        text = "Hi! We are looking for a Python developer to join our team."
        result = summarize_vacancy(text)
        assert result == text

    def test_no_sentence_boundary_returns_whole_text_when_it_fits(self) -> None:
        text = "Build scalable microservices in Python"
        result = summarize_vacancy(text)
        assert result == text

    def test_multisentence_text_fits_returns_first_informative_sentence(self) -> None:
        text = "Build Python microservices. Join a great team."
        result = summarize_vacancy(text, max_characters=360)
        assert result == "Build Python microservices."

    def test_long_text_truncated_on_word_boundary_with_ellipsis(self) -> None:
        text = "This is a very long job description " * 20
        result = summarize_vacancy(text, max_characters=100)
        assert len(result) <= 100
        assert result.endswith("\u2026")
        without_ellipsis = result[:-1]
        assert without_ellipsis == without_ellipsis.rstrip()

    def test_one_character_limit_returns_ellipsis(self) -> None:
        result = summarize_vacancy("Long description here", max_characters=1)
        assert result == "\u2026"
        assert len(result) == 1

    def test_zero_max_characters_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="max_characters must be positive"):
            summarize_vacancy("text", max_characters=0)

    def test_negative_max_characters_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="max_characters must be positive"):
            summarize_vacancy("text", max_characters=-5)

    def test_whitespace_normalization(self) -> None:
        result = summarize_vacancy("  Multiple   spaces\n\nand\tlines  ")
        assert result == "Multiple spaces and lines"

    def test_deterministic_output(self) -> None:
        text = "First sentence. Second sentence. Third sentence."
        assert summarize_vacancy(text) == summarize_vacancy(text)

    def test_russian_description_first_sentence(self) -> None:
        text = "Ищем Python-разработчика в команду. Требуется опыт от 3 лет."
        result = summarize_vacancy(text)
        assert result == "Ищем Python-разработчика в команду."

    def test_exact_limit_fits(self) -> None:
        text = "a" * 360
        result = summarize_vacancy(text, max_characters=360)
        assert result == text

    def test_one_over_limit_truncates(self) -> None:
        text = "word " * 80
        result = summarize_vacancy(text, max_characters=360)
        assert len(result) <= 360
        assert result.endswith("\u2026")

    def test_two_character_limit(self) -> None:
        result = summarize_vacancy("Hello world", max_characters=2)
        assert len(result) <= 2
        assert result.endswith("\u2026")

    def test_word_boundary_truncation_includes_full_words(self) -> None:
        text = "aaa bbb ccc ddd eee fff"
        result = summarize_vacancy(text, max_characters=15)
        assert len(result) <= 15
        assert result.endswith("\u2026")


class TestDetectWorkFormat:
    def test_remote_english_title(self) -> None:
        assert detect_work_format("Remote Python Developer", "", "") == "remote"

    def test_remote_english_description(self) -> None:
        assert detect_work_format("", "", "This is a fully remote position.") == "remote"

    def test_remote_english_location(self) -> None:
        assert detect_work_format("", "Remote", "") == "remote"

    def test_work_from_home(self) -> None:
        assert detect_work_format("", "", "Work from home allowed") == "remote"

    def test_remote_position_phrase(self) -> None:
        assert detect_work_format("", "", "We offer a remote position") == "remote"

    def test_hundred_percent_remote(self) -> None:
        assert detect_work_format("", "", "100% remote role") == "remote"

    def test_remote_russian_udalyonnaya(self) -> None:
        assert detect_work_format("Удалённая работа", "", "") == "remote"

    def test_remote_russian_udalyonno(self) -> None:
        assert detect_work_format("", "", "Работа удалённо, гибкий график") == "remote"

    def test_remote_russian_udalennaya(self) -> None:
        assert detect_work_format("", "", "Удаленная работа из дома") == "remote"

    def test_remote_russian_udalyonka(self) -> None:
        assert detect_work_format("", "", "Удалёнка приветствуется") == "remote"

    def test_remote_russian_udalenka(self) -> None:
        assert detect_work_format("", "", "Удаленка, гибкий график") == "remote"

    def test_remote_russian_distantsionno(self) -> None:
        assert detect_work_format("", "", "Дистанционная работа") == "remote"

    def test_remote_russian_na_domu(self) -> None:
        assert detect_work_format("", "", "Работа на дому") == "remote"

    def test_remote_russian_iz_doma(self) -> None:
        assert detect_work_format("", "", "Работа из дома") == "remote"

    def test_hybrid_english(self) -> None:
        assert detect_work_format("Hybrid Developer", "", "") == "hybrid"

    def test_hybrid_english_description(self) -> None:
        assert (
            detect_work_format("", "", "Hybrid work schedule, 3 days office 2 remote")
            == "hybrid"
        )

    def test_hybrid_russian_gibridnyi(self) -> None:
        assert detect_work_format("Гибридный формат", "", "") == "hybrid"

    def test_hybrid_russian_gibridnaya(self) -> None:
        assert detect_work_format("", "", "Гибридная занятость") == "hybrid"

    def test_hybrid_precedence_over_remote_and_office(self) -> None:
        assert detect_work_format("", "", "hybrid remote office") == "hybrid"
        assert detect_work_format("", "", "гибридная удалённая офисная работа") == "hybrid"

    def test_office_english_title(self) -> None:
        assert detect_work_format("Office Manager", "", "") == "office"

    def test_office_english_onsite(self) -> None:
        assert detect_work_format("", "", "On-site position in Berlin") == "office"

    def test_office_english_onsite_hyphenated(self) -> None:
        assert detect_work_format("", "", "This is an on-site role") == "office"

    def test_office_english_in_office(self) -> None:
        assert detect_work_format("", "", "In-office role, Monday to Friday") == "office"

    def test_office_headhunter_employer_location(self) -> None:
        assert (
            detect_work_format(
                "",
                "",
                "Full-time employment. Work format: at the employer's location.",
            )
            == "office"
        )

    def test_office_russian_ofis(self) -> None:
        assert detect_work_format("", "Москва, офис", "") == "office"

    def test_office_russian_v_ofise(self) -> None:
        assert detect_work_format("", "", "Работа в офисе, центр города") == "office"

    def test_office_russian_na_meste(self) -> None:
        assert detect_work_format("", "", "Работа на месте") == "office"

    def test_remote_precedence_over_office(self) -> None:
        assert detect_work_format("", "", "remote office work") == "remote"
        assert detect_work_format("", "", "удалённая офисная работа") == "remote"

    def test_unspecified_when_no_keywords(self) -> None:
        assert (
            detect_work_format("Python Engineer", "Moscow", "Build scalable systems")
            == "unspecified"
        )

    def test_unspecified_empty_inputs(self) -> None:
        assert detect_work_format("", "", "") == "unspecified"

    def test_no_false_positive_police_officer(self) -> None:
        assert detect_work_format("Police Officer", "", "") == "unspecified"

    def test_no_false_positive_ceo(self) -> None:
        assert detect_work_format("", "", "Chief Executive Officer role") == "unspecified"

    def test_no_false_positive_udaleniye_russian(self) -> None:
        assert (
            detect_work_format("", "", "Удаление данных из базы данных")
            == "unspecified"
        )

    def test_no_false_positive_administrator_russian(self) -> None:
        assert (
            detect_work_format("", "", "Администрация компании")
            == "unspecified"
        )


def test_extract_key_skills_combines_declared_and_description_skills() -> None:
    result = extract_key_skills(
        "Build Python services with Docker, Kubernetes, TensorFlow and CI/CD.",
        declared_skills=["Python", "PostgreSQL", "python"],
    )

    assert result[:2] == ("Python", "PostgreSQL")
    assert set(result) >= {
        "Python",
        "PostgreSQL",
        "Docker",
        "Kubernetes",
        "TensorFlow",
        "CI/CD",
    }
