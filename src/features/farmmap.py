"""팜맵(농경지 전자지도) WFS 클라이언트.

팜맵은 지적도가 아니라 **항공영상으로 판독한 실제 경작지 경계**이며,
논/밭/과수/시설 분류(`clsf_nm`)를 제공한다. 이 분류가 이 프로젝트의 핵심 전제다 —
실험 01에서 논 담수 confound 때문에 논/밭 분리 없이는 침수 판별이 성립하지 않음이 확인됐다.
docs/40_experiments.md 참조.

서비스: https://agis.epis.or.kr/ASD/farmmapApi/wfs.do

인증
    apiKey  발급받은 키
    domain  **키 발급 시 등록한 URL.** 이 값이 다르면
            "요청서버의 도메인과 등록하신 도메인 정보가 다릅니다" 로 거부된다.
    둘 다 .env 에 둔다 (.env 는 git 에서 제외).

제약
    - 1회 요청 최대 200건 (`count`). AOI 전체는 bbox 타일 + startindex 페이징으로 훑는다.
    - EPSG:5179/4326 등은 bbox 축 순서가 **ymin,xmin,ymax,xmax** 다 (3857과 반대).
    - 전 충남(8,264 km²)을 이 API로만 받는 것은 요청량이 크다.
      대량 확보는 공공데이터포털 파일데이터(시도별 SHP, 2021년 기준)를 우선 검토할 것.
      이 모듈은 최신 갱신분 확인·표본 검증·부분 영역 조회에 쓴다.

사용
    python src/features/farmmap.py --sgg 부여군 --out data/processed/farmmap/buyeo.parquet
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import geopandas as gpd
import httpx
import pandas as pd
from shapely.geometry import box, shape

WFS_URL = "https://agis.epis.or.kr/ASD/farmmapApi/wfs.do"
WORK_CRS = "EPSG:5179"
MAX_COUNT = 200  # 서비스 상한
PROPERTIES = "id,uid,clsf_nm,pnu,ldcg_cd,stdg_cd,stdg_addr,area,flight_ymd,updt_ymd,shape"
REPO_ROOT = Path(__file__).resolve().parents[2]


def load_env(path: Path | None = None) -> dict[str, str]:
    """.env 를 읽는다. 키를 코드나 커밋에 넣지 않기 위한 최소 로더."""
    path = path or REPO_ROOT / ".env"
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    env.setdefault("FARMMAP_API_KEY", os.environ.get("FARMMAP_API_KEY", ""))
    env.setdefault("FARMMAP_DOMAIN", os.environ.get("FARMMAP_DOMAIN", ""))
    if not env["FARMMAP_API_KEY"]:
        raise RuntimeError("FARMMAP_API_KEY 가 비어 있습니다. .env 를 확인하세요.")
    if not env["FARMMAP_DOMAIN"]:
        raise RuntimeError(
            "FARMMAP_DOMAIN 이 비어 있습니다. 팜맵 인증키 발급 시 등록한 URL을 .env 에 넣으세요."
        )
    return env


def _bbox_param(xmin: float, ymin: float, xmax: float, ymax: float) -> str:
    """EPSG:5179 는 축 순서가 ymin,xmin,ymax,xmax 다."""
    return f"{ymin},{xmin},{ymax},{xmax},{WORK_CRS}"


def fetch_bbox(
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    env: dict[str, str],
    client: httpx.Client,
    page_limit: int = 100,
    pause_s: float = 0.2,
) -> list[dict]:
    """한 bbox 안의 필지를 페이징으로 모두 가져온다."""
    features: list[dict] = []
    start = 0
    for _ in range(page_limit):
        params = {
            "service": "wfs",
            "version": "2.0.0",
            "request": "GetFeature",
            "typename": "farm_map_api",
            "outputformat": "json",
            "srsname": WORK_CRS,
            "propertyname": PROPERTIES,
            "bbox": _bbox_param(xmin, ymin, xmax, ymax),
            "sortby": "asc",
            "startindex": start,
            "count": MAX_COUNT,
            "apiKey": env["FARMMAP_API_KEY"],
            "domain": env["FARMMAP_DOMAIN"],
        }
        resp = client.get(WFS_URL, params=params, timeout=90)
        resp.raise_for_status()
        js = resp.json()
        if isinstance(js.get("status"), dict) and js["status"].get("result") == "F":
            raise RuntimeError(f"팜맵 WFS 오류: {js['status'].get('errorMsg')}")

        page = js.get("features", [])
        features.extend(page)
        if len(page) < MAX_COUNT:
            break
        start += MAX_COUNT
        time.sleep(pause_s)
    return features


def fetch_area(geom, env: dict[str, str], tile_m: float = 2000.0, pause_s: float = 0.2) -> gpd.GeoDataFrame:
    """대상 영역을 타일로 나눠 전부 조회한다. geom 은 EPSG:5179."""
    xmin, ymin, xmax, ymax = geom.bounds
    tiles = []
    y = ymin
    while y < ymax:
        x = xmin
        while x < xmax:
            t = box(x, y, min(x + tile_m, xmax), min(y + tile_m, ymax))
            if t.intersects(geom):
                tiles.append(t)
            x += tile_m
        y += tile_m

    print(f"타일 {len(tiles)}개 (tile={tile_m:.0f}m)")
    rows: dict[str, dict] = {}
    with httpx.Client() as client:
        for i, t in enumerate(tiles, 1):
            for f in fetch_bbox(*t.bounds, env=env, client=client, pause_s=pause_s):
                props = f.get("properties", {})
                key = str(props.get("uid") or f.get("id"))
                if key not in rows and f.get("geometry"):
                    rows[key] = {**props, "geometry": shape(f["geometry"])}
            if i % 20 == 0 or i == len(tiles):
                print(f"  {i}/{len(tiles)} 타일, 누적 필지 {len(rows):,}")
            time.sleep(pause_s)

    gdf = gpd.GeoDataFrame(pd.DataFrame(list(rows.values())), geometry="geometry", crs=WORK_CRS)
    return gdf


def main() -> None:
    parser = argparse.ArgumentParser(description="팜맵 WFS 조회")
    parser.add_argument("--sgg", required=True, help="시군구명 (예: 부여군). data/aoi/chungnam_sgg.geojson 기준")
    parser.add_argument("--tile", type=float, default=2000.0, help="타일 한 변 길이 (m)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    env = load_env()
    sgg = gpd.read_file(REPO_ROOT / "data" / "aoi" / "chungnam_sgg.geojson").to_crs(WORK_CRS)
    match = sgg[sgg["sgg_nm"] == args.sgg]
    if match.empty:
        raise SystemExit(f"'{args.sgg}' 를 찾을 수 없습니다. 사용 가능: {list(sgg['sgg_nm'])}")

    gdf = fetch_area(match.geometry.iloc[0], env, tile_m=args.tile)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(args.out)

    print(f"\n필지 {len(gdf):,}개 -> {args.out}")
    if "clsf_nm" in gdf.columns:
        print("\n[농경지분류]")
        print(gdf["clsf_nm"].value_counts().to_string())


if __name__ == "__main__":
    main()
