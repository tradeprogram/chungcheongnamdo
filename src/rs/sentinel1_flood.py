"""Sentinel-1 SAR 기반 실제 침수 탐지 — 이 프로젝트의 메인 센서.

출처: Waterside Guard의 S1 수집·변화탐지 패턴 이식.

파이프라인
    S1 GRD VV/VH
      -> 동일 orbit / 관측 geometry 필터
      -> radiometric / terrain corrected backscatter
      -> event 이전 baseline (최근 정상영상 + 동일계절 3년 median/MAD)
      -> event 직후 영상
      -> dVV / dVH / log-ratio
      -> HAND, slope, permanent water, parcel mask
      -> 필지 단위 flood evidence
      -> multi-feature classifier 또는 adaptive threshold
      -> parcel flood probability + confidence

주의
- 논은 관리된 수면과 식생이 공존하므로 단순 low-backscatter threshold는 오탐한다.
  논/밭/시설을 별도 strata로 분리하고 same-season baseline, 강우, HAND를 함께 쓴다.
- 결과는 최대 침수가 아니라 관측시점 침수로 표기한다.
- GEE 분석준비 컬렉션을 쓰는 경우 SNAP 전처리를 중복 수행하지 않는다.

TODO(P1): baseline -> event 영상 -> log-ratio -> 필지 집계까지 1개 event 완주
"""

from __future__ import annotations


def build_baseline(aoi, event_date, years: int = 3):
    """동일 계절 median/MAD baseline 구축."""
    raise NotImplementedError


def detect_flood(aoi, event_date):
    """event 전후 backscatter 변화로 flood evidence raster 생성."""
    raise NotImplementedError


def aggregate_to_parcels(flood_raster, parcels):
    """필지별 flood_fraction 과 confidence 집계."""
    raise NotImplementedError
