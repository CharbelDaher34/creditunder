"""Loader for `validation_config.yaml`.

Validation thresholds and recommendation mappings live in YAML rather than
in code so they can be tuned without a deployment. The loaded config is
cached for the process lifetime; restart the processor to pick up changes.

`get_validation_config().version` is stamped onto every `validation_result`
row (`config_version`) so the explainability trail is reproducible across
config changes.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from creditunder.domain.enums import Recommendation


_CONFIG_PATH = Path(__file__).parent / "validation_config.yaml"


class PersonalFinanceConfig(BaseModel):
    salary_deviation_tolerance: float = 0.10


class RecommendationMapping(BaseModel):
    on_hard_breach: Recommendation = Recommendation.DECLINE
    on_manual_review: Recommendation = Recommendation.HOLD
    on_soft_mismatch: Recommendation = Recommendation.HOLD
    on_low_confidence: Recommendation = Recommendation.HOLD
    default: Recommendation = Recommendation.APPROVE


class ValidationConfig(BaseModel):
    version: str
    confidence_thresholds: dict[str, float] = Field(default_factory=dict)
    personal_finance: PersonalFinanceConfig = Field(default_factory=PersonalFinanceConfig)
    recommendation_mapping: RecommendationMapping = Field(default_factory=RecommendationMapping)

    def confidence_threshold(self, document_type: str) -> float:
        if document_type in self.confidence_thresholds:
            return self.confidence_thresholds[document_type]
        try:
            return self.confidence_thresholds["default"]
        except KeyError as exc:
            raise RuntimeError(
                "validation_config.yaml: confidence_thresholds.default is required"
            ) from exc


@lru_cache(maxsize=1)
def get_validation_config() -> ValidationConfig:
    with _CONFIG_PATH.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return ValidationConfig.model_validate(raw)
