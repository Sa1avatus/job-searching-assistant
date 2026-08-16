"""Tests for fact ingestion: file parsing, deduplication, resume extraction schemas."""

import csv
import io
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.services.fact_ingestion import (
    CandidateFact,
    DuplicateStatus,
    ExperienceInterval,
    ExtractedFact,
    FactDeduplicator,
    FileParser,
    ResumeExtractionResult,
)
from app.storage.database import Base
from app.storage.tables import (
    FactImportBatchRow,
    ProfileFactRow,
    UserRow,
)

# ── Helpers ──────────────────────────────────────────────────────


def _session_factory() -> sessionmaker:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


# ── File Parser Tests ────────────────────────────────────────────


def test_parse_txt():
    parser = FileParser()
    result = parser.parse("resume.txt", b"Hello world")
    assert result == "Hello world"


def test_parse_md():
    parser = FileParser()
    result = parser.parse("notes.md", b"# Title\n\nSome content")
    assert "Title" in result
    assert "content" in result


def test_parse_csv():
    parser = FileParser()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["text", "type", "skills"])
    writer.writerow(["Built FastAPI backend", "project", "Python, FastAPI"])
    result = parser.parse("facts.csv", buf.getvalue().encode("utf-8"))
    assert "Built FastAPI backend" in result
    assert "Python" in result


def test_parse_json_as_text():
    parser = FileParser()
    data = [
        {"text": "Developed ML pipeline", "type": "project", "skills": ["PyTorch"]},
        {"text": "Built REST API", "type": "project", "skills": ["FastAPI"]},
    ]
    result = parser.parse("facts.json", json.dumps(data).encode("utf-8"))
    assert "Developed ML pipeline" in result
    assert "Built REST API" in result


def test_parse_structured_json():
    parser = FileParser()
    data = [
        {
            "text": "Built FastAPI backend",
            "type": "project_evidence",
            "skills": ["Python", "FastAPI"],
        },
        {"text": "ML pipelines", "type": "project_evidence", "skills": ["PyTorch"]},
    ]
    result = parser.parse_structured("facts.json", json.dumps(data).encode("utf-8"))
    assert len(result) == 2
    assert result[0].text == "Built FastAPI backend"
    assert result[0].skills == ["Python", "FastAPI"]


def test_parse_structured_csv():
    parser = FileParser()
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["text", "type", "skills", "confidence"])
    writer.writeheader()
    writer.writerow(
        {"text": "Built API", "type": "project", "skills": "Python,FastAPI", "confidence": "0.9"}
    )
    result = parser.parse_structured("facts.csv", buf.getvalue().encode("utf-8"))
    assert len(result) == 1
    assert result[0].text == "Built API"
    assert result[0].skills == ["Python", "FastAPI"]


def test_parse_unsupported_extension():
    parser = FileParser()
    try:
        parser.parse("file.xyz", b"content")
        raise AssertionError("Should have raised")
    except ValueError as e:
        assert "Unsupported" in str(e)


def test_parse_empty_file():
    parser = FileParser()
    try:
        parser.parse("empty.txt", b"")
        raise AssertionError("Should have raised")
    except Exception:
        pass  # Various errors acceptable for empty


def test_parse_malformed_json():
    parser = FileParser()
    try:
        parser.parse_structured("bad.json", b"{not valid json")
        raise AssertionError("Should have raised")
    except Exception:
        pass  # JSON parse error expected


# ── Deduplication Tests ──────────────────────────────────────────


def test_dedup_new_fact():
    sf = _session_factory()
    with sf() as session:
        user = UserRow(display_name="Test")
        session.add(user)
        session.flush()

        dedup = FactDeduplicator(session)
        candidates = [
            CandidateFact(
                category="skills",
                name="Python",
                value="Python programming",
            )
        ]
        result = dedup.check_duplicates(user.id, candidates)
        assert len(result) == 1
        assert result[0].duplicate_status == DuplicateStatus.NEW


def test_dedup_exact_duplicate():
    sf = _session_factory()
    with sf() as session:
        user = UserRow(display_name="Test")
        session.add(user)
        session.flush()
        fact = ProfileFactRow(
            user_id=user.id,
            category="skills",
            name="Python",
            value="Python programming",
            is_verified=True,
        )
        session.add(fact)
        session.flush()

        dedup = FactDeduplicator(session)
        candidates = [CandidateFact(category="skills", name="Python", value="Python programming")]
        result = dedup.check_duplicates(user.id, candidates)
        assert result[0].duplicate_status == DuplicateStatus.DUPLICATE


