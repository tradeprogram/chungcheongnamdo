"""판독 해상도를 20m -> 10m 로 올린다.

문제
팜맵 필지 평균 면적은 약 1,500 m² (39m x 39m) 다. 20m 격자에서는 4픽셀에 불과해
9.8% 는 격자에 아예 잡히지 않고, 작은 필지는 표본이 부족해 값을 낼 수 없었다.
Sentinel-1 GRD 는 원래 10m 이므로 20m 는 우리가 스스로 버린 해상도였다.

같이 바꾸는 것
분석 마스크(valid 밴드)를 래스터에 굽지 않는다. 경사 5도 / HAND 20m 조건이
농경지의 32%를 잘라내고 있었는데, 이 조건은 일반 홍수 매핑용이지 농경지 판독용이 아니다.
유효성은 로컬에서 zvv/zvh 가 유한한지로 판단하고, 경사는 필지 속성으로 따로 붙여 표기한다.

실행
    python notebooks/export_10m.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.rs import export, gee, sentinel1_flood as s1f  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "data" / "processed" / "z10"
SCALE_M = 10
TILE_DEG = 0.12  # 10m 에서 요청당 크기가 상한을 넘지 않도록 20m 때보다 잘게 쓴다

# 화면이 쓰는 사건 전부. 일부만 10m 로 바꾸면 안 된다 —
# 필지 상세는 화소 수를 하나만 표시하므로, 사건마다 해상도가 다르면
# 20m 사건의 값에 10m 의 화소 수가 붙어 근거를 잘못 말하게 된다.
EVENTS = [
    ("o134_2025-07-19", "2025-07-19", 134, "C", None),
    ("o127_2025-07-24", "2025-07-24", 127, "A", [2021, 2022, 2023, 2024]),
    ("o127_2024-07-17", "2024-07-17", 127, "A", [2021, 2022, 2023, 2025]),
    ("o127_2023-07-23", "2023-07-23", 127, "A", [2021, 2022, 2024]),
    ("o127_2022-07-16", "2022-07-16", 127, "A", [2021, 2023, 2024]),
    ("o127_2021-07-21", "2021-07-21", 127, "A", [2022, 2023, 2024]),
]


def z_image(aoi, date_kst: str, orbit: int, platform: str, base_years: list[int] | None):
    """zvv, zvh 두 밴드만. valid 는 굽지 않는다."""
    import datetime as dt

    if base_years is not None:
        med, mad = s1f.same_season_baseline(aoi, base_years, rel_orbit=orbit, platform=platform)
        nxt = (dt.date.fromisoformat(date_kst) + dt.timedelta(days=1)).isoformat()
        start, end = gee.kst_window(date_kst, nxt)
    else:
        ref = gee.s1_collection(aoi, *gee.kst_window("2025-08-07", "2025-08-31"), orbit, platform)
        med = ref.median()
        mad = ref.map(lambda im: im.subtract(med).abs()).median()
        start, end = gee.kst_window(f"{date_kst}T00:00", f"{date_kst}T23:59")

    image = gee.s1_collection(aoi, start, end, orbit, platform).mosaic()
    return s1f.robust_z(image, med, mad).rename(["zvv", "zvh"]).toFloat()


def main() -> None:
    gee.init()
    aoi = gee.chungnam_aoi()
    import geopandas as gpd
    aoi_geom = gpd.read_file(
        REPO_ROOT / "data" / "aoi" / "chungnam_boundary.geojson").geometry.union_all()
    bounds = tuple(gpd.read_file(REPO_ROOT / "data" / "aoi" / "chungnam_boundary.geojson").total_bounds)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for event_id, date_kst, orbit, platform, base_years in EVENTS:
        out = OUT_DIR / f"{event_id}.tif"
        if out.exists():
            print(f"[skip] {out.name} 이미 있음 ({out.stat().st_size/1e6:.0f} MB)")
            continue
        print(f"\n=== {event_id} @ {SCALE_M}m ===")
        export.download_image(
            z_image(aoi, date_kst, orbit, platform, base_years),
            bounds, out, scale=SCALE_M, tile_deg=TILE_DEG, crs="EPSG:5179",
            band_names=["zvv", "zvh"], aoi_geom=aoi_geom,
        )


if __name__ == "__main__":
    main()
