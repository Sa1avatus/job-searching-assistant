"""Integration tests for annotation API and sampling."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from app.matching.cross_encoder.annotation import (
    get_annotation_queue,
    submit_pointwise,
    submit_pairwise,
    get_annotation_stats,
    get_dataset_readiness,
)
from app.matching.cross_encoder.features import MatchFeatures
from app.storage.tables import CvFileRow, UserRow, VacancyRow, AnnotationFeedbackRow


class TestAnnotationQueue:
    """Tests for get_annotation_queue."""

    @pytest.fixture
    def mock_session(self):
        session = MagicMock()
        return session

    @pytest.fixture
    def mock_cv(self):
        cv = MagicMock(spec=CvFileRow)
        cv.id = "resume_1"
        cv.user_id = "user_1"
        cv.original_filename = "test_resume.pdf"
        cv.experience_summary = "Python developer"
        cv.skills = '["Python", "Docker"]'
        return cv

    @pytest.fixture
    def mock_features(self):
        features = []
        for i in range(1, 50):
            f = MatchFeatures(
                resume_id="resume_1",
                vacancy_id=f"vacancy_{i}",
                ce_ettin_raw=0.8 - i * 0.01,
                ce_mmbert_raw=0.7 - i * 0.01,
                ce_modernbert_raw=0.6 - i * 0.01,
                existing_match_score=80.0 - i * 0.5,
                existing_reranker_score=0.7 - i * 0.01,
                existing_semantic_similarity=0.6 - i * 0.01,
                required_total=5,
                required_matched=4,
                required_unmatched=1,
                required_coverage=0.8,
                required_avg_score=0.85,
                preferred_total=3,
                preferred_matched=2,
                preferred_unmatched=1,
                preferred_coverage=0.67,
                preferred_avg_score=0.7,
                hard_skill_coverage=0.9,
                experience_coverage=0.85,
                seniority_coverage=0.75,
                language_coverage=0.8,
                blocker_count=0,
                has_hard_blocker=False,
                has_location=True,
                has_work_format=True,
                has_salary=False,
                has_employment_types=True,
            )
            features.append(f)
        return features

    def test_queue_requires_ownership(self, mock_session, mock_cv):
        """Queue should verify ownership."""
        mock_session.get.return_value = mock_cv
        
        result = get_annotation_queue(
            mock_session, "user_2", "resume_1", [], limit=10
        )
        assert result.items == []
        assert result.total_eligible == 0

    def test_queue_loads_resume(self, mock_session, mock_cv, mock_features):
        """Queue should load resume text and skills."""
        mock_session.get.return_value = mock_cv
        
        result = get_annotation_queue(
            mock_session, "user_1", "resume_1", mock_features, limit=10
        )
        assert result.resume_filename == "test_resume.pdf"
        assert result.resume_id == "resume_1"


class TestSubmitPointwise:
    """Tests for submit_pointwise."""

    @pytest.fixture
    def mock_session(self):
        session = MagicMock()
        return session

    @pytest.fixture
    def mock_cv(self):
        cv = MagicMock(spec=CvFileRow)
        cv.id = "resume_1"
        cv.user_id = "user_1"
        return cv

    @pytest.fixture
    def mock_vacancy(self):
        vac = MagicMock(spec=VacancyRow)
        vac.id = "vacancy_1"
        return vac

    def test_submit_requires_ownership(self, mock_session, mock_cv, mock_vacancy):
        """Submit should verify ownership."""
        mock_session.get.side_effect = [mock_cv, mock_vacancy]
        
        result = submit_pointwise(mock_session, "user_2", "resume_1", "vacancy_1", "relevant", [], None)
        assert result.status == "forbidden"

    def test_submit_creates_new_annotation(self, mock_session, mock_cv, mock_vacancy):
        """Submit should create new annotation when none exists."""
        mock_session.get.side_effect = [mock_cv, mock_vacancy]
        mock_session.execute.return_value.scalar_one_or_none.return_value = None
        
        result = submit_pointwise(mock_session, "user_1", "resume_1", "vacancy_1", "relevant", ["good match"], "Great fit")
        
        assert result.status == "accepted"
        assert result.label == "relevant"
        mock_session.add.assert_called_once()

    def test_submit_updates_existing(self, mock_session, mock_cv, mock_vacancy):
        """Submit should update existing annotation."""
        existing = MagicMock(spec=AnnotationFeedbackRow)
        existing.id = "existing_id"
        
        mock_session.get.side_effect = [mock_cv, mock_vacancy]
        mock_session.execute.return_value.scalar_one_or_none.return_value = existing
        
        result = submit_pointwise(mock_session, "user_1", "resume_1", "vacancy_1", "maybe", ["ok"], "Okay fit")
        
        assert result.status == "accepted"
        assert result.label == "maybe"
        assert existing.label == "maybe"
        assert existing.reasons == ["ok"]


class TestSubmitPairwise:
    """Tests for submit_pairwise."""

    @pytest.fixture
    def mock_session(self):
        session = MagicMock()
        return session

    @pytest.fixture
    def mock_cv(self):
        cv = MagicMock(spec=CvFileRow)
        cv.id = "resume_1"
        cv.user_id = "user_1"
        return cv

    @pytest.fixture
    def mock_vacancies(self):
        vac_a = MagicMock(spec=VacancyRow)
        vac_a.id = "vacancy_a"
        vac_b = MagicMock(spec=VacancyRow)
        vac_b.id = "vacancy_b"
        return vac_a, vac_b

    def test_submit_requires_ownership(self, mock_session, mock_cv, mock_vacancies):
        """Submit should verify ownership."""
        mock_session.get.side_effect = [mock_cv, mock_vacancies[0], mock_vacancies[1]]
        
        result = submit_pairwise(mock_session, "user_2", "resume_1", "vacancy_a", "vacancy_b", "a_better", [], [], None)
        assert result.status == "forbidden"

    def test_submit_creates_new_pairwise(self, mock_session, mock_cv, mock_vacancies):
        """Submit should create new pairwise annotation."""
        mock_session.get.side_effect = [mock_cv, mock_vacancies[0], mock_vacancies[1]]
        mock_session.execute.return_value.scalar_one_or_none.return_value = None
        
        result = submit_pairwise(mock_session, "user_1", "resume_1", "vacancy_a", "vacancy_b", "a_better", ["a better"], ["b worse"], "A is better")
        
        assert result.status == "accepted"
        assert result.label == "a_better"
        mock_session.add.assert_called_once()


class TestAnnotationStats:
    """Tests for get_annotation_stats."""

    @pytest.fixture
    def mock_session(self):
        session = MagicMock()
        return session

    def test_empty_stats(self, mock_session):
        """Empty annotations returns zero stats."""
        mock_session.execute.return_value.scalars.return_value.all.return_value = []
        
        result = get_annotation_stats(mock_session)
        assert result.total_pointwise == 0
        assert result.total_pairwise == 0
        assert result.unique_resumes == 0
        assert result.unique_vacancies == 0

    def test_stats_with_data(self, mock_session):
        """Stats should count annotations correctly."""
        pw1 = MagicMock(spec=AnnotationFeedbackRow)
        pw1.feedback_type = "pointwise"
        pw1.label = "relevant"
        pw1.resume_id = "r1"
        pw1.vacancy_id = "v1"
        
        pw2 = MagicMock(spec=AnnotationFeedbackRow)
        pw2.feedback_type = "pointwise"
        pw2.label = "maybe"
        pw2.resume_id = "r1"
        pw2.vacancy_id = "v2"
        
        pws1 = MagicMock(spec=AnnotationFeedbackRow)
        pws1.feedback_type = "pairwise"
        pws1.label = "a_better"
        pws1.resume_id = "r1"
        pws1.vacancy_id = "v1"
        
        mock_session.execute.return_value.scalars.return_value.all.side_effect = [
            [pw1, pw2],  # pointwise
            [pws1],      # pairwise
        ]
        
        result = get_annotation_stats(mock_session)
        assert result.total_pointwise == 2
        assert result.total_pairwise == 1
        assert result.pointwise_by_label == {"relevant": 1, "maybe": 1}
        assert result.pairwise_by_label == {"a_better": 1}
        assert result.unique_resumes == 1
        assert result.unique_vacancies == 2


class TestDatasetReadiness:
    """Tests for get_dataset_readiness."""

    @pytest.fixture
    def mock_session(self):
        session = MagicMock()
        return session

    def test_not_ready_when_insufficient_data(self, mock_session):
        """Should not be ready with insufficient data."""
        mock_session.execute.return_value.all.return_value = []
        
        result = get_dataset_readiness(mock_session)
        assert result.ready_for_training is False
        assert result.unique_resume_groups == 0
        assert result.min_observations_per_group == 0

    def test_ready_when_sufficient_data(self, mock_session):
        """Should be ready with sufficient data."""
        # Mock pointwise counts per resume
        mock_session.execute.return_value.all.side_effect = [
            [("r1", 60), ("r2", 55)],  # pointwise
            [("r1", 10), ("r2", 5)],   # pairwise
        ]
        
        result = get_dataset_readiness(mock_session)
        assert result.pointwise_observations == 115
        assert result.pairwise_observations == 15
        assert result.unique_resume_groups == 2
        assert result.min_observations_per_group == 60  # r2 has 55+5=60
        assert result.ready_for_training is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
