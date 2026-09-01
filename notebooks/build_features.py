"""사전예측 모델용 feature table 생성.

    field_event_stats.parquet  (관측 라벨)
      + 지형 정적 공변량 (필지별 1회)
      + 사건별 선행강우
    -> data/processed/features/field_event_features.parquet

지형·강우는 필지보다 격자가 훨씬 굵으므로 대표점 샘플링을 쓴다 (`zonal.sample_points`).
라벨(z-score 20m)만 면적 집계다.

실행
    python notebooks/build_features.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.features import covariates, zonal  # noqa: E402
from src.rs import export, gee  # noqa: E402
from notebooks.build_event_labels import EVENTS  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
RASTER_DIR = REPO_ROOT / "data" / "processed" / "cov"
STATS = REPO_ROOT / "data" / "processed" / "features" / "field_event_stats.parquet"
OUT = REPO_ROOT / "data" / "processed" / "features" / "field_event_features.parquet"

STATIC_SCALE_M = 30
RAIN_SCALE_M = 1000
EXPORT_CRS = "EPSG:5179"


def main() -> None:
    gee.init()
    bounds = tuple(gpd.read_file(REPO_ROOT / "data" / "aoi" / "chungnam_boundary.geojson").total_bounds)

    parcels = gpd.read_parquet(
        REPO_ROOT / "data" / "processed" / "farmmap" / "chungnam_2021.parquet",
        columns=["farmmap_id", "class_nm", "sgg_nm", "area_m2", "geometry"],
    ).reset_index(drop=True)
    pts = parcels.geometry.representative_point()
    xs, ys = pts.x.to_numpy(), pts.y.to_numpy()
    print(f"필지 {len(parcels):,}개 대표점")

    # --- 정적 지형 ---------------------------------------------------------
    static_path = RASTER_DIR / "static_terrain.tif"
    if not static_path.exists():
        print("\n=== 지형 공변량 ===")
        export.download_image(
            covariates.static_image(), bounds, static_path,
            scale=STATIC_SCALE_M, tile_deg=0.25, crs=EXPORT_CRS,
        )
    static = zonal.sample_points(static_path, xs, ys)
    static["farmmap_id"] = parcels["farmmap_id"].to_numpy()
    print("지형 컬럼:", [c for c in static.columns if c != "farmmap_id"])
    print(static.drop(columns="farmmap_id").describe().round(2).to_string())

    # --- 사건별 강우 -------------------------------------------------------
    rain_frames = []
    for event_id, date_kst, _orbit, _plat, _base in EVENTS:
        rain_path = RASTER_DIR / f"rain_{event_id}.tif"
        if not rain_path.exists():
            print(f"\n=== 선행강우 {event_id} ({date_kst}) ===")
            export.download_image(
                covariates.rainfall_image(date_kst), bounds, rain_path,
                scale=RAIN_SCALE_M, tile_deg=0.5, crs=EXPORT_CRS,
            )
        rain = zonal.sample_points(rain_path, xs, ys)
        rain["farmmap_id"] = parcels["farmmap_id"].to_numpy()
        rain["event_id"] = event_id
        rain_frames.append(rain)
        cols = [c for c in rain.columns if c.startswith("rain")]
        print(f"  {event_id}: " + ", ".join(f"{c}={rain[c].mean():.1f}mm" for c in cols))

    rain_all = pd.concat(rain_frames, ignore_index=True)

    # --- 결합 -------------------------------------------------------------
    stats = pd.read_parquet(STATS)
    df = stats.merge(static, on="farmmap_id", how="left").merge(
        rain_all, on=["farmmap_id", "event_id"], how="left"
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT)

    print(f"\n{len(df):,} 행 x {df.shape[1]} 컬럼 -> {OUT}")
    print("컬럼:", list(df.columns))


if __name__ == "__main__":
    main()
