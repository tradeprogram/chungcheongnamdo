"""Copernicus Data Space 카탈로그 조회 — Sentinel-1 가용성 점검.

인증 없이 조회 가능한 OData 엔드포인트를 사용한다 (다운로드는 인증 필요).

핵심 용도는 하나다. **어떤 relative orbit이 충남을 실제로 덮는가.**
단순히 AOI와 intersect 하는 scene을 세면 안 된다. 충남 동쪽 끝을 2% 스치는 궤도도
intersect로 잡히기 때문이다. 반드시 footprint와 AOI의 면적 교집합 비율로 판단한다.

사용
    python src/rs/catalog.py --start 2025-06-01 --end 2025-09-01
    python src/rs/catalog.py --start 2021-06-01 --end 2025-09-01 --summer-only
"""

from __future__ import annotations

import argparse
import datetime as dt
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import httpx
import pandas as pd
from shapely.geometry import shape

ODATA = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
KST = dt.timedelta(hours=9)
WORK_CRS = "EPSG:5179"
FULL_COVERAGE_PCT = 80.0


def load_aoi(repo_root: Path):
    """충남 AOI를 (조회용 hull WKT, 면적계산용 투영 geometry)로 반환."""
    aoi = gpd.read_file(repo_root / "data" / "aoi" / "chungnam_boundary.geojson")
    geom = aoi.geometry.union_all()
    hull_wkt = geom.convex_hull.simplify(0.05).wkt
    aoi_m = gpd.GeoSeries([geom], crs=4326).to_crs(WORK_CRS).iloc[0]
    return hull_wkt, aoi_m


def query_products(hull_wkt: str, start: str, end: str, product_type: str = "IW_GRDH_1S") -> list[dict]:
    flt = (
        "Collection/Name eq 'SENTINEL-1' "
        "and Attributes/OData.CSC.StringAttribute/any("
        f"a:a/Name eq 'productType' and a/OData.CSC.StringAttribute/Value eq '{product_type}') "
        f"and OData.CSC.Intersects(area=geography'SRID=4326;{hull_wkt}') "
        f"and ContentDate/Start gt {start}T00:00:00.000Z "
        f"and ContentDate/Start lt {end}T00:00:00.000Z"
    )
    out: list[dict] = []
    url: str | None = ODATA
    params: dict | None = {"$filter": flt, "$expand": "Attributes", "$top": 200}
    while url:
        resp = httpx.get(url, params=params, timeout=180) if params else httpx.get(url, timeout=180)
        resp.raise_for_status()
        js = resp.json()
        for p in js["value"]:
            attrs = {a["Name"]: a.get("Value") for a in p.get("Attributes", [])}
            utc = dt.datetime.fromisoformat(p["ContentDate"]["Start"].replace("Z", "+00:00"))
            out.append(
                {
                    "name": p["Name"],
                    "sat": p["Name"][:3],
                    "kst": utc + KST,
                    "rel_orbit": attrs.get("relativeOrbitNumber"),
                    "direction": attrs.get("orbitDirection"),
                    "polarisation": attrs.get("polarisationChannels"),
                    "geom": shape(p["GeoFootprint"]) if p.get("GeoFootprint") else None,
                }
            )
        url, params = js.get("@odata.nextLink"), None
    return out


def coverage_by_pass(scenes: list[dict], aoi_m) -> pd.DataFrame:
    """(날짜, 궤도, 방향) 단위로 프레임을 합쳐 AOI 커버리지 %를 계산한다."""
    grouped = defaultdict(list)
    for s in scenes:
        grouped[(s["kst"].date(), s["rel_orbit"], s["direction"], s["sat"])].append(s)

    rows = []
    for (date, rel_orbit, direction, sat), items in grouped.items():
        geoms = [i["geom"] for i in items if i["geom"] is not None]
        if not geoms:
            continue
        foot = gpd.GeoSeries(geoms, crs=4326).to_crs(WORK_CRS).union_all()
        rows.append(
            {
                "date": date,
                "time_kst": items[0]["kst"].strftime("%H:%M"),
                "rel_orbit": rel_orbit,
                "direction": direction,
                "sat": sat,
                "frames": len(geoms),
                "coverage_pct": round(foot.intersection(aoi_m).area / aoi_m.area * 100, 1),
            }
        )
    return pd.DataFrame(rows).sort_values(["date", "rel_orbit"]).reset_index(drop=True)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="충남 Sentinel-1 가용성 점검")
    parser.add_argument("--start", default="2025-06-01")
    parser.add_argument("--end", default="2025-09-01")
    parser.add_argument("--summer-only", action="store_true", help="6~8월만 연도별로 조회")
    parser.add_argument("--out", type=Path, default=repo_root / "data" / "reference" / "s1_acquisitions_chungnam.csv")
    args = parser.parse_args()

    hull_wkt, aoi_m = load_aoi(repo_root)

    windows = []
    if args.summer_only:
        for yr in range(int(args.start[:4]), int(args.end[:4]) + 1):
            windows.append((f"{yr}-06-01", f"{yr}-09-01"))
    else:
        windows.append((args.start, args.end))

    frames = []
    for start, end in windows:
        scenes = query_products(hull_wkt, start, end)
        df = coverage_by_pass(scenes, aoi_m)
        print(f"{start}~{end}: scene {len(scenes)}개 / pass {len(df)}회")
        frames.append(df)

    out = pd.concat(frames).sort_values(["date", "rel_orbit"]).reset_index(drop=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False, encoding="utf-8-sig")

    full = out[out["coverage_pct"] >= FULL_COVERAGE_PCT]
    print(f"\n충남 {FULL_COVERAGE_PCT:.0f}% 이상 커버 pass: {len(full)} / {len(out)}")
    print("\n[궤도별 전체커버 통과 횟수]")
    print(full.groupby(["rel_orbit", "direction", "time_kst"]).size().to_string())
    print(f"\nsaved: {args.out}")


if __name__ == "__main__":
    main()