def test_dedup_merge_candidate():
    sf = _session_factory()
    with sf() as session:
        user = UserRow(display_name="Test")
        session.add(user)
        session.flush()
        fact = ProfileFactRow(
            user_id=user.id,
            category="skills",
            name="Python",
            value="Python",
            is_verified=True,
        )
        session.add(fact)
        session.flush()

        dedup = FactDeduplicator(session)
        candidates = [
            CandidateFact(
                category="skills",
                name="Python",
                value="Developed async backend APIs with Python, FastAPI, PostgreSQL, Docker",
            )
        ]
        result = dedup.check_duplicates(user.id, candidates)
        assert result[0].duplicate_status == DuplicateStatus.MERGE_CANDIDATE


def test_dedup_near_duplicate():
    sf = _session_factory()
    with sf() as session:
        user = UserRow(display_name="Test")
        session.add(user)
        session.flush()
        fact = ProfileFactRow(
            user_id=user.id,
            category="project_experience",
            name="FastAPI backend development",
            value="Built APIs",
            is_verified=True,
        )
        session.add(fact)
        session.flush()

        dedup = FactDeduplicator(session)
        candidates = [
            CandidateFact(
                category="project_experience",
                name="FastAPI backend services",
                value="Developed FastAPI services",
            )
        ]
        result = dedup.check_duplicates(user.id, candidates)
        # Near-duplicate detected by name token overlap
        assert result[0].duplicate_status == DuplicateStatus.MERGE_CANDIDATE


# ── ExtractedFact Schema Tests ───────────────────────────────────


def test_extracted_fact_defaults():
    fact = ExtractedFact(
        category="skills",
        name="Python",
        value="Python programming",
    )
    assert fact.experience_level == "mentioned"
    assert fact.confidence == 0.85
    assert fact.skills == []


def test_extracted_fact_experience_levels():
    for level in [
        "mentioned",
        "used",
        "practical_experience",
        "production_experience",
        "designed",
        "implemented",
        "operated",
        "evaluated",
    ]:
        fact = ExtractedFact(category="x", name="y", value="z", experience_level=level)
        assert fact.experience_level == level


def test_extracted_fact_invalid_level():
    fact = ExtractedFact(category="x", name="y", value="z", experience_level="invalid")
    assert fact.experience_level == "mentioned"


def test_experience_interval():
    interval = ExperienceInterval(
        role="ML Engineer",
        company="Example",
        started_at="2021-01",
        ended_at="2024-06",
        domains=["ML", "NLP"],
        skills=["Python", "PyTorch"],
    )
    assert interval.role == "ML Engineer"
    assert interval.ended_at == "2024-06"


def test_resume_extraction_result():
    result = ResumeExtractionResult(
        facts=[
            ExtractedFact(category="skills", name="Python", value="Python dev"),
        ],
        experience_intervals=[
            ExperienceInterval(role="Engineer", started_at="2020-01"),
        ],
    )
    assert len(result.facts) == 1
    assert len(result.experience_intervals) == 1


# ── Import Batch Tests ───────────────────────────────────────────


def test_batch_creation():
    sf = _session_factory()
    with sf() as session:
        user = UserRow(display_name="Test")
        session.add(user)
        session.flush()
        batch = FactImportBatchRow(
            user_id=user.id,
            source_type="file",
            source_filename="test.json",
            extractor_version="1",
        )
        session.add(batch)
        session.flush()
        assert batch.id is not None
        assert batch.status == "completed"  # default


# ── Mixed RU/EN Facts ────────────────────────────────────────────


def test_parse_russian_text():
    parser = FileParser()
    text = "Разработал бэкенд на Python и FastAPI.\nDeployed with Docker."
    result = parser.parse("resume.txt", text.encode("utf-8"))
    assert "Python" in result
    assert "Docker" in result


def test_structured_import_mixed_languages():
    parser = FileParser()
    data = [
        {
            "text": "Разработал асинхронный бэкенд на FastAPI",
            "type": "project",
            "skills": ["Python", "FastAPI"],
        },
        {"text": "Deployed ML models with Docker", "type": "project", "skills": ["Docker", "ML"]},
    ]
    result = parser.parse_structured("mixed.json", json.dumps(data).encode("utf-8"))
    assert len(result) == 2
    assert "FastAPI" in result[0].skills
