"""WebGIS 데모용 데이터 생성.

발표 중 API 실패나 GEE latency 로 데모가 끊기면 안 된다.
따라서 백엔드 없이 **미리 만들어 둔 파일만으로** 동작하는 정적 데이터를 만든다.

산출 (web/data/)
    events.json          사건 목록 + 관측 창 등급 + 필지 통계
    overlays/<id>.png    사건별 침수 후보 오버레이 (EPSG:4326 로 워핑)
    overlays/<id>.json   오버레이 경계 좌표
    emd_ranking.json     읍면동 침수빈도 순위
    sgg.geojson          시군 경계 (기존 AOI 재사용)

실행
    python notebooks/build_web_data.py
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import Resampling, calculate_default_transform, reproject

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.rs import observability as obs  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
Z_DIR = REPO_ROOT / "data" / "processed" / "z"
WEB_DATA = REPO_ROOT / "web" / "data"
OVERLAY_DIR = WEB_DATA / "overlays"

Z_THRESHOLD = 2.0
MAX_WIDTH = 1600  # 오버레이 PNG 가로 픽셀 상한

# 사건 정의: id, 표시명, 관측 KST, peak KST, 궤도
EVENTS = [
    ("o127_2021-07-21", "2021-07-21 관측", "2021-07-21 18:31", None, 127),
    ("o127_2022-07-16", "2022-07-16 관측", "2022-07-16 18:31", None, 127),
    ("o127_2023-07-23", "2023-07-23 호우", "2023-07-23 18:31", "2023-07-23 12:00", 127),
    ("o127_2024-07-17", "2024-07-17 관측", "2024-07-17 18:31", None, 127),
    ("o127_2025-07-24", "2025-07 호우 (늦은 관측)", "2025-07-24 18:31", "2025-07-17 15:00", 127),
    ("o134_2025-07-19", "2025-07 호우 (제때 관측)", "2025-07-19 06:31", "2025-07-17 15:00", 134),
]


def make_overlay(event_id: str) -> dict | None:
    """z-score 래스터에서 침수 후보 마스크를 만들어 4326 PNG 로 저장한다."""
    src_path = Z_DIR / f"{event_id}.tif"
    if not src_path.exists():
        print(f"  [skip] {src_path.name} 없음")
        return None

    with rasterio.open(src_path) as src:
        scale = max(1, int(np.ceil(src.width / MAX_WIDTH)))
        out_w, out_h = src.width // scale, src.height // scale
        zvv = src.read(1, out_shape=(out_h, out_w), resampling=Resampling.average)
        zvh = src.read(2, out_shape=(out_h, out_w), resampling=Resampling.average)
        valid = src.read(3, out_shape=(out_h, out_w), resampling=Resampling.average)
        transform = src.transform * src.transform.scale(scale, scale)

        flood = ((zvv > Z_THRESHOLD) & (zvh > Z_THRESHOLD) & (valid > 0.5)).astype(np.uint8)

        dst_crs = "EPSG:4326"
        dst_transform, dst_w, dst_h = calculate_default_transform(
            src.crs, dst_crs, out_w, out_h, *rasterio.transform.array_bounds(out_h, out_w, transform)
        )
        warped = np.zeros((dst_h, dst_w), dtype=np.uint8)
        reproject(flood, warped, src_transform=transform, src_crs=src.crs,
                  dst_transform=dst_transform, dst_crs=dst_crs, resampling=Resampling.nearest)

    # RGBA — 침수 후보만 불투명
    rgba = np.zeros((dst_h, dst_w, 4), dtype=np.uint8)
    mask = warped == 1
    rgba[mask] = [37, 99, 235, 200]  # 파랑

    from PIL import Image

    OVERLAY_DIR.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(OVERLAY_DIR / f"{event_id}.png", optimize=True)

    west, south, east, north = rasterio.transform.array_bounds(dst_h, dst_w, dst_transform)
    bounds = {"coordinates": [[west, north], [east, north], [east, south], [west, south]]}
    (OVERLAY_DIR / f"{event_id}.json").write_text(json.dumps(bounds), encoding="utf-8")
    print(f"  {event_id}: {dst_w}x{dst_h}px, 침수후보 {int(mask.sum()):,}px")
    return bounds


def main() -> None:
    WEB_DATA.mkdir(parents=True, exist_ok=True)
    history = obs.load_history()
    summary = pd.read_csv(REPO_ROOT / "data" / "reference" / "field_event_summary.csv", encoding="utf-8-sig")
    rain = pd.read_csv(REPO_ROOT / "data" / "reference" / "s1_passes_rainfall.csv", encoding="utf-8-sig")
    rain["date"] = pd.to_datetime(rain["date"]).dt.strftime("%Y-%m-%d")
    reliability = obs.acquisition_reliability(history)

    print("오버레이 생성")
    events = []
    for event_id, label, obs_kst, peak_kst, orbit in EVENTS:
        make_overlay(event_id)

        stats = summary[summary["event_id"] == event_id].set_index("class_nm")
        obs_dt = dt.datetime.strptime(obs_kst, "%Y-%m-%d %H:%M")
        r = rain[(rain["date"] == obs_dt.strftime("%Y-%m-%d")) & (rain["rel_orbit"] == orbit)]

        entry = {
            "id": event_id,
            "label": label,
            "observed_kst": obs_kst,
            "rel_orbit": orbit,
            "acquisition_reliability": round(reliability.get(orbit, 0.0), 2),
            "rain3d_mm": float(r["rain3d"].iloc[0]) if len(r) else None,
            "rain1d_mm": float(r["rain1d"].iloc[0]) if len(r) else None,
            "paddy_pct": float(stats.loc["논", "pct_double_ge50"]) if "논" in stats.index else None,
            "upland_pct": float(stats.loc["밭", "pct_double_ge50"]) if "밭" in stats.index else None,
        }
        if peak_kst:
            peak = dt.datetime.strptime(peak_kst, "%Y-%m-%d %H:%M")
            lag = (obs_dt - peak).total_seconds() / 3600
            # 사후 판독이므로 그 관측 자체의 지연으로 채점한다.
            # peak 기준 최선의 통과를 찾는 observation_window 와는 다른 질문이다.
            graded = obs.grade_observation(lag, orbit, history)
            entry.update({
                "peak_kst": peak_kst,
                "lag_hours": round(lag, 1),
                "grade": graded["grade"],
                "grade_reason": graded["reason"],
            })
        events.append(entry)

    (WEB_DATA / "events.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    # 읍면동 순위
    emd = pd.read_csv(REPO_ROOT / "data" / "reference" / "emd_flood_frequency.csv", encoding="utf-8-sig")
    emd = emd.sort_values("wet_freq", ascending=False).head(60)
    (WEB_DATA / "emd_ranking.json").write_text(
        emd.to_json(orient="records", force_ascii=False), encoding="utf-8"
    )

    shutil.copy(REPO_ROOT / "data" / "aoi" / "chungnam_sgg.geojson", WEB_DATA / "sgg.geojson")

    # 통과 예보
    upcoming = [{"when": p.when.strftime("%Y-%m-%d %H:%M"), "orbit": p.rel_orbit,
                 "direction": p.direction, "reliability": round(reliability.get(p.rel_orbit, 0), 2)}
                for p in obs.upcoming(dt.datetime.now(), 21, history)]
    (WEB_DATA / "upcoming.json").write_text(json.dumps(upcoming, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n사건 {len(events)}건, 읍면동 {len(emd)}개, 통과예보 {len(upcoming)}건 -> {WEB_DATA}")
    for e in events:
        print(f"  {e['label']:<26} 논 {e['paddy_pct']:>6}%  등급 {e.get('grade','-')}"
              f"  촬영률 {e['acquisition_reliability']*100:.0f}%")


if __name__ == "__main__":
    main()
