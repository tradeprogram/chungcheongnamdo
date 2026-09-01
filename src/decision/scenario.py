"""자원 및 강우 시나리오 비교 (What-if).

허용하는 시나리오
    - 예상강우 P10 / P50 / P90
    - 현장팀 5 / 10 / 20
    - 이동식 펌프 수량
    - 대응 시작시각 3h / 6h 지연
    - 우선순위 정책: 최대피해 방지 vs 취약농가 우선 vs 이동거리 최소
    - 특정 배수시설 가동 가능 여부

금지하는 주장
    펌프를 3대 늘리면 피해액이 18.7% 줄어든다
    -> 검증된 수리모형 없이 인과효과를 말하지 않는다.

대신 직접 계산 가능한 운영지표만 낸다
    펌프 3대에서 6대로 늘렸을 때 6시간 내 대응 가능한 고위험 필지 면적 42% -> 61%

TODO(P2): team_count 하나만 먼저 구현 (5/10/20 coverage)
"""

from __future__ import annotations


def run_scenario(parcels, team_count: int, pump_count: int, deadline_hours: float):
    """coverage(고위험 면적 비율), travel-hour, 미대응 필지 수 반환."""
    raise NotImplementedError
