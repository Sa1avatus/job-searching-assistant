"""Versioned feature schema and model artifacts for learning-to-rank.

A model is only valid for the exact feature list it was trained on, so the artifact stores the
schema version and a hash of the ordered feature names; loading refuses a mismatch instead of
silently scoring with shifted columns. A model trained on an incomplete dataset is marked
``provisional`` and is never loaded for inference unless explicitly allowed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.matching.cross_encoder.ltr_feature_contract import LTR_FEATURE_NAMES

# Bump when the meaning of a feature changes without its name changing.
FEATURE_SCHEMA_VERSION = 1


def feature_schema_hash(names: list[str] | None = None) -> str:
    payload = json.dumps({"version": FEATURE_SCHEMA_VERSION, "names": names or LTR_FEATURE_NAMES})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class ModelArtifactError(ValueError):
    pass


@dataclass(slots=True)
class ModelArtifact:
    model_type: str  # logistic | lambdamart
    feature_names: list[str]
    schema_version: int
    schema_hash: str
    parameters: dict[str, Any]
    trained_on: dict[str, Any]
    metrics: dict[str, Any] = field(default_factory=dict)
    provisional: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(asdict(self), indent=2, sort_keys=True), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: str | Path, *, allow_provisional: bool = False) -> ModelArtifact:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        artifact = cls(**data)
        if artifact.schema_version != FEATURE_SCHEMA_VERSION or artifact.schema_hash != (
            feature_schema_hash(artifact.feature_names)
        ):
            raise ModelArtifactError("Model was trained on a different feature schema")
        if artifact.feature_names != LTR_FEATURE_NAMES:
            raise ModelArtifactError("Model feature list does not match the runtime contract")
        if artifact.provisional and not allow_provisional:
            raise ModelArtifactError(
                "Model is provisional (trained on an incomplete dataset) and cannot be used"
            )
        return artifact
