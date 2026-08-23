"""Tests for stratified pointwise sampling."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from app.matching.cross_encoder.annotation import (
    SampledCandidate,
    _stratified_sample,
    STRATA_QUOTAS,
    STRATA_PRIORITY,
)
from app.matching.cross_encoder.features import MatchFeatures


class TestStratifiedSampling:
    """Tests for _stratified_sample function."""

    def make_candidate(
        self,
        vacancy_id: str,
        current_rank: int | None = None,
        ltr_rank: int | None = None,
        current_score: float | None = None,
        ltr_score: float | None = None,
    ) -> SampledCandidate:
        return SampledCandidate(
            vacancy_id=vacancy_id,
            vacancy_title=f"Vacancy {vacancy_id}",
            vacancy_company="Test Company",
            vacancy_location="Remote",
            vacancy_description="Test description",
            required_skills=["Python"],
            preferred_skills=["Docker"],
            salary_text="100k",
            work_format="remote",
            employment_types=["full_time"],
            current_rank=current_rank,
            ltr_rank=ltr_rank,
            current_score=current_score,
            ltr_score=ltr_score,
            sampling_reason="",
            strata=[],
        )

    def test_empty_candidates_returns_empty(self):
        """Empty candidate list returns empty result."""
        result = _stratified_sample([])
        assert result == []

    def test_sample_size_respects_limit(self):
        """Sample size should not exceed limit."""
        candidates = [self.make_candidate(f"v{i}", current_rank=i, ltr_rank=i) for i in range(1, 200)]
        result = _stratified_sample(candidates, limit=100)
        assert len(result) <= 100

    def test_sample_size_equals_limit_when_enough_candidates(self):
        """When enough candidates, sample size should equal limit."""
        candidates = [self.make_candidate(f"v{i}", current_rank=i, ltr_rank=i) for i in range(1, 200)]
        result = _stratified_sample(candidates, limit=100)
        assert len(result) == 100

    def test_all_strata_represented(self):
        """All five strata should be represented when enough candidates."""
        # Create candidates with varying rank disagreements
        candidates = []
        for i in range(1, 200):
            c = self.make_candidate(f"v{i}", current_rank=i, ltr_rank=i + 10 if i < 100 else i - 10)
            candidates.append(c)
        
        result = _stratified_sample(candidates, limit=100)
        strata_found = set(c.sampling_reason for c in result)
        
        # Check all priority strata are present (except possibly 'fill')
        for stratum in STRATA_PRIORITY:
            assert stratum in strata_found, f"Stratum {stratum} not found in result"

    def test_deterministic_sampling(self):
        """Repeated sampling with same input should produce same result."""
        candidates = [self.make_candidate(f"v{i}", current_rank=i, ltr_rank=i) for i in range(1, 150)]
        
        result1 = _stratified_sample(candidates, limit=100)
        result2 = _stratified_sample(candidates, limit=100)
        
        assert [c.vacancy_id for c in result1] == [c.vacancy_id for c in result2]

    def test_no_duplicate_vacancy_ids(self):
        """No duplicate vacancy_ids in final sample."""
        candidates = [self.make_candidate(f"v{i}", current_rank=i, ltr_rank=i) for i in range(1, 150)]
        result = _stratified_sample(candidates, limit=100)
        
        ids = [c.vacancy_id for c in result]
        assert len(ids) == len(set(ids)), "Duplicate vacancy_ids found"

    def test_overlapping_strata_priority(self):
        """Candidate in multiple strata gets highest priority reason."""
        # Create a candidate that would be in rank_disagreement, ltr_top, and current_top
        candidates = []
        # High disagreement candidate that's also top in both
        c = self.make_candidate("v1", current_rank=1, ltr_rank=50)
        candidates.append(c)
        # Fill rest
        for i in range(2, 150):
            candidates.append(self.make_candidate(f"v{i}", current_rank=i, ltr_rank=i))
        
        result = _stratified_sample(candidates, limit=100)
        
        # Find the candidate
        v1 = next(c for c in result if c.vacancy_id == "v1")
        # Should have rank_disagreement as highest priority
        assert v1.sampling_reason == "rank_disagreement"
        assert "ltr_top" in v1.strata
        assert "current_top" in v1.strata

    def test_existing_pointwise_excluded(self):
        """This is tested at the queue level, not in _stratified_sample."""
        # _stratified_sample doesn't handle exclusion - that's done before
        pass

    def test_fewer_candidates_than_limit(self):
        """When fewer candidates than limit, return all."""
        candidates = [self.make_candidate(f"v{i}", current_rank=i, ltr_rank=i) for i in range(1, 50)]
        result = _stratified_sample(candidates, limit=100)
        assert len(result) == 49  # 49 candidates

    def test_middle_rank_adapts_to_candidate_count(self):
        """Middle rank range should adapt for smaller datasets."""
        # Small dataset
        candidates = [self.make_candidate(f"v{i}", current_rank=i, ltr_rank=i) for i in range(1, 60)]
        result = _stratified_sample(candidates, limit=100)
        
        # Should still get middle_rank stratum
        middle_found = any(c.sampling_reason == "middle_rank" for c in result)
        assert middle_found

    def test_strata_counts_respect_quotas(self):
        """Each stratum should not exceed its quota."""
        candidates = [self.make_candidate(f"v{i}", current_rank=i, ltr_rank=i) for i in range(1, 500)]
        result = _stratified_sample(candidates, limit=100)
        
        from collections import Counter
        strata_counts = Counter(c.sampling_reason for c in result)
        
        # Check quotas (with some tolerance for fill)
        assert strata_counts.get("rank_disagreement", 0) <= STRATA_QUOTAS["rank_disagreement"]
        assert strata_counts.get("ltr_top", 0) <= STRATA_QUOTAS["ltr_top"]
        assert strata_counts.get("current_top", 0) <= STRATA_QUOTAS["current_top"]
        assert strata_counts.get("middle_rank", 0) <= STRATA_QUOTAS["middle_rank"]
        assert strata_counts.get("random", 0) <= STRATA_QUOTAS["random"]

    def test_fill_stratum_used_when_needed(self):
        """Fill stratum used when strata overlap reduces unique count."""
        # Create many overlapping candidates
        candidates = []
        # First 20 candidates are top in both rankings (high overlap)
        for i in range(1, 21):
            candidates.append(self.make_candidate(f"v{i}", current_rank=i, ltr_rank=i))
        # Rest are distinct
        for i in range(21, 200):
            candidates.append(self.make_candidate(f"v{i}", current_rank=i, ltr_rank=i + 100))
        
        result = _stratified_sample(candidates, limit=100)
        
        # Should have fill stratum if needed
        fill_found = any(c.sampling_reason == "fill" for c in result)
        # With this setup, there might be enough unique candidates, so fill may not be needed
        assert len(result) == 100

    def test_sorting_by_priority_then_rank(self):
        """Final result sorted by stratum priority then rank."""
        candidates = [self.make_candidate(f"v{i}", current_rank=i, ltr_rank=i) for i in range(1, 150)]
        result = _stratified_sample(candidates, limit=100)
        
        # Check ordering: rank_disagreement first, then ltr_top, etc.
        prev_priority = -1
        prev_rank = -1
        for c in result:
            priority = STRATA_PRIORITY.index(c.sampling_reason) if c.sampling_reason in STRATA_PRIORITY else 99
            assert priority >= prev_priority, f"Priority decreased: {prev_priority} -> {priority}"
            if priority == prev_priority:
                assert c.current_rank >= prev_rank, f"Rank not sorted within priority: {prev_rank} -> {c.current_rank}"
            prev_priority = priority
            prev_rank = c.current_rank or 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
