"""모든 분석 모듈이 공유하는 입출력 계약.

AquaGuard의 응답계약을 이식한다.

- 모든 모듈은 run(payload: dict) -> dict 로 독립 실행된다.
- 모든 API 응답은 status / fallback_tier / data / warnings / provenance 를 갖는다.
- 모든 숫자에는 source, date, model_version 이 붙는다 (PolicyMaps Evidence schema).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

CRS = "EPSG:5179"  # Korea 2000 / Unified CS
TZ = "Asia/Seoul"


class Status(str, Enum):
    OK = "OK"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class FallbackTier(str, Enum):
    """관측 가용성에 따른 서비스 등급. 화면에 그대로 표시한다."""

    A = "A"  # S1 + S2 + 기상 + 현장자료
    B = "B"  # S1 + 기상
    C = "C"  # 예측모델 + 기상, 위성 후속관측 대기


class Provenance(str, Enum):
    """숫자의 출처 구분. UI 범례와 1:1 대응한다."""

    OBSERVED = "OBSERVED"
    FORECAST = "FORECAST"
    MODEL = "MODEL"
    ASSUMPTION = "ASSUMPTION"


@dataclass
class Evidence:
    """단일 값에 붙는 근거. 근거 없는 값은 UI로 내보내지 않는다."""

    value: Any
    provenance: Provenance
    source: str                      # 예: Sentinel-1 GRD IW VV
    observed_at: str | None = None   # ISO8601, TZ 명시
    model_version: str | None = None
    confidence: str | None = None    # HIGH / MEDIUM / LOW


@dataclass
class ModuleResponse:
    status: Status
    data: dict[str, Any]
    fallback_tier: FallbackTier = FallbackTier.C
    warnings: list[str] = field(default_factory=list)
    provenance: dict[str, Evidence] = field(default_factory=dict)


def run(payload: dict) -> dict:
    """각 분석 모듈이 동일 시그니처로 구현한다."""
    raise NotImplementedError
