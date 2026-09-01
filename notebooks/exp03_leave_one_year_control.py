"""실험 03 — leave-one-year-out 대조군.

실험 01·02의 대조군(06-30)은 공정하지 않았다.
baseline이 7월 영상으로 만들어졌는데 대조군만 6월 30일이어서,
탐지된 이상치의 상당 부분이 침수가 아니라 **생육단계 차이**였다.
그래서 논뿐 아니라 밭·과수·시설에서도 대조군이 사건보다 높게 나왔다.

올바른 대조군은 **같은 계절 창 안의 다른 연도**다.

    orbit 127 ASC, 7월 중순~하순 통과 (모두 S1A, 동일 궤도)
        2021-07-21   2022-07-16   2023-07-23   2024-07-17   2025-07-24(사건)

    각 연도를 평가할 때 baseline은 그 연도를 뺀 나머지 연도의 7월 영상으로 만든다.
    2025만 호우 사건이 있었으므로, 방법에 판별력이 있다면
    2025의 이상치 비율이 2021~2024보다 뚜렷하게 높아야 한다.

실행
    python notebooks/exp03_leave_one_year_control.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import ee
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.rs import gee, sentinel1_flood as s1f  # noqa: E402
from notebooks.exp02_paddy_upland_control import sample_points, BATCH  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
Z_THRESHOLD = 2.0

# 7월 중순~하순, orbit 127 ASC, S1A. KST 날짜.
YEAR_PASS = {
    2021: ("2021-07-21", "2021-07-22"),
    2022: ("2022-07-16", "2022-07-17"),
    2023: ("2023-07-23", "2023-07-24"),
    2024: ("2024-07-17", "2024-07-18"),
    2025: ("2025-07-24", "2025-07-25"),  # Golden Event (peak 07-17 +7일)
}
BASELINE_YEARS = [2021, 2022, 2023, 2024]


def build_stack(aoi: ee.Geometry) -> ee.Image:
    """연도별 leave-one-out z-score를 다중밴드로 묶는다."""
    stack = None
    for year, (d0, d1) in YEAR_PASS.items():
        base_years = [y for y in BASELINE_YEARS if y != year]
        med, mad = s1f.same_season_baseline(aoi, base_years, rel_orbit=127, platform="A")
        image = gee.s1_collection(aoi, *gee.kst_window(d0, d1), 127, "A").mosaic()
        z = s1f.robust_z(image, med, mad).rename([f"y{year}_zvv", f"y{year}_zvh"])
        stack = z if stack is None else stack.addBands(z)
    return stack.addBands(gee.analysis_mask())


def sample_stack(stack: ee.Image, points) -> pd.DataFrame:
    rows = []
    for start in range(0, len(points), BATCH):
        chunk = points.iloc[start : start + BATCH]
        fc = ee.FeatureCollection(
            [
                ee.Feature(ee.Geometry.Point([g.x, g.y]), {"class_nm": c})
                for c, g in zip(chunk["class_nm"], chunk.geometry)
            ]
        )
        sampled = stack.sampleRegions(collection=fc, scale=20, geometries=False).getInfo()
        rows.extend(f["properties"] for f in sampled["features"])
        print(f"  샘플링 {min(start + BATCH, len(points)):,}/{len(points):,}")
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    df = df[df["valid"] == 1].copy()
    out = []
    for year in YEAR_PASS:
        vv, vh = df[f"y{year}_zvv"], df[f"y{year}_zvh"]
        df["open"] = ((vv < -Z_THRESHOLD) & (vh < -Z_THRESHOLD)).astype(int)
        df["double"] = ((vv > Z_THRESHOLD) & (vh > Z_THRESHOLD)).astype(int)
        for cls, grp in df.groupby("class_nm"):
            out.append(
                {
                    "연도": year,
                    "분류": cls,
                    "표본": len(grp),
                    "개방수면형%": round(grp["open"].mean() * 100, 2),
                    "이중반사형%": round(grp["double"].mean() * 100, 2),
                }
            )
    return pd.DataFrame(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="leave-one-year-out 대조군")
    parser.add_argument("--n-paddy", type=int, default=6000)
    parser.add_argument("--n-upland", type=int, default=6000)
    parser.add_argument("--n-other", type=int, default=2000)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "reference" / "exp03_leave_one_year.csv")
    args = parser.parse_args()

    gee.init()
    aoi = gee.chungnam_aoi()

    points = sample_points({"논": args.n_paddy, "밭": args.n_upland, "과수": args.n_other, "시설": args.n_other})
    print(f"표본 {len(points):,}점")

    df = sample_stack(build_stack(aoi), points)
    print(f"\n샘플 회수 {len(df):,}점 (분석유효 {int(df['valid'].sum()):,})")

    summary = summarize(df)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.out, index=False, encoding="utf-8-sig")

    for metric in ("개방수면형%", "이중반사형%"):
        print(f"\n[{metric}]  행=연도, 열=분류   (2025만 호우 사건)")
        print(summary.pivot(index="연도", columns="분류", values=metric).to_string())


if __name__ == "__main__":
    main()
