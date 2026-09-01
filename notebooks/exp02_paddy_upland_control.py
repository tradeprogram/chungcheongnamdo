"""실험 02 — 논/밭 분리 후 부정 대조군 재실행.

질문
    실험 01의 부정 대조군 실패가 논 담수 때문이라면,
    밭(upland)에서는 대조군이 통과해야 한다. 실제로 그런가?

방법
    팜맵 필지에서 분류별로 표본을 뽑아 대표점을 만들고,
    GEE에서 계산한 robust z-score 다중밴드 영상을 그 점들에서 샘플링한다.
    필지 140만 개를 GEE에 올릴 수 없으므로 층화표본으로 분포를 추정한다.

비교 축
    사건(07-24, peak+7일) vs 부정대조군(06-30, peak-17일)  — 동일 궤도 127, 동일 baseline
    논 vs 밭 vs 과수 vs 시설

    밭에서 사건 > 대조군 이고 논에서 그렇지 않다면,
    "논은 별도 방법, 밭은 변화탐지로 가능" 이라는 설계가 근거를 얻는다.

실행
    python notebooks/exp02_paddy_upland_control.py --n-paddy 6000 --n-upland 6000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import ee
import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.rs import gee, sentinel1_flood as s1f  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
FARMMAP = REPO_ROOT / "data" / "processed" / "farmmap" / "chungnam_2021.parquet"
BATCH = 2000
Z_THRESHOLD = 2.0


def sample_points(counts: dict[str, int], seed: int = 42) -> gpd.GeoDataFrame:
    """분류별 층화표본 대표점 (EPSG:4326)."""
    gdf = gpd.read_parquet(FARMMAP, columns=["farmmap_id", "class_nm", "sgg_nm", "area_m2", "geometry"])
    parts = []
    for cls, n in counts.items():
        sub = gdf[gdf["class_nm"] == cls]
        if sub.empty:
            continue
        parts.append(sub.sample(min(n, len(sub)), random_state=seed))
    sample = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=gdf.crs)
    # 폴리곤 내부가 보장되는 대표점
    sample["geometry"] = sample.geometry.representative_point()
    return sample.to_crs(4326)


def build_z_stack(aoi: ee.Geometry) -> ee.Image:
    """사건·대조군 z-score를 한 장의 다중밴드 영상으로 묶는다."""
    med127, mad127 = s1f.same_season_baseline(aoi, [2021, 2022, 2023, 2024], rel_orbit=127, platform="A")
    event127 = gee.s1_collection(aoi, *gee.kst_window("2025-07-24", "2025-07-25"), 127, "A").mosaic()
    ctrl127 = gee.s1_collection(aoi, *gee.kst_window("2025-06-30", "2025-07-01"), 127, "A").mosaic()

    ref134 = gee.s1_collection(aoi, *gee.kst_window("2025-08-07", "2025-08-31"), 134, "C")
    med134 = ref134.median()
    mad134 = ref134.map(lambda im: im.subtract(med134).abs()).median()
    event134 = gee.s1_collection(aoi, *gee.kst_window("2025-07-19T00:00", "2025-07-19T23:59"), 134, "C").mosaic()

    z_e127 = s1f.robust_z(event127, med127, mad127).rename(["e127_zvv", "e127_zvh"])
    z_c127 = s1f.robust_z(ctrl127, med127, mad127).rename(["c127_zvv", "c127_zvh"])
    z_e134 = s1f.robust_z(event134, med134, mad134).rename(["e134_zvv", "e134_zvh"])

    return z_e127.addBands(z_c127).addBands(z_e134).addBands(gee.analysis_mask())


def sample_stack(stack: ee.Image, points: gpd.GeoDataFrame) -> pd.DataFrame:
    rows = []
    for start in range(0, len(points), BATCH):
        chunk = points.iloc[start : start + BATCH]
        feats = [
            ee.Feature(
                ee.Geometry.Point([geom.x, geom.y]),
                {"fid": str(fid), "class_nm": cls, "sgg_nm": sgg},
            )
            for fid, cls, sgg, geom in zip(chunk["farmmap_id"], chunk["class_nm"], chunk["sgg_nm"], chunk.geometry)
        ]
        fc = ee.FeatureCollection(feats)
        sampled = stack.sampleRegions(collection=fc, scale=20, geometries=False).getInfo()
        rows.extend(f["properties"] for f in sampled["features"])
        print(f"  샘플링 {min(start + BATCH, len(points)):,}/{len(points):,}")
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    df = df[df["valid"] == 1].copy()
    out = []
    for prefix, label in (("e127", "사건 07-24 (peak+7일)"), ("c127", "대조군 06-30 (peak-17일)"), ("e134", "사건 07-19 (peak+1.6일)")):
        vv, vh = df[f"{prefix}_zvv"], df[f"{prefix}_zvh"]
        df[f"{prefix}_open"] = ((vv < -Z_THRESHOLD) & (vh < -Z_THRESHOLD)).astype(int)
        df[f"{prefix}_double"] = ((vv > Z_THRESHOLD) & (vh > Z_THRESHOLD)).astype(int)
        for cls, grp in df.groupby("class_nm"):
            out.append(
                {
                    "구성": label,
                    "분류": cls,
                    "표본": len(grp),
                    "개방수면형%": round(grp[f"{prefix}_open"].mean() * 100, 2),
                    "이중반사형%": round(grp[f"{prefix}_double"].mean() * 100, 2),
                }
            )
    return pd.DataFrame(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="논/밭 분리 부정 대조군")
    parser.add_argument("--n-paddy", type=int, default=6000)
    parser.add_argument("--n-upland", type=int, default=6000)
    parser.add_argument("--n-other", type=int, default=2000)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "reference" / "exp02_paddy_upland.csv")
    args = parser.parse_args()

    gee.init()
    aoi = gee.chungnam_aoi()

    points = sample_points({"논": args.n_paddy, "밭": args.n_upland, "과수": args.n_other, "시설": args.n_other})
    print(f"표본 {len(points):,}점")
    print(points["class_nm"].value_counts().to_string())

    df = sample_stack(build_z_stack(aoi), points)
    print(f"\n샘플 회수 {len(df):,}점 (분석유효 {int(df['valid'].sum()):,})")

    summary = summarize(df)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.out, index=False, encoding="utf-8-sig")

    for label, grp in summary.groupby("구성", sort=False):
        print(f"\n[{label}]")
        print(grp[["분류", "표본", "개방수면형%", "이중반사형%"]].to_string(index=False))

    print("\n판정 기준: 밭에서 사건 > 대조군 이면 밭은 변화탐지로 판별 가능.")
    print("           논에서 사건 <= 대조군 이면 논은 별도 방법이 필요.")


if __name__ == "__main__":
    main()
