"""기획서 [그림 1] 생성 — 같은 사건, 두 관측.

2025년 7월 호우(peak 07-17)를 두 번 관측한 결과를 위성영상 위에 나란히 놓는다.
왼쪽 07-19(peak+40시간, 등급 A), 오른쪽 07-24(peak+172시간, 등급 C).

UI 스크린샷이 아니라 스크립트로 그린다. 배율·범위·색을 고정할 수 있어
재현되고, 인쇄 해상도를 맞출 수 있다.

배경은 Esri World Imagery 타일을 직접 받아 합성한다 (contextily 미설치).

실행
    python notebooks/make_figure.py --emd 부여읍 --zoom 15
"""

from __future__ import annotations

import argparse
import io
import math
import sys
from pathlib import Path

import geopandas as gpd
import httpx
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Patch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]
FARMMAP = REPO_ROOT / "data" / "processed" / "farmmap" / "chungnam_2021.parquet"
STATS = REPO_ROOT / "data" / "processed" / "features" / "field_event_stats.parquet"
OUT_DIR = REPO_ROOT / "docs" / "90_submission"

TILE_URL = "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
TILE_SIZE = 256

EVENTS = [
    ("o134_2025-07-19", "2025-07-19 06:31 관측", "peak +40시간 · 등급 A"),
    ("o127_2025-07-24", "2025-07-24 18:31 관측", "peak +172시간 · 등급 C"),
]

# 한글 폰트 — 없으면 그림의 라벨이 깨진다
for name in ("Malgun Gothic", "NanumGothic", "AppleGothic"):
    if any(name in f.name for f in mpl.font_manager.fontManager.ttflist):
        mpl.rcParams["font.family"] = name
        break
mpl.rcParams["axes.unicode_minus"] = False


def deg2num(lon: float, lat: float, z: int) -> tuple[float, float]:
    lat_r = math.radians(lat)
    n = 2.0**z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n
    return x, y


def num2deg(x: float, y: float, z: int) -> tuple[float, float]:
    n = 2.0**z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


def fetch_basemap(bounds: tuple[float, float, float, float], zoom: int) -> tuple[Image.Image, tuple]:
    """bounds(EPSG:4326) 를 덮는 타일을 받아 한 장으로 합친다."""
    west, south, east, north = bounds
    x0, y0 = deg2num(west, north, zoom)
    x1, y1 = deg2num(east, south, zoom)
    xa, xb = int(math.floor(x0)), int(math.floor(x1))
    ya, yb = int(math.floor(y0)), int(math.floor(y1))

    cols, rows = xb - xa + 1, yb - ya + 1
    print(f"  타일 {cols}x{rows} = {cols*rows}장 (zoom {zoom})")
    canvas = Image.new("RGB", (cols * TILE_SIZE, rows * TILE_SIZE))
    headers = {"User-Agent": "Mozilla/5.0"}
    with httpx.Client(timeout=60, headers=headers, follow_redirects=True) as client:
        for i, xt in enumerate(range(xa, xb + 1)):
            for j, yt in enumerate(range(ya, yb + 1)):
                r = client.get(TILE_URL.format(z=zoom, x=xt, y=yt))
                r.raise_for_status()
                canvas.paste(Image.open(io.BytesIO(r.content)), (i * TILE_SIZE, j * TILE_SIZE))

    # 합쳐진 캔버스의 실제 지리 범위
    w, n = num2deg(xa, ya, zoom)
    e, s = num2deg(xb + 1, yb + 1, zoom)
    return canvas, (w, s, e, n)


def flood_color(v: float) -> str:
    if not np.isfinite(v):
        return "#9ca3af"
    if v >= 0.5:
        return "#1d4ed8"
    if v >= 0.2:
        return "#60a5fa"
    return "#facc15"


