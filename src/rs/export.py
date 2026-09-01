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
import zipfile
from pathlib import Path

import ee
import httpx
import rasterio
from rasterio.merge import merge


def _tiles(bounds: tuple[float, float, float, float], tile_deg: float) -> list[tuple[float, float, float, float]]:
    xmin, ymin, xmax, ymax = bounds
    out = []
    y = ymin
    while y < ymax:
        x = xmin
        while x < xmax:
            out.append((x, y, min(x + tile_deg, xmax), min(y + tile_deg, ymax)))
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
) -> Path:
    """image 를 타일로 내려받아 하나의 GeoTIFF 로 합친다. bounds 는 EPSG:4326.

    GEE 가 내려주는 GeoTIFF 에는 밴드 이름이 들어 있지 않다.
    ee.Image 에서 밴드명을 읽어 descriptions 로 기록해 둔다.
    """
    if band_names is None:
        band_names = image.bandNames().getInfo()
    tiles = _tiles(bounds, tile_deg)
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
                resp = client.get(url)
                resp.raise_for_status()
                blob = resp.content

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
