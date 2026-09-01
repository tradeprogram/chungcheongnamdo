"""실험 06 — 관측 기반 상습침수 지도의 신뢰성.

관측 15건이 충남 전역을 덮으므로, 취약도를 지형으로 **예측할 필요가 없다.**
관측된 침수 빈도 자체가 지도다. 모델은 "왜 여기가 상습인가"를 설명하는 보조일 뿐이다.

그렇다면 검증해야 할 것은 예측 정확도가 아니라 **지도의 재현성**이다.
관측 15건을 겹치지 않는 두 묶음으로 나눠 각각 빈도 지도를 만들고 서로 비교한다.
두 지도가 일치하면 그 지도는 잡음이 아니라 안정적인 무언가를 재고 있다.

집계 단위도 함께 본다. 배수개선사업의 의사결정 단위는 필지가 아니라 지구다.
읍면동 단위로 올리면 잡음이 평균화되어 순위가 더 안정될 것으로 기대한다.

실행
    python notebooks/exp06_split_half_reliability.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.features import susceptibility as sus, zonal  # noqa: E402
from src.rs import export, gee  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
COV_DIR = REPO_ROOT / "data" / "processed" / "cov"
OUT_CSV = REPO_ROOT / "data" / "reference" / "exp06_split_half.csv"
EMD_CSV = REPO_ROOT / "data" / "reference" / "emd_flood_frequency.csv"

ORBIT = 127
BANDS = ["n_flag", "n_obs", "freq"]
MIN_OBS = 5.0


def main() -> None:
    gee.init()
    aoi = gee.chungnam_aoi()
    bounds = tuple(gpd.read_file(REPO_ROOT / "data" / "aoi" / "chungnam_boundary.geojson").total_bounds)

    parcels = gpd.read_parquet(
        REPO_ROOT / "data" / "processed" / "farmmap" / "chungnam_2021.parquet",
        columns=["farmmap_id", "class_nm", "sgg_nm", "emd_cd", "emd_nm", "area_m2", "geometry"],
    ).reset_index(drop=True)

    wet = sus.load_passes(ORBIT, wet_min_rain3d=40.0)
    halves = {"half_a": wet.iloc[::2].reset_index(drop=True), "half_b": wet.iloc[1::2].reset_index(drop=True)}
    for tag, sub in halves.items():
        print(f"{tag}: {len(sub)}건  " + ", ".join(str(d.date()) for d in sub["date"]))

    index = None
    frames = {}
    for tag, sub in halves.items():
        path = COV_DIR / f"freq_{tag}.tif"
        if not path.exists():
            print(f"\n=== {tag} ===")
            export.download_image(
                sus.flood_frequency_image(aoi, sub, ORBIT), bounds, path,
                scale=20, tile_deg=0.25, crs="EPSG:5179", band_names=BANDS,
            )
        if index is None:
            print("  필지 인덱스 래스터화...")
            index = zonal.build_index(path, parcels)
        frames[tag] = zonal.parcel_means(path, parcels, index=index, names=BANDS)

    df = pd.DataFrame({
        "farmmap_id": parcels["farmmap_id"], "class_nm": parcels["class_nm"],
        "sgg_nm": parcels["sgg_nm"], "emd_cd": parcels["emd_cd"], "emd_nm": parcels["emd_nm"],
        "area_m2": parcels["area_m2"],
        "freq_a": frames["half_a"]["freq"], "n_obs_a": frames["half_a"]["n_obs"],
        "freq_b": frames["half_b"]["freq"], "n_obs_b": frames["half_b"]["n_obs"],
    })
    ok = df[(df["n_obs_a"] >= MIN_OBS) & (df["n_obs_b"] >= MIN_OBS)].dropna(subset=["freq_a", "freq_b"])
    print(f"\n유효필지 {len(ok):,}")

    rows = []
    rho_parcel = spearmanr(ok["freq_a"], ok["freq_b"]).statistic
    rows.append({"unit": "필지", "n": len(ok), "spearman": round(float(rho_parcel), 3)})
    print(f"\n[필지 단위] 두 묶음 빈도 상관 Spearman rho = {rho_parcel:.3f}")

    for unit, key in (("읍면동", "emd_cd"), ("시군구", "sgg_nm")):
        agg = ok.groupby(key).agg(
            freq_a=("freq_a", "mean"), freq_b=("freq_b", "mean"), n=("farmmap_id", "size")
        )
        agg = agg[agg["n"] >= 100]
        rho = spearmanr(agg["freq_a"], agg["freq_b"]).statistic
        rows.append({"unit": unit, "n": len(agg), "spearman": round(float(rho), 3)})
        print(f"[{unit} 단위, 필지 100개 이상] n={len(agg)}  Spearman rho = {rho:.3f}")

    pd.DataFrame(rows).to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    # --- 읍면동 순위표 (전체 관측 기준) --------------------------------
    full = pd.read_parquet(REPO_ROOT / "data" / "processed" / "features" / "parcel_susceptibility.parquet")
    full = full.join(parcels[["emd_cd", "emd_nm"]])
    full = full[full["wet_n_obs"] >= 10]
    emd = full.groupby(["emd_cd", "emd_nm", "sgg_nm"], observed=True).agg(
        parcels=("farmmap_id", "size"),
        area_km2=("area_m2", lambda s: round(s.sum() / 1e6, 2)),
        wet_freq=("wet_freq", "mean"),
        dry_freq=("dry_freq", "mean"),
    ).reset_index()
    emd = emd[emd["parcels"] >= 100].sort_values("wet_freq", ascending=False)
    emd.round(4).to_csv(EMD_CSV, index=False, encoding="utf-8-sig")

    print(f"\n[읍면동 상습침수 순위 상위 15]  (전체 {len(emd)}개 읍면동)")
    print(emd.head(15)[["sgg_nm", "emd_nm", "parcels", "area_km2", "wet_freq", "dry_freq"]].to_string(index=False))
    print(f"\n상위 10% 읍면동 평균 wet_freq {emd['wet_freq'].quantile(0.9):.3f}"
          f" vs 하위 10% {emd['wet_freq'].quantile(0.1):.3f}"
          f" ({emd['wet_freq'].quantile(0.9)/max(emd['wet_freq'].quantile(0.1),1e-9):.1f}배)")


if __name__ == "__main__":
    main()
