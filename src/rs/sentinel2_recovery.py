"""Sentinel-2 기반 생육 회복 모니터링.

S2는 메인 flood sensor가 아니라 recovery sensor다.

    S2 L2A -> cloud/shadow mask -> NDVI/EVI/NDMI
           -> 동일 작물, 동일 생육시기 baseline -> robust anomaly
           -> event 후 7~21일 회복곡선
           -> 정상 회복 / 회복지연 / 심각이상

구름으로 광학 관측이 없으면 S1 VH/VV 시계열을 fallback으로 쓰고
응답의 fallback_tier를 B로 낮춘다.

TODO(P2, Should): 사건 후 회복지연 지도 생성
"""

from __future__ import annotations


def compute_indices(scene):
    """NDVI / EVI / NDMI 산출."""
    raise NotImplementedError


def recovery_anomaly(parcels, event_date, horizon_days: int = 21):
    """same-season baseline 대비 robust z-score와 회복 분류."""
    raise NotImplementedError
