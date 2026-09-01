"""래스터 -> 팜맵 필지 단위 집계.

필지 1,434,057개를 GEE에 올릴 수 없으므로 z-score 래스터를 내려받아 로컬에서 집계한다
(`src/rs/export.py`). 필지 인덱스를 래스터화한 뒤 np.bincount 로 한 번에 합산한다.
필지마다 zonal_stats 를 도는 방식은 140만 개에서 현실적이지 않다.

한계 — 팜맵 필지 평균 면적은 약 1,500 m² 로 20m 격자에서 4픽셀 수준이다.
작은 필지는 픽셀이 1~2개뿐이므로 `n_valid` 를 함께 내보내고
표본이 적은 필지는 신뢰도를 낮춰 다룬다. speckle 완화에 50m focal median 을 이미 적용했으므로
20m 보다 잘게 보는 것은 의미가 없다.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize

Z_THRESHOLD = 2.0


def band_index(src: rasterio.DatasetReader) -> dict[str, int]:
    """밴드 설명(descriptions)에서 이름 -> 인덱스(1-base) 매핑."""
    names = src.descriptions or ()
    out = {n: i + 1 for i, n in enumerate(names) if n}
    if not out:  # 이름이 없으면 관례 순서를 쓴다
        out = {"zvv": 1, "zvh": 2, "valid": 3}
    return out


def build_index(raster_path: Path, parcels: gpd.GeoDataFrame) -> np.ndarray:
    """필지 인덱스 래스터(0=없음, i+1=parcels의 i번째)를 만든다.

    여러 사건 래스터가 같은 격자를 쓰면 이 배열을 한 번만 만들어 재사용한다.
    """
    with rasterio.open(raster_path) as src:
        if parcels.crs is None or str(parcels.crs) != str(src.crs):
            parcels = parcels.to_crs(src.crs)
        return rasterize(
            ((geom, i + 1) for i, geom in enumerate(parcels.geometry)),
            out_shape=(src.height, src.width),
            transform=src.transform,
            fill=0,
            all_touched=True,
            dtype="int32",
        )


def parcel_stats(
    raster_path: Path,
    parcels: gpd.GeoDataFrame,
    z_threshold: float = Z_THRESHOLD,
    chunk_rows: int = 4096,
    index: np.ndarray | None = None,
) -> pd.DataFrame:
    """필지별 침수 후보 픽셀 비율.

    parcels 는 래스터와 동일 CRS 여야 한다. 반환 컬럼:
        n_pixels    필지에 걸린 픽셀 수
        n_valid     분석유효 픽셀 수 (영구수역·급경사·고지대 제외 후)
        n_open      개방수면형 (zvv<-t & zvh<-t)
        n_double    이중반사형 (zvv>+t & zvh>+t)
        mean_zvv, mean_zvh   유효픽셀 평균
    """
    with rasterio.open(raster_path) as src:
        if parcels.crs is None or str(parcels.crs) != str(src.crs):
            parcels = parcels.to_crs(src.crs)
        bands = band_index(src)
        n = len(parcels)

        if index is None:
            index = rasterize(
                ((geom, i + 1) for i, geom in enumerate(parcels.geometry)),
                out_shape=(src.height, src.width),
                transform=src.transform,
                fill=0,
                all_touched=True,
                dtype="int32",
            )
        elif index.shape != (src.height, src.width):
            raise ValueError(f"index shape {index.shape} != raster {(src.height, src.width)}")

        acc = {k: np.zeros(n + 1, dtype=np.float64) for k in
               ("n_pixels", "n_valid", "n_open", "n_double", "sum_zvv", "sum_zvh")}

        for row0 in range(0, src.height, chunk_rows):
            rows = min(chunk_rows, src.height - row0)
            window = rasterio.windows.Window(0, row0, src.width, rows)
            zvv = src.read(bands["zvv"], window=window).astype(np.float32)
            zvh = src.read(bands["zvh"], window=window).astype(np.float32)
            valid = src.read(bands["valid"], window=window)
            idx = index[row0 : row0 + rows]

            flat = idx.ravel()
            keep = flat > 0
            if not keep.any():
                continue
            fi = flat[keep]
            zv, zh = zvv.ravel()[keep], zvh.ravel()[keep]
            ok = (valid.ravel()[keep] == 1) & np.isfinite(zv) & np.isfinite(zh)

            acc["n_pixels"] += np.bincount(fi, minlength=n + 1)
            acc["n_valid"] += np.bincount(fi[ok], minlength=n + 1)
            acc["n_open"] += np.bincount(fi[ok & (zv < -z_threshold) & (zh < -z_threshold)], minlength=n + 1)
            acc["n_double"] += np.bincount(fi[ok & (zv > z_threshold) & (zh > z_threshold)], minlength=n + 1)
            acc["sum_zvv"] += np.bincount(fi[ok], weights=zv[ok], minlength=n + 1)
            acc["sum_zvh"] += np.bincount(fi[ok], weights=zh[ok], minlength=n + 1)

    out = pd.DataFrame({k: v[1:] for k, v in acc.items()})
    denom = out["n_valid"].replace(0, np.nan)
    out["mean_zvv"] = out["sum_zvv"] / denom
    out["mean_zvh"] = out["sum_zvh"] / denom
    out["open_fraction"] = out["n_open"] / denom
    out["double_fraction"] = out["n_double"] / denom
    out = out.drop(columns=["sum_zvv", "sum_zvh"])

    for col in ("farmmap_id", "class_nm", "sgg_nm", "area_m2"):
        if col in parcels.columns:
            out[col] = parcels[col].to_numpy()
    return out
