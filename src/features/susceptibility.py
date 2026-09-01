"""상습 침수 취약도 — 다년 SAR 관측에서 필지별 침수 빈도를 만든다.

실험 05 결론: 사건 하나의 필지 침수를 예측하는 것은 판별력이 없다(ROC-AUC 0.50~0.55).
반면 **젖은 관측 여러 건에서 반복 침수되는 필지**는 지형으로 판별된다(0.63).
관측을 2건에서 10건 이상으로 늘려 라벨을 안정화하는 것이 이 모듈의 목적이다.
전략문서의 "historical SAR flood frequency" feature 가 이것이다.

설계상 중요한 두 가지.

**1. 빈도를 GEE 안에서 합산한다.**
사건마다 z-score 래스터(약 470MB)를 내려받으면 10건에 4.7GB다.
픽셀 단위로 flag 를 합산한 뒤 한 장만 내보낸다.
필지 단위 임계값을 사건마다 적용하지 않으므로 임계 선택의 자의성도 줄어든다.

**2. baseline 을 생육시기로 맞춘다.**
실험 02에서 6월 영상을 7월 baseline 과 비교하면 생육단계 차이가 이상치로 잡히는 것이 확인됐다.
따라서 각 통과일에 대해 **다른 연도의 같은 day-of-year +-15일** 영상만으로 baseline 을 만든다.
동일 relative orbit 이어야 입사각이 맞으므로 궤도도 고정한다.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import ee
import pandas as pd

from ..rs import gee, sentinel1_flood as s1f

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSES_CSV = REPO_ROOT / "data" / "reference" / "s1_passes_rainfall.csv"

DOY_WINDOW = 15
Z_THRESHOLD = 2.0
MIN_COVERAGE = 95.0


def load_passes(
    orbit: int = 127,
    wet_min_rain3d: float | None = None,
    dry_max_rain3d: float | None = None,
) -> pd.DataFrame:
    """통과 목록에서 젖은/마른 관측을 고른다."""
    df = pd.read_csv(PASSES_CSV, encoding="utf-8-sig")
    df = df[(df["rel_orbit"] == orbit) & (df["coverage_pct"] >= MIN_COVERAGE)].copy()
    df["date"] = pd.to_datetime(df["date"])
    if wet_min_rain3d is not None:
        df = df[df["rain3d"] >= wet_min_rain3d]
    if dry_max_rain3d is not None:
        df = df[df["rain3d"] <= dry_max_rain3d]
    return df.sort_values("date").reset_index(drop=True)


def doy_matched_baseline(
    aoi: ee.Geometry, date: dt.date, orbit: int, window: int = DOY_WINDOW
) -> tuple[ee.Image, ee.Image]:
    """같은 궤도, 다른 연도, 같은 시기(day-of-year +-window)의 median/MAD."""
    doy = int(date.strftime("%j"))
    col = (
        ee.ImageCollection(gee.S1_GRD)
        .filterBounds(aoi)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.eq("relativeOrbitNumber_start", orbit))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
        .filter(ee.Filter.calendarRange(max(doy - window, 1), min(doy + window, 366), "day_of_year"))
        .filter(ee.Filter.calendarRange(date.year, date.year, "year").Not())
        .map(lambda im: ee.Image(im.select(["VV", "VH"]).focal_median(gee.SPECKLE_RADIUS_M, "circle", "meters")))
    )
    median = col.median()
    mad = col.map(lambda im: im.subtract(median).abs()).median()
    return median, mad


def flood_frequency_image(
    aoi: ee.Geometry, passes: pd.DataFrame, orbit: int, z_threshold: float = Z_THRESHOLD
) -> ee.Image:
    """관측들에 걸친 픽셀 단위 침수 빈도.

    밴드
        n_flag   이중반사형으로 판정된 관측 수
        n_obs    유효 관측 수
        freq     n_flag / n_obs
    """
    valid = gee.analysis_mask()
    flag_sum = ee.Image(0)
    obs_sum = ee.Image(0)

    for row in passes.itertuples():
        date = row.date.date() if hasattr(row.date, "date") else dt.date.fromisoformat(str(row.date)[:10])
        median, mad = doy_matched_baseline(aoi, date, orbit)
        image = gee.s1_collection(
            aoi, *gee.kst_window(date.isoformat(), (date + dt.timedelta(days=1)).isoformat()), orbit
        ).mosaic()
        z = s1f.robust_z(image, median, mad)
        flag = z.select("VV").gt(z_threshold).And(z.select("VH").gt(z_threshold)).And(valid)
        observed = image.select("VV").mask().And(valid)
        flag_sum = flag_sum.add(flag.unmask(0))
        obs_sum = obs_sum.add(observed.unmask(0))

    freq = flag_sum.divide(obs_sum.max(1))
    return (
        flag_sum.rename("n_flag")
        .addBands(obs_sum.rename("n_obs"))
        .addBands(freq.rename("freq"))
        .toFloat()
    )
