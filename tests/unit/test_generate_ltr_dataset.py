"""Tests for LTR dataset generation from annotations."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.generate_ltr_dataset import (
    build_dataset_from_db,
    main,
)


class TestGenerateLTRDataset:
    """Tests for generate_ltr_dataset.py."""

    @pytest.fixture
    def mock_session(self):
        session = MagicMock()
        return session

    @patch("scripts.generate_ltr_dataset.session_scope")
    @patch("scripts.generate_ltr_dataset.build_dataset_from_db")
    def test_main_writes_jsonl(self, mock_build, mock_session_scope, tmp_path):
        """Main should write dataset to JSONL file."""
        mock_build.return_value = [
            {"resume_id": "r1", "vacancy_id": "v1", "human_signal": 1.0, "ce_ettin_raw": 0.8},
            {"resume_id": "r1", "vacancy_id": "v2", "human_signal": 0.0, "ce_ettin_raw": 0.3},
        ]
        
        mock_session = MagicMock()
        mock_session_scope.return_value.__enter__.return_value = mock_session
        
        output_path = tmp_path / "ltr_dataset.jsonl"
        
        with patch("scripts.generate_ltr_dataset.Path") as mock_path:
            mock_path.return_value = output_path
            main()
        
        assert output_path.exists()
        with open(output_path) as f:
            lines = f.readlines()
        assert len(lines) == 2
        for line in lines:
            data = json.loads(line)
            assert "resume_id" in data
            assert "vacancy_id" in data
            assert "human_signal" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
