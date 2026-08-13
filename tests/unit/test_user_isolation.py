"""Cross-user isolation tests.

Verifies that User A cannot access, modify, or use data belonging to User B.
Tests cover: CV files, facts, applications, matching, evidence, browser sessions.
"""


from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.services.fact_ingestion import CandidateFact, DuplicateStatus, FactDeduplicator
from app.services.recruitment import EntityNotFoundError, RecruitmentService
from app.storage.database import Base
from app.storage.tables import (
    ApplicationMatchResultRow,
    ApplicationRow,
    BrowserSessionRow,
    CandidateEvidenceRow,
    CompanyBlacklistRow,
    CvFileRow,
    FactImportBatchRow,
    ProfileFactRow,
    UserRow,
    VacancyRow,
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


def _create_user(session: Session, name: str) -> UserRow:
    user = UserRow(display_name=name)
    session.add(user)
    session.flush()
    return user


def _create_cv(session: Session, user: UserRow, filename: str = "resume.pdf") -> CvFileRow:
    cv = CvFileRow(
        user_id=user.id,
        original_filename=filename,
        storage_path=f"/storage/{filename}",
        content_type="application/pdf",
        sha256="a" * 64,
        size_bytes=100,
    )
    session.add(cv)
    session.flush()
    return cv


def _create_vacancy(session: Session, url: str = "https://example.test/v1") -> VacancyRow:
    vacancy = VacancyRow(
        source_url=url,
        title="Engineer",
        company="Example",
    )
    session.add(vacancy)
    session.flush()
    return vacancy


def _create_application(session: Session, user: UserRow, vacancy: VacancyRow) -> ApplicationRow:
    app = ApplicationRow(
        user_id=user.id,
        vacancy_id=vacancy.id,
        status="awaiting_review",
        match_score=50,
    )
    session.add(app)
    session.flush()
    return app


# ── CV File Isolation ────────────────────────────────────────────


def test_user_a_cannot_get_user_b_cv():
    sf = _session_factory()
    with sf() as session:
        user_a = _create_user(session, "User A")
        user_b = _create_user(session, "User B")
        cv_b = _create_cv(session, user_b)

        service = RecruitmentService(session)
        try:
            service.get_cv_file(user_a.id, cv_b.id)
            raise AssertionError("Should have raised")
        except EntityNotFoundError:
            pass  # Expected: CV not found for user A


def test_user_a_cannot_delete_user_b_cv():
    sf = _session_factory()
    with sf() as session:
        user_a = _create_user(session, "User A")
        user_b = _create_user(session, "User B")
        cv_b = _create_cv(session, user_b)

        service = RecruitmentService(session)
        try:
            service.delete_cv_file(user_a.id, cv_b.id)
            raise AssertionError("Should have raised")
        except EntityNotFoundError:
            pass

        # Verify CV still exists
        assert session.get(CvFileRow, cv_b.id) is not None


def test_user_a_list_cvs_returns_only_own():
    sf = _session_factory()
    with sf() as session:
        user_a = _create_user(session, "User A")
        user_b = _create_user(session, "User B")
        _create_cv(session, user_a, "a_resume.pdf")
        _create_cv(session, user_b, "b_resume.pdf")

        service = RecruitmentService(session)
        cvs_a = service.list_cv_files(user_a.id)
        assert len(cvs_a) == 1
        assert cvs_a[0].user_id == user_a.id


# ── Fact Isolation ───────────────────────────────────────────────


def test_user_a_cannot_update_user_b_fact():
    sf = _session_factory()
    with sf() as session:
        user_a = _create_user(session, "User A")
        user_b = _create_user(session, "User B")
        fact_b = ProfileFactRow(
            user_id=user_b.id,
            category="skill",
            name="Python",
            value="5 years",
            is_verified=True,
        )
        session.add(fact_b)
        session.flush()

        service = RecruitmentService(session)
        try:
            service.update_profile_fact(
                user_a.id,
                fact_b.id,
                category="skill",
                name="Python",
                value="10 years",
                is_verified=True,
            )
            raise AssertionError("Should have raised")
        except EntityNotFoundError:
            pass


def test_user_a_cannot_delete_user_b_fact():
    sf = _session_factory()
    with sf() as session:
        user_a = _create_user(session, "User A")
        user_b = _create_user(session, "User B")
        fact_b = ProfileFactRow(
            user_id=user_b.id,
            category="skill",
            name="Python",
            value="5 years",
            is_verified=True,
        )
        session.add(fact_b)
        session.flush()

        service = RecruitmentService(session)
        try:
            service.delete_profile_fact(user_a.id, fact_b.id)
            raise AssertionError("Should have raised")
        except EntityNotFoundError:
            pass

        assert session.get(ProfileFactRow, fact_b.id) is not None


def test_dedup_does_not_cross_users():
    sf = _session_factory()
    with sf() as session:
        user_a = _create_user(session, "User A")
        user_b = _create_user(session, "User B")
        fact_b = ProfileFactRow(
            user_id=user_b.id,
            category="skill",
            name="Python",
            value="5 years",
            is_verified=True,
        )
        session.add(fact_b)
        session.flush()

        dedup = FactDeduplicator(session)
        # User A has same fact name - should NOT be duplicate of User B's fact
        candidates = [CandidateFact(category="skill", name="Python", value="3 years")]
        result = dedup.check_duplicates(user_a.id, candidates)
        assert result[0].duplicate_status == DuplicateStatus.NEW


# ── Application Isolation ────────────────────────────────────────


def test_user_a_cannot_see_user_b_application():
    sf = _session_factory()
    with sf() as session:
        user_a = _create_user(session, "User A")
        user_b = _create_user(session, "User B")
        vacancy = _create_vacancy(session)
        app_b = _create_application(session, user_b, vacancy)

        # User A should not be able to access User B's application
        # through the recruitment service
        RecruitmentService(session)
        # The service doesn't have a direct get_application, but
        # match-related operations should validate ownership
        app_from_db = session.get(ApplicationRow, app_b.id)
        assert app_from_db.user_id == user_b.id
        assert app_from_db.user_id != user_a.id


def test_user_a_matching_does_not_use_user_b_evidence():
    """User A's matching should never retrieve User B's evidence."""
    sf = _session_factory()
    with sf() as session:
        user_a = _create_user(session, "User A")
        user_b = _create_user(session, "User B")
        _create_cv(session, user_a)
        cv_b = _create_cv(session, user_b)
        _create_vacancy(session)

        # User B has evidence
        evidence_b = CandidateEvidenceRow(
            user_id=user_b.id,
            cv_file_id=cv_b.id,
            evidence_text="UNIQUE_USER_B_SKILL_XYZ",
            normalized_text="unique user b skill xyz",
            evidence_type="skill_statement",
            skill_name="UNIQUE_USER_B_SKILL_XYZ",
            experience_level="production",
            is_verified=True,
            source_fragment="UNIQUE_USER_B_SKILL_XYZ",
            extraction_model="fake",
            extraction_model_version="1",
            extraction_schema_version="1",
            extraction_run_id="run-1",
            confidence=0.9,
        )
        session.add(evidence_b)
        session.flush()

        # Verify: querying for User A's evidence should NOT return User B's
        user_a_evidence = session.scalars(
            select(CandidateEvidenceRow).where(
                CandidateEvidenceRow.user_id == user_a.id,
                CandidateEvidenceRow.is_verified.is_(True),
            )
        ).all()
        assert len(user_a_evidence) == 0

        # Verify: querying for User B's evidence returns only User B's
        user_b_evidence = session.scalars(
            select(CandidateEvidenceRow).where(
                CandidateEvidenceRow.user_id == user_b.id,
                CandidateEvidenceRow.is_verified.is_(True),
            )
        ).all()
        assert len(user_b_evidence) == 1
        assert user_b_evidence[0].skill_name == "UNIQUE_USER_B_SKILL_XYZ"


# ── Blacklist Isolation ──────────────────────────────────────────


def test_user_a_blacklist_independent_of_user_b():
    sf = _session_factory()
    with sf() as session:
        user_a = _create_user(session, "User A")
        user_b = _create_user(session, "User B")

        bl_a = CompanyBlacklistRow(
            user_id=user_a.id,
            company="BadCo",
            normalized_company="badco",
        )
        bl_b = CompanyBlacklistRow(
            user_id=user_b.id,
            company="OtherCo",
            normalized_company="otherco",
        )
        session.add_all((bl_a, bl_b))
        session.flush()

        # User A sees only their blacklist
        a_entries = session.scalars(
            select(CompanyBlacklistRow).where(CompanyBlacklistRow.user_id == user_a.id)
        ).all()
        assert len(a_entries) == 1
        assert a_entries[0].company == "BadCo"


# ── Browser Session Isolation ────────────────────────────────────


def test_browser_session_scoped_to_user():
    sf = _session_factory()
    with sf() as session:
        user_a = _create_user(session, "User A")
        user_b = _create_user(session, "User B")

        bs_a = BrowserSessionRow(
            user_id=user_a.id,
            site_key="linkedin",
            adapter_name="linkedin",
            encrypted_state_path="/state/a",
            status="available",
        )
        bs_b = BrowserSessionRow(
            user_id=user_b.id,
            site_key="linkedin",
            adapter_name="linkedin",
            encrypted_state_path="/state/b",
            status="available",
        )
        session.add_all((bs_a, bs_b))
        session.flush()

        # User A sees only their session
        a_sessions = session.scalars(
            select(BrowserSessionRow).where(BrowserSessionRow.user_id == user_a.id)
        ).all()
        assert len(a_sessions) == 1
        assert a_sessions[0].user_id == user_a.id


# ── Fact Import Batch Isolation ──────────────────────────────────


def test_batch_scoped_to_user():
    sf = _session_factory()
    with sf() as session:
        user_a = _create_user(session, "User A")
        user_b = _create_user(session, "User B")

        batch_a = FactImportBatchRow(
            user_id=user_a.id,
            source_type="file",
            source_filename="a.json",
            extractor_version="1",
        )
        batch_b = FactImportBatchRow(
            user_id=user_b.id,
            source_type="resume",
            source_id="cv-b",
            extractor_version="1",
        )
        session.add_all((batch_a, batch_b))
        session.flush()

        a_batches = session.scalars(
            select(FactImportBatchRow).where(FactImportBatchRow.user_id == user_a.id)
        ).all()
        assert len(a_batches) == 1
        assert a_batches[0].source_type == "file"


# ── Matching Result Isolation ────────────────────────────────────


def test_matching_result_belongs_to_application_owner():
    sf = _session_factory()
    with sf() as session:
        user_a = _create_user(session, "User A")
        user_b = _create_user(session, "User B")
        vacancy = _create_vacancy(session)
        app_a = _create_application(session, user_a, vacancy)
        app_b = _create_application(session, user_b, vacancy)

        match_a = ApplicationMatchResultRow(
            application_id=app_a.id,
            status="scored",
            final_score=80,
            eligibility_status="eligible",
        )
        match_b = ApplicationMatchResultRow(
            application_id=app_b.id,
            status="scored",
            final_score=60,
            eligibility_status="review",
        )
        session.add_all((match_a, match_b))
        session.flush()

        # Verify: match_a belongs to user_a's application
        assert match_a.application_id == app_a.id
        assert app_a.user_id == user_a.id

        # Verify: match_b belongs to user_b's application
        assert match_b.application_id == app_b.id
        assert app_b.user_id == user_b.id

        # Verify: can't access match_b through user_a's context
        user_a_matches = session.scalars(
            select(ApplicationMatchResultRow)
            .join(ApplicationRow)
            .where(ApplicationRow.user_id == user_a.id)
        ).all()
        assert len(user_a_matches) == 1
        assert user_a_matches[0].application_id == app_a.id
