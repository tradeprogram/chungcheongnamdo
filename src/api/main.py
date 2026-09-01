"""FastAPI 오케스트레이션.

AquaGuard의 응답계약을 그대로 따른다.
모든 엔드포인트는 status / fallback_tier / data / warnings / provenance 를 반환한다.

엔드포인트 계획
    GET  /events                  사건 목록 (Golden Event 포함)
    GET  /events/{id}/summary     사건 요약
    GET  /events/{id}/forecast    T-48 / T-24 필지 위험
    GET  /events/{id}/observed    SAR 관측 침수
    GET  /fields/{field_id}       필지 상세와 evidence
    POST /scenario                team / pump / deadline What-if
    POST /agent/ask               Tool-grounded 질의

TODO(P1): 응답 스키마부터 고정하고 mock data로 계약을 먼저 검증
"""

from __future__ import annotations
