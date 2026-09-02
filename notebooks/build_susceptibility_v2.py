"""다년 침수 빈도 재생성 — 새 마스크, 그리고 GEE 메모리 한계를 우회하는 분할 수출.

두 가지 문제를 동시에 푼다.

**1. 옛 마스크가 필지의 38.5%를 비워 두었다.**
경사 5도 미만 & HAND 20m 미만 조건이 31.8%를 잘랐고, 20m 격자 미포착이 9.8%를 더 잘랐다.
`gee.analysis_mask()` 기본값은 이미 경사 20도 / HAND 미적용으로 고쳤으므로
래스터를 다시 뽑기만 하면 된다. 격자 미포착은 `zonal.parcel_means_v2` 의 대표점 표본으로 메운다.

**2. 관측 21건을 한 그래프에 담으면 GEE 가 거부한다.**
    User memory limit exceeded (400)
관측마다 day-of-year 정합 baseline(median + MAD)을 만드는데, MAD 는 컬렉션을 두 번 훑으므로
메모리를 많이 쓴다. 무료 티어가 restricted mode 로 내려간 뒤에는 4건짜리도 통과하지 못했다.
실측 결과 **2건까지는 통과**한다.

따라서 관측을 2건씩 끊어 `n_flag`/`n_obs` 만 받아 오고 **합산은 로컬에서 한다.**
빈도는 GEE 가 아니라 여기서 계산한다. 부분 파일이 남아 있으면 건너뛰므로
중간에 끊겨도 이어받을 수 있다.

실행
    python notebooks/build_susceptibility_v2.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.features import susceptibility as sus, zonal  # noqa: E402
from src.rs import export, gee  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
COV_DIR = REPO_ROOT / "data" / "processed" / "cov"
PART_DIR = COV_DIR / "freq_parts"
OUT = REPO_ROOT / "data" / "processed" / "features" / "parcel_susceptibility.parquet"

ORBIT = 127
WET_MIN_RAIN3D = 40.0
DRY_MAX_RAIN3D = 5.0
SCALE_M = 20
# GEE 가 견디는 한 그래프당 관측 수. 4건은 User memory limit exceeded 로 거부됐다.
CHUNK = 2


def export_parts(aoi, bounds, tag: str, passes: pd.DataFrame) -> list[Path]:
    PART_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    n_chunk = (len(passes) + CHUNK - 1) // CHUNK
    for i in range(n_chunk):
        part = passes.iloc[i * CHUNK : (i + 1) * CHUNK]
        path = PART_DIR / f"{tag}_p{i:02d}.tif"
        paths.append(path)
        if path.exists():
            print(f"  [{tag} {i+1}/{n_chunk}] 있음 — 건너뜀")
            continue
        dates = ", ".join(str(d)[:10] for d in part["date"])
        print(f"  [{tag} {i+1}/{n_chunk}] {dates}")
        export.download_image(
            sus.flood_frequency_image(aoi, part, ORBIT), bounds, path,
            scale=SCALE_M, tile_deg=0.25, crs="EPSG:5179",
            band_names=["n_flag", "n_obs", "freq"],
        )
    return paths


def combine(paths: list[Path], out_path: Path) -> Path:
    """부분 래스터의 n_flag / n_obs 를 더하고 빈도를 다시 계산한다."""
    flag = obs = None
    profile = None
    for path in paths:
        with rasterio.open(path) as src:
            bands = zonal.band_index(src)
            f = src.read(bands["n_flag"]).astype(np.float32)
            o = src.read(bands["n_obs"]).astype(np.float32)
            if flag is None:
                flag, obs, profile = np.zeros_like(f), np.zeros_like(o), src.profile
            elif f.shape != flag.shape:
                raise ValueError(f"{path.name} 격자 불일치 {f.shape} != {flag.shape}")
        flag += np.nan_to_num(f)
        obs += np.nan_to_num(o)

    freq = np.divide(flag, obs, out=np.zeros_like(flag), where=obs > 0)
    profile.update(count=3, dtype="float32", compress="deflate", tiled=True, predictor=2)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(flag, 1)
        dst.write(obs, 2)
        dst.write(freq, 3)
        dst.descriptions = ("n_flag", "n_obs", "freq")
    covered = (obs > 0).mean() * 100
    print(f"  -> {out_path.name}  관측이 있는 화소 {covered:.1f}%")
    return out_path


def main() -> None:
    gee.init()
    aoi = gee.chungnam_aoi()
    bounds = tuple(gpd.read_file(REPO_ROOT / "data" / "aoi" / "chungnam_boundary.geojson").total_bounds)

    parcels = gpd.read_parquet(
        REPO_ROOT / "data" / "processed" / "farmmap" / "chungnam_2021.parquet",
        columns=["farmmap_id", "class_nm", "sgg_nm", "area_m2", "geometry"],
    ).reset_index(drop=True)
    print(f"필지 {len(parcels):,}개")

    sets = {
        "wet": sus.load_passes(ORBIT, wet_min_rain3d=WET_MIN_RAIN3D),
        "dry": sus.load_passes(ORBIT, dry_max_rain3d=DRY_MAX_RAIN3D),
    }
    print(f"젖은 관측 {len(sets['wet'])}건 | 마른 관측 {len(sets['dry'])}건 (관측 {CHUNK}건씩 분할)")

    index = None
    frames = []
    for tag, passes in sets.items():
        print(f"\n=== {tag} ===")
        merged = COV_DIR / f"freq_{tag}.tif"
        if not merged.exists():
            parts = export_parts(aoi, bounds, tag, passes)
            combine(parts, merged)
        if index is None:
            print("필지 인덱스 래스터화...")
            index = zonal.build_index(merged, parcels)
        stats = zonal.parcel_means_v2(merged, parcels, names=["n_flag", "n_obs", "freq"], index=index)
        got = stats["n_obs"].fillna(0) > 0
        print(f"  값이 있는 필지 {got.mean()*100:.1f}% "
              f"(면적집계 {(stats['method']=='area').mean()*100:.1f}% / "
              f"점표본 {(stats['method']=='point').mean()*100:.1f}%)")
        frames.append(stats.rename(columns={c: f"{tag}_{c}" for c in stats.columns}))

    out = pd.concat(frames, axis=1)
    for col in ("farmmap_id", "class_nm", "sgg_nm", "area_m2"):
        out[col] = parcels[col].to_numpy()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT)

    print(f"\n{len(out):,} 필지 -> {OUT}")
    ok = out[out["wet_n_obs"].fillna(0) > 0]
    print(f"침수빈도가 산출된 필지 {len(ok):,} ({len(ok)/len(out)*100:.1f}%)")
    print("\n[분류별 평균 침수빈도]")
    print(ok.groupby("class_nm", observed=True)[["wet_freq", "dry_freq"]].mean().round(3).to_string())
    print("\n대조: dry_freq 가 높으면 그 필지의 신호는 강우와 무관한 오탐이다.")
    print(f"  wet_freq >= 0.5 필지: {(ok['wet_freq'] >= 0.5).sum():,}")
    print(f"  그중 dry_freq >= 0.3: {((ok['wet_freq'] >= 0.5) & (ok['dry_freq'] >= 0.3)).sum():,}")


if __name__ == "__main__":
    main()
