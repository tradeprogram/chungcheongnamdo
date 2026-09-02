"""GEE 계산 결과를 로컬 GeoTIFF로 내려받는다.

필지 1,434,057개를 GEE에 올릴 수 없으므로 방향을 뒤집는다.
**래스터를 내려받아 로컬에서 zonal 집계**한다.

ee.Image.getDownloadURL 은 요청당 크기 상한이 있으므로 AOI를 타일로 잘라
받은 뒤 rasterio 로 합친다.

사용
    from src.rs import export
    export.download_image(image, aoi_bounds, scale=20, out_path=Path("z.tif"))
"""

from __future__ import annotations

import shutil
import tempfile
import time
import zipfile
from pathlib import Path

import ee
import httpx
import rasterio
from rasterio.merge import merge
from shapely.geometry import box

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_RETRY = 5
BACKOFF_S = 20


def _tiles(
    bounds: tuple[float, float, float, float],
    tile_deg: float,
    aoi_geom=None,
) -> list[tuple[float, float, float, float]]:
    """bbox 를 타일로 자른다. aoi_geom 을 주면 닿지 않는 타일은 버린다.

    충남 bbox 는 서해를 크게 물고 있어 45타일 중 13타일이 도 경계에 닿지 않는다.
    한 타일이 연산 1분씩 걸리는 상황에서 그 29%는 그냥 버리는 시간이다.
    """
    xmin, ymin, xmax, ymax = bounds
    out = []
    y = ymin
    while y < ymax:
        x = xmin
        while x < xmax:
            tile = (x, y, min(x + tile_deg, xmax), min(y + tile_deg, ymax))
            if aoi_geom is None or aoi_geom.intersects(box(*tile)):
                out.append(tile)
            x += tile_deg
        y += tile_deg
    return out


def download_image(
    image: ee.Image,
    bounds: tuple[float, float, float, float],
    out_path: Path,
    scale: int = 20,
    tile_deg: float = 0.25,
    crs: str = "EPSG:4326",
    band_names: list[str] | None = None,
    aoi_geom=None,
) -> Path:
    """image 를 타일로 내려받아 하나의 GeoTIFF 로 합친다. bounds 는 EPSG:4326.

    GEE 가 내려주는 GeoTIFF 에는 밴드 이름이 들어 있지 않다.
    ee.Image 에서 밴드명을 읽어 descriptions 로 기록해 둔다.
    """
    if band_names is None:
        band_names = image.bandNames().getInfo()
    tiles = _tiles(bounds, tile_deg, aoi_geom)
    tmp = Path(tempfile.mkdtemp(prefix="ee_dl_"))
    paths: list[Path] = []
    print(f"타일 {len(tiles)}개 (scale={scale}m)")

    try:
        with httpx.Client(timeout=600, follow_redirects=True) as client:
            for i, (x0, y0, x1, y1) in enumerate(tiles, 1):
                region = ee.Geometry.Rectangle([x0, y0, x1, y1], proj="EPSG:4326", geodesic=False)
                url = image.getDownloadURL(
                    {"scale": scale, "region": region, "crs": crs, "format": "GEO_TIFF", "filePerBand": False}
                )
                # GEE 는 간헐적으로 503/429 를 낸다. 몇 장 실패했다고 전체를 버리지 않는다.
                blob = None
                for attempt in range(1, MAX_RETRY + 1):
                    try:
                        resp = client.get(url)
                        resp.raise_for_status()
                        blob = resp.content
                        break
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code not in RETRY_STATUS or attempt == MAX_RETRY:
                            raise
                        wait = BACKOFF_S * attempt
                        print(f"    타일 {i} {exc.response.status_code} — {wait}s 후 재시도 ({attempt}/{MAX_RETRY})")
                        time.sleep(wait)
                        # URL 은 만료될 수 있으므로 다시 발급받는다
                        url = image.getDownloadURL(
                            {"scale": scale, "region": region, "crs": crs,
                             "format": "GEO_TIFF", "filePerBand": False}
                        )

                tile_path = tmp / f"tile_{i:04d}.tif"
                if blob[:2] == b"PK":  # zip 으로 오는 경우
                    zpath = tmp / f"tile_{i:04d}.zip"
                    zpath.write_bytes(blob)
                    with zipfile.ZipFile(zpath) as zf:
                        member = next(n for n in zf.namelist() if n.lower().endswith(".tif"))
                        tile_path.write_bytes(zf.read(member))
                else:
                    tile_path.write_bytes(blob)

                paths.append(tile_path)
                if i % 5 == 0 or i == len(tiles):
                    print(f"  {i}/{len(tiles)}  ({sum(p.stat().st_size for p in paths)/1e6:.0f} MB)")

        srcs = [rasterio.open(p) for p in paths]
        mosaic, transform = merge(srcs)
        profile = srcs[0].profile
        profile.update(
            height=mosaic.shape[1], width=mosaic.shape[2], transform=transform,
            compress="deflate", tiled=True, predictor=2,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(mosaic)
            if band_names and len(band_names) == mosaic.shape[0]:
                dst.descriptions = tuple(band_names)
        for s in srcs:
            s.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"-> {out_path}  {out_path.stat().st_size/1e6:.1f} MB  shape={mosaic.shape}")
    return out_path
