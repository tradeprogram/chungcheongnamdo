"""필지·읍면동 지도 데이터 생성.

필지 1,434,057개를 한 번에 브라우저로 보낼 수 없다. 두 단계로 나눈다.

    emd.geojson          읍면동 249개 — 농경지를 읍면동별로 dissolve. 도 전역 choropleth
    parcels/<emd>.json   읍면동별 필지 — 확대했을 때만 해당 파일 하나를 불러온다

읍면동 경계는 별도로 받지 않고 **팜맵 필지를 dissolve** 해서 만든다.
팜맵의 법정동코드(8자리)와 SGIS 행정동코드(8자리)는 체계가 달라 그대로 조인되지 않고,
어차피 이 화면에서 의미 있는 것은 행정경계 전체가 아니라 **농경지 범위**다.

필지 파일은 용량이 커서 git 에서 제외한다 (web/data/parcels/).
이 스크립트로 언제든 다시 만든다.

실행
    python notebooks/build_parcel_tiles.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]
FARMMAP = REPO_ROOT / "data" / "processed" / "farmmap" / "chungnam_2021.parquet"
SUS = REPO_ROOT / "data" / "processed" / "features" / "parcel_susceptibility.parquet"
STATS = REPO_ROOT / "data" / "processed" / "features" / "field_event_stats.parquet"
WEB_DATA = REPO_ROOT / "web" / "data"
PARCEL_DIR = WEB_DATA / "parcels"

# 지도 표시용 단순화 허용오차 (m). 팜맵 필지는 평균 1,500 m² 라 5m 면 형상이 유지된다.
PARCEL_TOLERANCE_M = 5.0
EMD_TOLERANCE_M = 100.0
BUFFER_M = 50.0  # 인접 필지를 붙였다 되돌리는 폭
MIN_PARCELS_PER_EMD = 30
# 화면에 띄울 사건 — 필지 단위 침수율을 함께 실어 보낸다
EVENT_COLS = {"o134_2025-07-19": "e2025", "o127_2025-07-24": "e2025late", "o127_2023-07-23": "e2023"}


def main() -> None:
    print("필지 로드")
    parcels = gpd.read_parquet(
        FARMMAP, columns=["farmmap_id", "class_nm", "sgg_nm", "emd_cd", "emd_nm", "area_m2", "geometry"]
    ).reset_index(drop=True)

    sus_cols = ["farmmap_id", "wet_freq", "dry_freq", "wet_n_obs"]
    sus = pd.read_parquet(SUS)
    if "wet_method" in sus.columns:
        sus_cols.append("wet_method")
    parcels = parcels.merge(sus[sus_cols], on="farmmap_id", how="left")

    stats = pd.read_parquet(STATS)
    stats = stats[stats["event_id"].isin(EVENT_COLS)]
    wide = stats.pivot_table(index="farmmap_id", columns="event_id", values="double_fraction")
    wide = wide.rename(columns=EVENT_COLS)
    parcels = parcels.merge(wide, on="farmmap_id", how="left")

    # 판독 근거를 필지에 싣는다. 면적 집계(area)와 대표점 표본(point)은 근거의 등급이
    # 다르므로 같은 색으로 뭉뚱그리지 않는다. 격자 미포착 여부는 사건이 아니라
    # 필지 형상에서 결정되므로 대표 사건 하나의 method 를 대표값으로 쓴다.
    primary = next(iter(EVENT_COLS))
    if "method" in stats.columns:
        base = stats[stats["event_id"] == primary][["farmmap_id", "method", "n_valid", "steep"]]
        base = base.rename(columns={"method": "mth", "n_valid": "npx", "steep": "stp"})
        parcels = parcels.merge(base, on="farmmap_id", how="left")
        parcels["mth"] = parcels["mth"].map({"area": 0, "point": 1}).fillna(2).astype("int8")
        parcels["stp"] = parcels["stp"].fillna(False).astype("int8")
        parcels["npx"] = parcels["npx"].fillna(0).round(0).astype("int32")
        n = len(parcels)
        print(f"  판독 근거 — 면적집계 {(parcels['mth']==0).sum()/n*100:.1f}% / "
              f"점표본 {(parcels['mth']==1).sum()/n*100:.1f}% / "
              f"불가 {(parcels['mth']==2).sum()/n*100:.1f}% / 급경사 {parcels['stp'].sum()/n*100:.1f}%")
    print(f"  필지 {len(parcels):,}, 컬럼 {list(parcels.columns)}")

    # --- 읍면동 집계 ---------------------------------------------------
    # 필지를 그냥 dissolve 하면 안 된다. 필지들은 서로 떨어져 있어 dissolve 해도
    # 하나로 합쳐지지 않고 140만 개 경계가 그대로 남는다 (실제로 265MB 가 나왔다).
    # 살짝 buffer 해서 인접 필지를 붙인 뒤 되돌리고, 크게 simplify 한다.
    print("읍면동 경계 생성 (buffer -> union -> simplify)")
    rows = []
    for emd_cd, g in parcels.groupby("emd_cd"):
        if len(g) < MIN_PARCELS_PER_EMD:
            continue
        merged = g.geometry.buffer(BUFFER_M).union_all().buffer(-BUFFER_M).simplify(EMD_TOLERANCE_M)
        rows.append({
            "emd_cd": emd_cd,
            "emd_nm": g["emd_nm"].iloc[0],
            "sgg_nm": g["sgg_nm"].iloc[0],
            "parcels": len(g),
            "area_km2": round(g["area_m2"].sum() / 1e6, 2),
            "wet_freq": round(float(g["wet_freq"].mean()), 4),
            # 읍면동 단위 판독률 — 화면에서 근거의 두께를 함께 보여준다
            "read_pct": round(float((g["mth"] < 2).mean() * 100), 1) if "mth" in g else None,
            "geometry": merged,
        })
    emd = gpd.GeoDataFrame(rows, geometry="geometry", crs=parcels.crs)
    emd["rank"] = emd["wet_freq"].rank(ascending=False, method="min").astype("Int64")

    WEB_DATA.mkdir(parents=True, exist_ok=True)
    out_path = WEB_DATA / "emd.geojson"
    if out_path.exists():
        out_path.unlink()
    emd.to_crs(4326).to_file(out_path, driver="GeoJSON", COORDINATE_PRECISION=5)
    print(f"  읍면동 {len(emd)}개 -> emd.geojson ({out_path.stat().st_size/1e6:.1f} MB)")

    # --- 읍면동별 필지 ---------------------------------------------------
    print("읍면동별 필지 파일 생성")
    PARCEL_DIR.mkdir(parents=True, exist_ok=True)
    for f in PARCEL_DIR.glob("*.json"):
        f.unlink()

    keep = parcels[parcels["emd_cd"].isin(set(emd["emd_cd"]))].copy()
    keep["geometry"] = keep.geometry.simplify(PARCEL_TOLERANCE_M)
    keep = keep.to_crs(4326)

    index = []
    for emd_cd, group in keep.groupby("emd_cd"):
        cols = ["farmmap_id", "class_nm", "emd_nm", "sgg_nm", "area_m2", "wet_freq", "wet_n_obs"]
        cols += [c for c in ("mth", "npx", "stp") if c in group.columns]
        cols += [c for c in EVENT_COLS.values() if c in group.columns]
        out = group[cols + ["geometry"]].copy()
        for c in ("wet_freq", *EVENT_COLS.values()):
            if c in out.columns:
                out[c] = out[c].round(3)
        out["area_m2"] = out["area_m2"].round(0)
        path = PARCEL_DIR / f"{emd_cd}.json"
        path.write_text(out.to_json(drop_id=True), encoding="utf-8")
        # 화면은 지도 중심이 어느 읍면동에 드는지를 이 bbox 로 판정한다.
        # bbox 를 빼면 필지가 영영 로드되지 않으므로 반드시 함께 쓴다.
        b = group.total_bounds
        index.append({
            "emd_cd": emd_cd,
            "emd_nm": group["emd_nm"].iloc[0],
            "sgg_nm": group["sgg_nm"].iloc[0],
            "bbox": [round(float(v), 5) for v in b],
            "n": len(out),
            "kb": round(path.stat().st_size / 1024),
        })

    (WEB_DATA / "parcel_index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8")
    total = sum(i["kb"] for i in index) / 1024
    print(f"  {len(index)}개 파일, 합계 {total:.0f} MB, 평균 {total*1024/len(index):.0f} KB")
    print(f"  최대 {max(index, key=lambda i: i['kb'])}")


if __name__ == "__main__":
    main()
