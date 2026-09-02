"""필지 판독 커버리지를 끌어올린 재집계.

기존 결과는 필지 1,434,057개 중 **61.3%가 판독 불가**였다. 원인은 둘이다.

  1) 분석 마스크 — 경사 5도 미만 & HAND 20m 미만 조건이 32.3%(462,953필지)를 잘랐다.
     농경지 경사 중앙값은 4.05도지만 밭은 6.16도, 90분위 15.41도다.
     즉 5도 기준은 밭의 절반 이상을 구조적으로 배제한다. 이 조건은 일반 홍수 매핑에서
     지형 왜곡을 피하려고 쓰는 것인데, 팜맵 필지 안에서 상태를 보고하는 목적에는 과하다.

  2) 격자 미포착 — 20m 격자에서 9.8%(140,279필지)가 픽셀을 하나도 배정받지 못했다.
     필지 평균 면적이 1,500 m²(39m x 39m)라 인접 필지에 밀리면 사라진다.

해결
  - 유효성은 zvv/zvh 가 유한한지로만 판단한다 (래스터의 valid 밴드 미사용)
  - 경사는 배제 조건이 아니라 **필지 속성**으로 붙인다. 20도 초과는 신뢰도 낮음으로 표기
  - 픽셀이 없는 필지는 대표점 한 곳을 읽고 method="point" 로 구분한다
  - 10m 래스터가 있으면 그것을 쓴다 (data/processed/z10)

실행
    python notebooks/build_event_labels_v2.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.features import zonal  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
Z20_DIR = REPO_ROOT / "data" / "processed" / "z"
Z10_DIR = REPO_ROOT / "data" / "processed" / "z10"
STATIC = REPO_ROOT / "data" / "processed" / "cov" / "static_terrain.tif"
OUT = REPO_ROOT / "data" / "processed" / "features" / "field_event_stats.parquet"
SUMMARY = REPO_ROOT / "data" / "reference" / "field_event_summary.csv"

# 경사가 이보다 크면 SAR 기하 왜곡으로 신뢰도가 떨어진다.
# Sentinel-1 IW 입사각이 30~45도이므로 20도까지는 판독 가능한 범위로 본다.
# 농경지의 97.5%가 이 안에 들어온다 (5도 기준은 57.6%였다).
STEEP_DEG = 20.0

EVENTS = ["o127_2021-07-21", "o127_2022-07-16", "o127_2023-07-23",
          "o127_2024-07-17", "o127_2025-07-24", "o134_2025-07-19"]


def raster_for(event_id: str) -> tuple[Path, int] | None:
    p10, p20 = Z10_DIR / f"{event_id}.tif", Z20_DIR / f"{event_id}.tif"
    if p10.exists():
        return p10, 10
    if p20.exists():
        return p20, 20
    return None


def main() -> None:
    parcels = gpd.read_parquet(
        REPO_ROOT / "data" / "processed" / "farmmap" / "chungnam_2021.parquet",
        columns=["farmmap_id", "class_nm", "sgg_nm", "emd_cd", "emd_nm", "area_m2", "geometry"],
    ).reset_index(drop=True)
    print(f"필지 {len(parcels):,}")

    pts = parcels.geometry.representative_point()
    terrain = zonal.sample_points(STATIC, pts.x.to_numpy(), pts.y.to_numpy(),
                                  names=["elevation", "slope", "hand", "upa", "twi", "dist_stream"])
    slope = terrain["slope"].to_numpy()
    print(f"경사 {STEEP_DEG:.0f}도 이하 필지 {(slope < STEEP_DEG).mean()*100:.1f}%")

    index_cache: dict[tuple[int, int], object] = {}
    frames = []
    for event_id in EVENTS:
        found = raster_for(event_id)
        if not found:
            print(f"[skip] {event_id} 래스터 없음")
            continue
        path, scale = found

        import rasterio
        with rasterio.open(path) as src:
            shape = (src.height, src.width)
        if shape not in index_cache:
            print(f"  인덱스 래스터화 {shape} ({scale}m)")
            index_cache[shape] = zonal.build_index(path, parcels)

        stats = zonal.parcel_stats_v2(path, parcels, index=index_cache[shape])
        stats["event_id"] = event_id
        stats["scale_m"] = scale
        stats["slope"] = slope
        stats["steep"] = slope >= STEEP_DEG
        frames.append(stats)

        ok = stats["n_valid"] > 0
        area = (stats["method"] == "area") & ok
        print(f"  {event_id} @{scale}m : 판독 {ok.mean()*100:5.1f}%  "
              f"(면적집계 {area.mean()*100:4.1f}% / 점표본 {(ok & ~area).mean()*100:4.1f}%)")

    out = pd.concat(frames, ignore_index=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT)

    usable = out[(out["n_valid"] > 0) & (~out["steep"])]
    summary = (
        usable.groupby(["event_id", "class_nm"], observed=True)
        .agg(parcels=("farmmap_id", "size"),
             mean_double=("double_fraction", "mean"),
             mean_open=("open_fraction", "mean"),
             pct_double_ge50=("double_fraction", lambda s: round((s >= 0.5).mean() * 100, 2)))
        .round(4).reset_index()
    )
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(SUMMARY, index=False, encoding="utf-8-sig")

    first = out[out["event_id"] == EVENTS[0]] if EVENTS[0] in set(out["event_id"]) else out
    print(f"\n{len(out):,}행 -> {OUT}")
    print(f"판독 가능 필지 비율 {(first['n_valid'] > 0).mean()*100:.1f}%"
          f" (급경사 제외 시 {((first['n_valid'] > 0) & ~first['steep']).mean()*100:.1f}%)")
    print("\n[사건 x 분류: double_fraction >= 0.5 필지 비율 %]")
    print(summary.pivot(index="event_id", columns="class_nm", values="pct_double_ge50").to_string())


if __name__ == "__main__":
    main()
