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


def sample_points(
    raster_path: Path, xs: np.ndarray, ys: np.ndarray, names: list[str] | None = None
) -> pd.DataFrame:
    """래스터를 점 좌표에서 샘플링한다 (래스터 CRS 기준).

    지형(MERIT 90m, DEM 30m)과 강우(ERA5-Land 약 11km)는 필지(평균 1,500 m²)보다
    격자가 훨씬 굵다. 필지 하나가 격자 한 칸보다 작으므로 면적평균 대신
    대표점 샘플링으로 충분하며 훨씬 빠르다.
    """
    with rasterio.open(raster_path) as src:
        rows, cols = rasterio.transform.rowcol(src.transform, xs, ys)
        rows = np.clip(np.asarray(rows), 0, src.height - 1)
        cols = np.clip(np.asarray(cols), 0, src.width - 1)
        if names is None:
            described = [n for n in (src.descriptions or ()) if n]
            names = described if len(described) == src.count else [f"b{i+1}" for i in range(src.count)]
        if len(names) != src.count:
            raise ValueError(f"밴드명 {len(names)}개 != 래스터 밴드 {src.count}개")
        out = {}
        for i, name in enumerate(names, start=1):
            band = src.read(i)
            values = band[rows, cols].astype(np.float32)
            nodata = src.nodatavals[i - 1]
            if nodata is not None:
                values = np.where(values == nodata, np.nan, values)
            out[name] = values
    return pd.DataFrame(out)


def parcel_means(
    raster_path: Path,
    parcels: gpd.GeoDataFrame,
    index: np.ndarray | None = None,
    names: list[str] | None = None,
    chunk_rows: int = 4096,
) -> pd.DataFrame:
    """필지별 밴드 평균. 연속값 래스터(빈도·지형 등)에 쓴다.

    parcel_stats 가 임계값 기반 카운트라면 이쪽은 단순 면적평균이다.
    """
    with rasterio.open(raster_path) as src:
        if parcels.crs is None or str(parcels.crs) != str(src.crs):
            parcels = parcels.to_crs(src.crs)
        if names is None:
            described = [n for n in (src.descriptions or ()) if n]
            names = described if len(described) == src.count else [f"b{i+1}" for i in range(src.count)]
        if index is None:
            index = build_index(raster_path, parcels)
        n = len(parcels)

        sums = {name: np.zeros(n + 1, dtype=np.float64) for name in names}
        counts = np.zeros(n + 1, dtype=np.float64)

        for row0 in range(0, src.height, chunk_rows):
            rows = min(chunk_rows, src.height - row0)
            window = rasterio.windows.Window(0, row0, src.width, rows)
            idx = index[row0 : row0 + rows].ravel()
            keep = idx > 0
            if not keep.any():
                continue
            fi = idx[keep]
            first = src.read(1, window=window).ravel()[keep].astype(np.float32)
            ok = np.isfinite(first)
            counts += np.bincount(fi[ok], minlength=n + 1)
            for band, name in enumerate(names, start=1):
                values = src.read(band, window=window).ravel()[keep].astype(np.float32)
                sums[name] += np.bincount(fi[ok], weights=np.nan_to_num(values[ok]), minlength=n + 1)

    denom = pd.Series(counts[1:]).replace(0, np.nan)
    return pd.DataFrame({name: sums[name][1:] / denom for name in names})
