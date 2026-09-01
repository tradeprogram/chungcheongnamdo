"""현장 대응 우선순위 큐.

단순 위험도 정렬이 아니라 자원제약을 명시한 ranking 또는 최적화다.

    maximize  sum_i  x_i * (Impact_i * Urgency_i * Recoverability_i * Vulnerability_i)
    s.t.      sum_i  TravelTime_i * x_i <= T
              sum_i  x_i <= N

이 계산을 LLM에게 시키지 않는다. OR-Tools 또는 명시적 ranking engine이 결과를
생성하고 Agent는 설명만 담당한다.

필지 출력 스키마
    field_id, crop_type,
    forecast_flood_probability, observed_flood_fraction,
    impact_level, recovery_anomaly, uncertainty, access_minutes,
    recommended_action_class, evidence[]

action_class는 충남의 실제 사후관리 업무와 연결한다.
    FIELD_INSPECTION_PRIORITY / DRAINAGE_PUMP / PEST_CONTROL / RESEEDING_REVIEW

TODO(P1): Top-10 필지와 reason code 반환
"""

from __future__ import annotations

ACTION_CLASSES = [
    "FIELD_INSPECTION_PRIORITY",
    "DRAINAGE_PUMP",
    "PEST_CONTROL",
    "RESEEDING_REVIEW",
]


def score_impact(parcel_row):
    """침수확률 또는 실침수, 침수지속 proxy, 생육단계, 회복 anomaly를 결합."""
    raise NotImplementedError


def rank(parcels, team_count: int, deadline_hours: float):
    """자원제약 하 Top-N 필지와 선정 사유 반환."""
    raise NotImplementedError
