"""ERA5 재분석 일강수량 조회 (Open-Meteo, 인증 불필요).

**보조자료 전용.** 모델 feature로 쓰는 강우는 KMA 기상자료개방포털이 primary다.
이 모듈의 용도는 하나다: 인증키 발급 전에 **호우 사건 window를 빠르게 특정**하는 것.

ERA5는 약 9km 격자 재분석이라 국지 집중호우의 peak 강도를 과소평가한다.
사건 날짜를 좁히는 데는 충분하지만, 필지 단위 feature로는 쓰지 않는다.

사용
    python src/features/rainfall_era5.py --start 2025-06-01 --end 2025-08-31
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import httpx
import pandas as pd

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"


def fetch_daily_precip(repo_root: Path, start: str, end: str) -> pd.DataFrame:
    """충남 시군구 중심점별 일강수량(mm)."""
    sgg = gpd.read_file(repo_root / "data" / "aoi" / "chungnam_sgg.geojson")
    centroids = sgg.to_crs("EPSG:5179").geometry.centroid.to_crs(4326)

    resp = httpx.get(
        ARCHIVE,
        params={
            "latitude": ",".join(f"{p.y:.4f}" for p in centroids),
            "longitude": ",".join(f"{p.x:.4f}" for p in centroids),
            "start_date": start,
            "end_date": end,
            "daily": "precipitation_sum",
            "timezone": "Asia/Seoul",
        },
        timeout=120,
    )
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload, dict):
        payload = [payload]

    series = {
        name: pd.Series(loc["daily"]["precipitation_sum"], index=loc["daily"]["time"])
        for name, loc in zip(sgg["sgg_nm"], payload)
    }
    df = pd.DataFrame(series)
    df.index = pd.to_datetime(df.index)
    df.index.name = "date"
    return df


def rank_events(df: pd.DataFrame, window: int = 3, top: int = 10) -> pd.DataFrame:
    """도 평균 일강수량과 누적강수량 기준 상위 일자."""
    summary = pd.DataFrame(
        {
            "mean_mm": df.mean(axis=1),
            "max_mm": df.max(axis=1),
            f"mean_{window}d_mm": df.mean(axis=1).rolling(window).sum(),
        }
    )
    return summary.sort_values(f"mean_{window}d_mm", ascending=False).head(top).round(1)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="충남 일강수량 (ERA5 보조자료)")
    parser.add_argument("--start", default="2025-06-01")
    parser.add_argument("--end", default="2025-08-31")
    parser.add_argument("--out", type=Path, default=repo_root / "data" / "reference" / "chungnam_rain_era5.csv")
    args = parser.parse_args()

    df = fetch_daily_precip(repo_root, args.start, args.end)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.round(1).to_csv(args.out, encoding="utf-8-sig")

    print("[3일 누적 상위 10일 — 도 평균]")
    print(rank_events(df).to_string())
    print(f"\nsaved: {args.out}")


if __name__ == "__main__":
    main()
