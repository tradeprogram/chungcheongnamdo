"""Google Earth Engine 공통 헬퍼.

프로젝트: MOFOMAI (`gen-lang-client-0419682396`)

주의 — **GEE의 시간 필터는 UTC다.**
충남을 덮는 orbit 134 DESC는 06:32 KST 통과이므로 UTC로는 **전날 21:32**다.
`filterDate("2025-07-19", "2025-07-20")` 으로는 07-19 06:32 KST 영상이 잡히지 않는다.
KST 기준으로 생각할 때는 반드시 `kst_window()` 를 거칠 것.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from functools import lru_cache
from pathlib import Path

import ee
import geopandas as gpd

PROJECT = os.environ.get("EE_PROJECT", "gen-lang-client-0419682396")
REPO_ROOT = Path(__file__).resolve().parents[2]

S1_GRD = "COPERNICUS/S1_GRD"
GSW = "JRC/GSW1_4/GlobalSurfaceWater"
MERIT_HYDRO = "MERIT/Hydro/v1_0_1"
COP_DEM = "COPERNICUS/DEM/GLO30_2024_1"
WORLDCOVER = "ESA/WorldCover/v200/2021"

SPECKLE_RADIUS_M = 50


def init(project: str = PROJECT) -> None:
    ee.Initialize(project=project)


def kst_window(start_kst: str, end_kst: str) -> tuple[str, str]:
    """KST 날짜 구간을 GEE가 쓰는 UTC 구간으로 변환한다."""
    fmt = "%Y-%m-%d" if len(start_kst) == 10 else "%Y-%m-%dT%H:%M"
    to_utc = lambda s: (dt.datetime.strptime(s, fmt) - dt.timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M")
    return to_utc(start_kst), to_utc(end_kst)


@lru_cache(maxsize=1)
def chungnam_aoi(simplify_deg: float = 0.002) -> ee.Geometry:
    """충남 AOI. 요청 크기를 줄이기 위해 약간 단순화한다."""
    gdf = gpd.read_file(REPO_ROOT / "data" / "aoi" / "chungnam_boundary.geojson")
    geom = gdf.geometry.union_all().simplify(simplify_deg)
    fc = json.loads(gpd.GeoSeries([geom], crs=4326).to_json())
    return ee.Geometry(fc["features"][0]["geometry"])


def s1_collection(
    aoi: ee.Geometry,
    start_utc: str,
    end_utc: str,
    rel_orbit: int | None = None,
    platform: str | None = None,
    speckle_m: int = SPECKLE_RADIUS_M,
) -> ee.ImageCollection:
    """분석준비된 S1 GRD 컬렉션. 날짜는 UTC.

    COPERNICUS/S1_GRD는 thermal noise 제거·radiometric calibration·terrain correction이
    이미 적용된 컬렉션이다. SNAP 전처리를 중복 수행하지 않는다.
    speckle 완화용 focal median만 추가한다.
    """
    col = (
        ee.ImageCollection(S1_GRD)
        .filterBounds(aoi)
        .filterDate(start_utc, end_utc)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
    )
    if rel_orbit is not None:
        col = col.filter(ee.Filter.eq("relativeOrbitNumber_start", rel_orbit))
    if platform is not None:
        col = col.filter(ee.Filter.eq("platform_number", platform))
    return col.map(
        lambda im: ee.Image(
            im.select(["VV", "VH"])
            .focal_median(speckle_m, "circle", "meters")
            .copyProperties(im, ["system:time_start", "relativeOrbitNumber_start", "platform_number"])
        )
    )


def analysis_mask(max_slope_deg: float = 5.0, max_hand_m: float = 20.0) -> ee.Image:
    """영구수역·급경사·고지대를 제외한 분석 유효역."""
    permanent = ee.Image(GSW).select("occurrence").unmask(0).gte(50)
    hand = ee.Image(MERIT_HYDRO).select("hnd")
    slope = ee.Terrain.slope(ee.ImageCollection(COP_DEM).mosaic().select("DEM"))
    return permanent.Not().And(slope.lt(max_slope_deg)).And(hand.lt(max_hand_m)).rename("valid")


def cropland_mask() -> ee.Image:
    """WorldCover 농경지(class 40).

    **논과 밭을 구분하지 못한다.** 논 담수와 침수를 분리하려면 팜맵 필지 속성이 필요하다.
    """
    return ee.Image(WORLDCOVER).select("Map").eq(40).rename("cropland")


def area_km2(mask: ee.Image, aoi: ee.Geometry, scale: int = 50) -> float:
    value = (
        mask.rename("m")
        .multiply(ee.Image.pixelArea())
        .reduceRegion(ee.Reducer.sum(), aoi, scale, maxPixels=1e10)
        .get("m")
        .getInfo()
    )
    return (value or 0) / 1e6


def pass_inventory(col: ee.ImageCollection) -> list[dict]:
    """컬렉션의 통과 목록을 KST 기준으로 반환."""
    feats = col.map(
        lambda im: ee.Feature(
            None,
            {
                "kst": ee.Date(im.get("system:time_start")).advance(9, "hour").format("YYYY-MM-dd HH:mm"),
                "orbit": im.get("relativeOrbitNumber_start"),
                "platform": im.get("platform_number"),
            },
        )
    ).getInfo()["features"]
    return sorted((f["properties"] for f in feats), key=lambda p: p["kst"])
