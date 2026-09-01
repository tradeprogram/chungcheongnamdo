"""팜맵 필지 단위 feature table 생성.

픽셀 예측을 행정 단위(필지)로 바꾸는 지점. 산출물은 field_event_features.parquet.

Feature 구성
    Dynamic : forecast rain 1/3/6/24h, antecedent rain 1/3/7d,
              soil moisture proxy, recent S1 wetness, season / crop stage
    Static  : elevation, slope, HAND, TWI, distance to stream,
              배수시설 거리 및 용량 proxy, soil drainage class,
              historical SAR flood frequency
    Context : 주변 필지 침수 이력, crop / field type, 유역

TODO(P0): 팜맵 충남 AOI -> GeoParquet, DEM -> slope/HAND/TWI, KMA 강우 결합
"""

from __future__ import annotations


def load_farmmap(aoi_path):
    """팜맵 필지 로드 및 CRS 통일."""
    raise NotImplementedError


def terrain_features(dem_path, parcels):
    """slope / HAND / TWI / 하천거리."""
    raise NotImplementedError


def rainfall_features(event, parcels):
    """KMA 관측 및 예보 강우를 필지에 결합."""
    raise NotImplementedError


def build_feature_table(event, parcels):
    """event x parcel 단위 학습 및 추론용 테이블."""
    raise NotImplementedError
