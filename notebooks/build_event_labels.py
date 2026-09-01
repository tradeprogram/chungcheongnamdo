"""사건별 필지 단위 관측 라벨 생성.

산출: data/processed/features/field_event_stats.parquet  (필지 x 사건 long format)

사건 구성 — orbit 127 ASC 는 2021~2025 7월 중순~하순 통과를 모두 쓴다.
연도별 leave-one-year-out baseline 이므로 각 연도가 독립 표본이 된다.
관측 시점의 습윤 상태가 선행강우에 단조 반응한다는 것이 실험 03에서 확인됐다
(docs/40_experiments.md). 따라서 이 5개 연도는 "젖은 관측"과 "마른 관측"의
스펙트럼을 이루며, 그 자체가 학습 표본이 된다.

    2021-07-21  선행3일  3.6mm   (마름)
    2022-07-16  선행3일  4.7mm   (마름)
    2023-07-23  선행3일 65.7mm   (당일 18시까지 49.0mm — 가장 좋은 관측)
    2024-07-17  선행3일 39.0mm   (중간)
    2025-07-24  선행3일  4.0mm   (Golden Event이나 peak+7일, 이미 배수됨)
    2025-07-19  orbit 134, peak+1.6일 — 2025 사건을 담은 유일한 관측

주의 — 2025-07-19 은 궤도와 기준영상이 다르므로 orbit 127 사건들과
같은 축에서 비교하지 않는다. event_id 로 구분해 두고 모델에서 궤도를 feature 로 넣는다.

실행
    python notebooks/build_event_labels.py
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.features import zonal  # noqa: E402
from src.rs import export, gee, sentinel1_flood as s1f  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
RASTER_DIR = REPO_ROOT / "data" / "processed" / "z"
OUT = REPO_ROOT / "data" / "processed" / "features" / "field_event_stats.parquet"
SUMMARY = REPO_ROOT / "data" / "reference" / "field_event_summary.csv"

SCALE_M = 20
EXPORT_CRS = "EPSG:5179"

# event_id, KST 날짜, 궤도, 위성, baseline 연도 (None 이면 사건 후 평상상태 기준)
EVENTS = [
    ("o127_2021-07-21", "2021-07-21", 127, "A", [2022, 2023, 2024]),
    ("o127_2022-07-16", "2022-07-16", 127, "A", [2021, 2023, 2024]),
    ("o127_2023-07-23", "2023-07-23", 127, "A", [2021, 2022, 2024]),
    ("o127_2024-07-17", "2024-07-17", 127, "A", [2021, 2022, 2023]),
    ("o127_2025-07-24", "2025-07-24", 127, "A", [2021, 2022, 2023, 2024]),
    ("o134_2025-07-19", "2025-07-19", 134, "C", None),
]


def z_image(aoi, date_kst: str, orbit: int, platform: str, base_years: list[int] | None):
    if base_years is not None:
        med, mad = s1f.same_season_baseline(aoi, base_years, rel_orbit=orbit, platform=platform)
        next_day = (dt.date.fromisoformat(date_kst) + dt.timedelta(days=1)).isoformat()
        start, end = gee.kst_window(date_kst, next_day)
    else:
        ref = gee.s1_collection(aoi, *gee.kst_window("2025-08-07", "2025-08-31"), orbit, platform)
        med = ref.median()
        mad = ref.map(lambda im: im.subtract(med).abs()).median()
        start, end = gee.kst_window(f"{date_kst}T00:00", f"{date_kst}T23:59")

    image = gee.s1_collection(aoi, start, end, orbit, platform).mosaic()
    return s1f.robust_z(image, med, mad).rename(["zvv", "zvh"]).addBands(gee.analysis_mask()).toFloat()


def main() -> None:
    gee.init()
    aoi = gee.chungnam_aoi()
    bounds = tuple(gpd.read_file(REPO_ROOT / "data" / "aoi" / "chungnam_boundary.geojson").total_bounds)
    print("AOI bounds(4326):", [round(v, 3) for v in bounds])

    parcels = gpd.read_parquet(
        REPO_ROOT / "data" / "processed" / "farmmap" / "chungnam_2021.parquet",
        columns=["farmmap_id", "class_nm", "sgg_nm", "area_m2", "geometry"],
    ).reset_index(drop=True)
    print(f"필지 {len(parcels):,}개")

    index = None
    frames = []
    for event_id, date_kst, orbit, platform, base_years in EVENTS:
        print(f"\n=== {event_id} ===")
        raster = RASTER_DIR / f"{event_id}.tif"
        if not raster.exists():
            export.download_image(
                z_image(aoi, date_kst, orbit, platform, base_years),
                bounds, raster, scale=SCALE_M, tile_deg=0.25, crs=EXPORT_CRS,
            )
        else:
            print(f"  기존 파일 사용: {raster.name}")

        if index is None:
            print("  필지 인덱스 래스터화...")
            index = zonal.build_index(raster, parcels)

        stats = zonal.parcel_stats(raster, parcels, index=index)
        stats["event_id"] = event_id
        stats["obs_date"] = date_kst
        stats["rel_orbit"] = orbit
        frames.append(stats)
        ok = stats[stats["n_valid"] >= 3]
        print(f"  유효필지 {len(ok):,} | 논 double_fraction 평균 "
              f"{ok.loc[ok['class_nm'] == '논', 'double_fraction'].mean():.3f}")

    out = pd.concat(frames, ignore_index=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT)

    summary = (
        out[out["n_valid"] >= 3]
        .groupby(["event_id", "class_nm"])
        .agg(
            parcels=("farmmap_id", "size"),
            mean_double=("double_fraction", "mean"),
            mean_open=("open_fraction", "mean"),
            pct_double_ge50=("double_fraction", lambda s: round((s >= 0.5).mean() * 100, 2)),
        )
        .round(4)
        .reset_index()
    )
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(SUMMARY, index=False, encoding="utf-8-sig")

    print(f"\n{len(out):,} 행 -> {OUT}")
    print("\n[사건 x 분류 요약: double_fraction >= 0.5 필지 비율 %]")
    print(summary.pivot(index="event_id", columns="class_nm", values="pct_double_ge50").to_string())


if __name__ == "__main__":
    main()
