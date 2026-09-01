"""Sentinel-1 SAR 기반 침수 탐지 — 이 프로젝트의 메인 센서.

파이프라인
    S1 GRD VV/VH (분석준비 컬렉션)
      -> 동일 relative orbit / 동일 위성 필터
      -> speckle 완화 (focal median)
      -> 기준영상: 동일 계절 다년 median/MAD  또는  사건 후 평상상태
      -> 사건영상과의 차이 -> robust z-score
      -> 영구수역·경사·HAND 마스크
      -> 침수 후보

**두 방향을 모두 본다.**
    개방수면형 (z < 0): 물이 드러난 면. 후방산란 감소.
    이중반사형 (z > 0): 식생 캐노피 아래 침수. 줄기-수면 double bounce로 후방산란 증가.
논은 후자가 지배적이므로 감소 방향만 보면 침수를 놓친다.

**중요 — 이 모듈만으로 논 침수를 판정할 수 없다.**
notebooks/exp01_flood_change_detection.py 의 부정 대조군에서, 사건 17일 **전** 영상이
사건 후 영상보다 더 큰 이상치를 냈다. 한국 논의 담수·중간낙수 관리 주기가
침수 신호와 같은 대역에서 움직이기 때문이다. 자세한 결과와 함의는 docs/40_experiments.md 참조.
논/밭 분리(팜맵)와 시계열 지속성 판정 없이 이 산출물을 침수로 부르지 말 것.
"""

from __future__ import annotations

import ee

from . import gee

MAD_FLOOR_DB = 0.3  # MAD가 0에 가까운 안정 픽셀에서 z가 폭주하는 것을 막는다


def same_season_baseline(
    aoi: ee.Geometry,
    years: list[int],
    month_start: str = "07-01",
    month_end: str = "08-01",
    rel_orbit: int = 127,
    platform: str | None = None,
) -> tuple[ee.Image, ee.Image]:
    """동일 계절 다년 median 과 MAD.

    반드시 **동일 relative orbit**으로 쌓는다. 궤도가 다르면 입사각·관측 geometry가
    달라 후방산란이 직접 비교되지 않는다.
    """
    parts = [
        gee.s1_collection(aoi, f"{yr}-{month_start}", f"{yr}-{month_end}", rel_orbit, platform)
        for yr in years
    ]
    stack = parts[0]
    for p in parts[1:]:
        stack = stack.merge(p)
    stack = ee.ImageCollection(stack)

    median = stack.median()
    mad = stack.map(lambda im: im.subtract(median).abs()).median()
    return median, mad


def robust_z(image: ee.Image, median: ee.Image, mad: ee.Image) -> ee.Image:
    """band별 robust z-score (VV, VH)."""
    return image.subtract(median).divide(mad.max(MAD_FLOOR_DB))


def flood_candidates(
    z: ee.Image,
    mode: str = "both",
    z_threshold: float = 2.0,
    mask: ee.Image | None = None,
) -> ee.Image:
    """침수 후보 마스크.

    mode:
        "open"   개방수면형만 (z < -threshold)
        "double" 이중반사형만 (z > +threshold)
        "both"   둘 중 하나
    """
    zvv, zvh = z.select("VV"), z.select("VH")
    open_water = zvv.lt(-z_threshold).And(zvh.lt(-z_threshold))
    double_bounce = zvv.gt(z_threshold).And(zvh.gt(z_threshold))

    if mode == "open":
        out = open_water
    elif mode == "double":
        out = double_bounce
    elif mode == "both":
        out = open_water.Or(double_bounce)
    else:
        raise ValueError(f"unknown mode: {mode}")

    if mask is not None:
        out = out.And(mask)
    return out.rename("flood_candidate")


def aggregate_to_parcels(flood: ee.Image, parcels: ee.FeatureCollection, scale: int = 20) -> ee.FeatureCollection:
    """필지별 침수 후보 면적비율.

    팜맵 필지를 받아 flood_fraction 을 계산한다. 팜맵 확보 후 연결한다.
    """
    return flood.reduceRegions(collection=parcels, reducer=ee.Reducer.mean(), scale=scale)
