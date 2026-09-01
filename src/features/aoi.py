"""충청남도 AOI(관심영역) 구축.

입력: 통계청 SGIS 행정동 경계 shapefile (BND_ADM_DONG_PG, EPSG:5186)
출력:
    data/processed/aoi/chungnam_adm_dong.parquet   전체 해상도, EPSG:5179, 208행
    data/aoi/chungnam_sgg.geojson                  시군구 dissolve, EPSG:4326, 단순화
    data/aoi/chungnam_boundary.geojson             충남 전체 외곽, EPSG:4326, 단순화

코드 체계는 SGIS 8자리다: 시도(2) + 시군구(3) + 행정동(3).
충청남도 시도코드는 34이며 세종특별자치시(29)는 별도 시도이므로 포함하지 않는다.

주의
- 커밋되는 GeoJSON 2종은 **단순화된 표시·AOI 전용**이다.
  필지를 시군에 배정하는 spatial holdout 등 분석용 조인에는 반드시
  data/processed/aoi/chungnam_adm_dong.parquet (전체 해상도)를 사용한다.
- 원본 shapefile 인코딩은 CP949다.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd

SIDO_CODE = "34"  # 충청남도 (SGIS)
SRC_CRS = "EPSG:5186"  # KGD2002 Central Belt 2010
WORK_CRS = "EPSG:5179"  # Korea 2000 / Unified CS — 프로젝트 표준
OUT_CRS = "EPSG:4326"  # GEE 입력용

# 시군구 코드 -> 명칭.
# 원본에 시군구명 필드가 없어 소속 읍·면 이름으로 식별했다 (예: 34580 태안읍·안면읍 -> 태안군).
# 공식 행정구역 코드표와 한 번 대조할 것.
SGG_NAMES: dict[str, str] = {
    "34011": "천안시 동남구",
    "34012": "천안시 서북구",
    "34020": "공주시",
    "34030": "보령시",
    "34040": "아산시",
    "34050": "서산시",
    "34060": "논산시",
    "34070": "계룡시",
    "34080": "당진시",
    "34510": "금산군",
    "34530": "부여군",
    "34540": "서천군",
    "34550": "청양군",
    "34560": "홍성군",
    "34570": "예산군",
    "34580": "태안군",
}

# 천안시 2개 구를 하나의 시로 합쳐 시군 단위(15개)로 볼 때 사용한다.
SGG_TO_SIGUNGU: dict[str, str] = {
    code: ("천안시" if name.startswith("천안시") else name) for code, name in SGG_NAMES.items()
}

SIMPLIFY_TOLERANCE_M = 50.0


def load_chungnam(src: Path) -> gpd.GeoDataFrame:
    """행정동 경계에서 충남만 추출하고 작업 CRS로 변환한다."""
    gdf = gpd.read_file(src, encoding="cp949")
    if gdf.crs is None:
        gdf = gdf.set_crs(SRC_CRS)

    cn = gdf[gdf["ADM_CD"].str.startswith(SIDO_CODE)].copy()
    if cn.empty:
        raise ValueError(f"시도코드 {SIDO_CODE} 에 해당하는 행정동이 없습니다: {src}")

    cn["sgg_cd"] = cn["ADM_CD"].str[:5]
    unknown = sorted(set(cn["sgg_cd"]) - set(SGG_NAMES))
    if unknown:
        raise ValueError(f"SGG_NAMES에 없는 시군구 코드: {unknown}")

    cn["sgg_nm"] = cn["sgg_cd"].map(SGG_NAMES)
    cn["sigungu_nm"] = cn["sgg_cd"].map(SGG_TO_SIGUNGU)
    cn = cn.rename(columns={"ADM_CD": "adm_cd", "ADM_NM": "adm_nm", "BASE_DATE": "base_date"})
    cn = cn[["adm_cd", "adm_nm", "sgg_cd", "sgg_nm", "sigungu_nm", "base_date", "geometry"]]
    return cn.to_crs(WORK_CRS).reset_index(drop=True)


def build(src: Path, repo_root: Path) -> None:
    cn = load_chungnam(src)

    processed_dir = repo_root / "data" / "processed" / "aoi"
    aoi_dir = repo_root / "data" / "aoi"
    processed_dir.mkdir(parents=True, exist_ok=True)
    aoi_dir.mkdir(parents=True, exist_ok=True)

    dong_path = processed_dir / "chungnam_adm_dong.parquet"
    cn.to_parquet(dong_path)

    # 시군구 단위 dissolve — spatial holdout 그룹 정의와 지도 표시용
    sgg = cn.dissolve(by="sgg_cd", aggfunc={"sgg_nm": "first", "sigungu_nm": "first"}).reset_index()
    sgg_simple = sgg.copy()
    sgg_simple["geometry"] = sgg_simple.geometry.simplify(SIMPLIFY_TOLERANCE_M)
    sgg_simple.to_crs(OUT_CRS).to_file(aoi_dir / "chungnam_sgg.geojson", driver="GeoJSON")

    # 충남 전체 외곽 — GEE AOI
    outline = sgg.dissolve()[["geometry"]]
    outline["geometry"] = outline.geometry.simplify(SIMPLIFY_TOLERANCE_M)
    outline["name"] = "충청남도"
    outline.to_crs(OUT_CRS).to_file(aoi_dir / "chungnam_boundary.geojson", driver="GeoJSON")

    bounds = cn.to_crs(OUT_CRS).total_bounds
    print(f"행정동 {len(cn)}개 / 시군구 {len(sgg)}개")
    print(f"면적 합계: {cn.area.sum() / 1e6:,.1f} km2")
    print(f"bbox (EPSG:4326): {bounds[0]:.4f}, {bounds[1]:.4f}, {bounds[2]:.4f}, {bounds[3]:.4f}")
    print(f"기준일자: {sorted(cn['base_date'].unique())}")
    for path in (dong_path, aoi_dir / "chungnam_sgg.geojson", aoi_dir / "chungnam_boundary.geojson"):
        print(f"  {path.relative_to(repo_root)}  {path.stat().st_size / 1024:,.0f} KB")


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="충남 AOI 생성")
    parser.add_argument(
        "--src",
        type=Path,
        default=repo_root / "data" / "raw" / "admin_boundary" / "BND_ADM_DONG_PG.shp",
        help="SGIS 행정동 경계 shapefile 경로",
    )
    args = parser.parse_args()
    build(args.src, repo_root)


if __name__ == "__main__":
    main()
