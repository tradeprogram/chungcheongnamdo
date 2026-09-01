"""사전예측 모델용 공변량 — 지형(정적)과 선행강우(사건별).

두 종류를 분리해서 만든다.
    static_image()   지형·수문. 사건과 무관하므로 한 번만 내려받는다.
    rainfall_image() 사건 시각 기준 선행강우 누적. 사건마다 다르다.

지형은 MERIT Hydro 를 주로 쓴다. hnd(HAND, 최근접 하도 위 높이)와 upa(상류 유역면적)가
이미 계산돼 있어 홍수 문제에 그대로 맞는다. TWI 는 upa 와 slope 로 근사한다.

강우는 ERA5-Land daily (약 11km) 를 쓴다. **모델 feature 로는 임시다.**
KMA 실관측·예보로 교체해야 한다 (인증키 발급 필요). ERA5는 격자가 굵어
국지 집중호우의 공간 변동을 과소평가하므로, 이 상태의 모델은
"필지가 같은 강우 아래 어디가 먼저 잠기는가"를 지형으로 설명하는 쪽에 가깝다.
"""

from __future__ import annotations

import datetime as dt

import ee

ERA5_DAILY = "ECMWF/ERA5_LAND/DAILY_AGGR"
PRECIP_BAND = "total_precipitation_sum"  # m/day
MERIT = "MERIT/Hydro/v1_0_1"
COP_DEM = "COPERNICUS/DEM/GLO30_2024_1"


STREAM_UPA_KM2 = 1.0  # 상류 유역면적 1km² 이상을 하도로 본다


def terrain_slope() -> ee.Image:
    """경사 (도).

    **주의 — ImageCollection.mosaic() 결과에 ee.Terrain.slope 를 그냥 걸면 안 된다.**
    mosaic 은 기본 투영(1도 격자)을 갖기 때문에 경사가 사실상 0으로 계산된다
    (충남 p50 이 0.03도로 나온다). 원본 projection 을 다시 붙여야 한다.
    이 실수는 slope < 5도 같은 마스크를 항상 참으로 만들어 조용히 무력화한다.
    """
    col = ee.ImageCollection(COP_DEM)
    proj = ee.Image(col.first()).select("DEM").projection()
    dem = col.mosaic().select("DEM").setDefaultProjection(proj)
    return ee.Terrain.slope(dem).rename("slope")


def static_image() -> ee.Image:
    """지형·수문 정적 공변량.

    밴드
        elevation    표고 (m)
        slope        경사 (도)
        hand         HAND — 최근접 하도 위 높이 (m)
        upa          상류 유역면적 (km²)
        twi          지형습윤지수 ln(upa / tan(slope))
        dist_stream  최근접 하도까지 거리 (m)
    """
    merit = ee.Image(MERIT)
    dem = ee.ImageCollection(COP_DEM).mosaic().select("DEM").rename("elevation")
    slope = terrain_slope()

    upa = merit.select("upa").rename("upa")
    hand = merit.select("hnd").rename("hand")

    # TWI = ln(a / tan(beta)). 경사 0 에서 발산하지 않도록 하한을 둔다.
    tan_beta = slope.multiply(3.141592653589793 / 180).tan().max(0.001)
    twi = upa.max(0.0001).divide(tan_beta).log().rename("twi")

    # 하도까지 거리. MERIT 의 wth(하폭)는 하천 픽셀에만 정의돼 있어
    # 필지 feature 로 쓸 수 없다 (충남 필지 143만 중 771개만 유효했다).
    proj = merit.select("upa").projection()
    stream = merit.select("upa").gte(STREAM_UPA_KM2).reproject(proj)
    dist_stream = (
        stream.fastDistanceTransform(256).sqrt().multiply(proj.nominalScale()).rename("dist_stream")
    )

    return dem.addBands([slope, hand, upa, twi, dist_stream]).toFloat()


def rainfall_image(date_kst: str, windows: tuple[int, ...] = (1, 3, 7, 14, 30)) -> ee.Image:
    """관측일 기준 선행강우 누적 (mm).

    date_kst 당일을 포함한 N일 누적을 만든다.
    ERA5-Land daily 는 UTC 기준 집계이므로 KST 관측시각과 몇 시간 어긋날 수 있다.
    선행 1일 값은 그 오차에 민감하므로 3일 이상 창을 주 feature 로 쓴다.
    """
    end = dt.date.fromisoformat(date_kst)
    bands = []
    for days in windows:
        start = end - dt.timedelta(days=days - 1)
        total = (
            ee.ImageCollection(ERA5_DAILY)
            .filterDate(start.isoformat(), (end + dt.timedelta(days=1)).isoformat())
            .select(PRECIP_BAND)
            .sum()
            .multiply(1000)  # m -> mm
            .rename(f"rain{days}d")
        )
        bands.append(total)
    return ee.Image.cat(bands).toFloat()