def main() -> None:
    parser = argparse.ArgumentParser(description="기획서 그림 생성")
    parser.add_argument("--emd", default="부여읍")
    parser.add_argument("--zoom", type=int, default=15)
    parser.add_argument("--pad", type=float, default=0.0, help="범위 여백 비율")
    parser.add_argument("--out", type=Path, default=OUT_DIR / "figure1_two_observations.png")
    args = parser.parse_args()

    print(f"필지 로드 ({args.emd})")
    parcels = gpd.read_parquet(
        FARMMAP, columns=["farmmap_id", "class_nm", "emd_nm", "sgg_nm", "area_m2", "geometry"]
    )
    sub = parcels[parcels["emd_nm"] == args.emd].copy()
    if sub.empty:
        raise SystemExit(f"'{args.emd}' 필지를 찾을 수 없습니다.")

    stats = pd.read_parquet(STATS, columns=["farmmap_id", "event_id", "double_fraction", "n_valid"])
    stats = stats[stats["event_id"].isin([e[0] for e in EVENTS])]
    wide = stats.pivot_table(index="farmmap_id", columns="event_id", values="double_fraction")
    sub = sub.merge(wide, on="farmmap_id", how="left")
    print(f"  필지 {len(sub):,}개")

    # 침수가 잘 드러나는 구역으로 범위를 좁힌다 — 07-19 침수 필지의 밀집 지역
    flooded = sub[sub[EVENTS[0][0]] >= 0.5]
    focus = flooded if len(flooded) > 50 else sub
    cx, cy = focus.geometry.centroid.x.median(), focus.geometry.centroid.y.median()
    # 기획서는 3페이지를 넘길 수 없고, 그림 하나가 0.38페이지를 먹는다.
    # 정사각 범위를 가로로 눕히면 같은 필지 수를 보여주면서 높이가 줄어든다.
    # 지도 두 장을 나란히 두는 구성이므로 가로 확장이 판독에 손해가 아니다.
    half_x, half_y = 1900, 1060  # m
    sub_m = sub.cx[cx - half_x : cx + half_x, cy - half_y : cy + half_y]
    print(f"  표시 범위 내 필지 {len(sub_m):,}개")

    sub_wgs = sub_m.to_crs(4326)
    bounds = tuple(sub_wgs.total_bounds)
    print("배경 타일 수집")
    img, img_bounds = fetch_basemap(bounds, args.zoom)

    fig, axes = plt.subplots(1, 2, figsize=(13, 4.1), dpi=200)
    extent = [img_bounds[0], img_bounds[2], img_bounds[1], img_bounds[3]]

    for ax, (event_id, title, sub_title) in zip(axes, EVENTS):
        ax.imshow(np.asarray(img), extent=extent, origin="upper")
        vals = sub_wgs[event_id] if event_id in sub_wgs.columns else pd.Series(np.nan, index=sub_wgs.index)
        colors = [flood_color(v) for v in vals]
        sub_wgs.plot(ax=ax, color=colors, alpha=0.62, edgecolor="white", linewidth=0.25)

        n_flood = int((vals >= 0.5).sum())
        ax.set_xlim(bounds[0], bounds[2])
        ax.set_ylim(bounds[1], bounds[3])
        ax.set_xticks([]); ax.set_yticks([])
        ax.set_title(f"{title}\n{sub_title}", fontsize=12, pad=8)
        ax.text(0.02, 0.03, f"침수 후보 필지 {n_flood:,}개 / {len(sub_wgs):,}개",
                transform=ax.transAxes, fontsize=10, color="white",
                bbox=dict(facecolor="#0f172a", alpha=0.75, edgecolor="none", pad=4))
        for s in ax.spines.values():
            s.set_edgecolor("#94a3b8"); s.set_linewidth(0.8)

    handles = [
        Patch(facecolor="#1d4ed8", alpha=0.62, edgecolor="white", label="침수율 50% 이상"),
        Patch(facecolor="#60a5fa", alpha=0.62, edgecolor="white", label="20~50%"),
        Patch(facecolor="#facc15", alpha=0.62, edgecolor="white", label="20% 미만"),
    ]
    # 판독 불가 항목은 두지 않는다. 마스크를 걷어낸 뒤 이 구역의 필지는 전량 판독된다.
    # 실제로 회색이 나오는 필지가 있으면 그때 범례에 되살린다.
    if any(not np.isfinite(v) for e in EVENTS for v in sub_m[e[0]]):
        handles.append(Patch(facecolor="#9ca3af", alpha=0.62, edgecolor="white", label="판독 불가"))
    # 그림 높이를 줄이면 아래쪽 요소의 절대 간격도 같이 줄어 서로 겹친다.
    # 범례를 위, 설명을 아래로 두고 각각의 y 를 명시한다.
    fig.legend(handles=handles, loc="lower center", ncol=4, frameon=False, fontsize=10,
               bbox_to_anchor=(0.5, 0.055))
    # 큰 제목과 출처 줄은 넣지 않는다. 기획서 본문의 캡션과 ⑤ 필요 데이터가
    # 같은 말을 하고 있고, 3페이지 제한에서는 중복 한 줄이 곧 잘려나갈 한 줄이다.
    fig.text(0.5, 0.018, f"표시 필지 {len(sub_m):,}개 전량 판독",
             ha="center", fontsize=8.5, color="#475569")
    fig.tight_layout(rect=[0, 0.115, 1, 0.99])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, bbox_inches="tight", facecolor="white")
    print(f"\n-> {args.out}  ({args.out.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
