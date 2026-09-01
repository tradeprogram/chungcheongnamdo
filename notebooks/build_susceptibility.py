"""상습 침수 취약도 라벨·모델.

    젖은 관측 N건 -> 픽셀 침수 빈도 -> 필지 평균 빈도 = 라벨
    마른 관측 M건 -> 같은 방식의 오탐 빈도 (대조군)
    지형 feature -> LightGBM -> 시군 GroupKFold 검증

실험 05에서 관측 2건 일치 라벨로 ROC-AUC 0.629 를 얻었다.
관측 수를 늘려 라벨을 안정화하면 얼마나 개선되는지 확인한다.

실행
    python notebooks/build_susceptibility.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.features import susceptibility as sus, zonal  # noqa: E402
from src.rs import export, gee  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
COV_DIR = REPO_ROOT / "data" / "processed" / "cov"
OUT = REPO_ROOT / "data" / "processed" / "features" / "parcel_susceptibility.parquet"

ORBIT = 127
WET_MIN_RAIN3D = 40.0
DRY_MAX_RAIN3D = 5.0
SCALE_M = 20


def main() -> None:
    gee.init()
    aoi = gee.chungnam_aoi()
    bounds = tuple(gpd.read_file(REPO_ROOT / "data" / "aoi" / "chungnam_boundary.geojson").total_bounds)

    parcels = gpd.read_parquet(
        REPO_ROOT / "data" / "processed" / "farmmap" / "chungnam_2021.parquet",
        columns=["farmmap_id", "class_nm", "sgg_nm", "area_m2", "geometry"],
    ).reset_index(drop=True)
    print(f"필지 {len(parcels):,}개")

    wet = sus.load_passes(ORBIT, wet_min_rain3d=WET_MIN_RAIN3D)
    dry = sus.load_passes(ORBIT, dry_max_rain3d=DRY_MAX_RAIN3D)
    print(f"젖은 관측 {len(wet)}건 (rain3d>={WET_MIN_RAIN3D}mm) | 마른 관측 {len(dry)}건 (<={DRY_MAX_RAIN3D}mm)")

    index = None
    frames = []
    for tag, passes in (("wet", wet), ("dry", dry)):
        path = COV_DIR / f"freq_{tag}.tif"
        if not path.exists():
            print(f"\n=== {tag}: 관측 {len(passes)}건 ===")
            print(passes[["date", "sat", "rain1d", "rain3d"]].to_string(index=False))
            export.download_image(
                sus.flood_frequency_image(aoi, passes, ORBIT), bounds, path,
                scale=SCALE_M, tile_deg=0.25, crs="EPSG:5179",
                band_names=["n_flag", "n_obs", "freq"],
            )
        if index is None:
            print("  필지 인덱스 래스터화...")
            index = zonal.build_index(path, parcels)
        stats = zonal.parcel_means(path, parcels, index=index, names=["n_flag", "n_obs", "freq"])
        frames.append(stats.rename(columns={c: f"{tag}_{c}" for c in stats.columns}))

    out = pd.concat(frames, axis=1)
    for col in ("farmmap_id", "class_nm", "sgg_nm", "area_m2"):
        out[col] = parcels[col].to_numpy()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT)

    print(f"\n{len(out):,} 필지 -> {OUT}")
    ok = out[out["wet_n_obs"] >= 5]
    print(f"\n유효필지(관측 5건 이상) {len(ok):,}")
    print("\n[분류별 평균 침수빈도]")
    print(ok.groupby("class_nm")[["wet_freq", "dry_freq"]].mean().round(3).to_string())
    print("\n[wet_freq 분포]")
    print(ok["wet_freq"].describe(percentiles=[.5, .75, .9, .95, .99]).round(3).to_string())
    print("\n대조: dry_freq 가 높으면 그 필지의 신호는 강우와 무관한 오탐이다.")
    print(f"  wet_freq >= 0.5 필지: {(ok['wet_freq'] >= 0.5).sum():,}")
    print(f"  그중 dry_freq >= 0.3: {((ok['wet_freq'] >= 0.5) & (ok['dry_freq'] >= 0.3)).sum():,}")


if __name__ == "__main__":
    main()
