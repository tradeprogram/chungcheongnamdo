"""수출이 도중에 멈춰도 있는 것까지로 침수빈도 parquet 을 만든다.

`build_susceptibility_v2.py` 는 젖은 관측을 먼저 끝내고 마른 관측으로 넘어간다.
젖은 쪽만 끝난 상태에서 아침을 맞으면 화면에 쓸 값이 하나도 없는 것보다
**대조군 없이라도 침수빈도가 있는 편이 낫다.** 이 스크립트는 부분 결과를 합쳐
쓸 수 있는 상태로 만든다.

  - freq_{tag}.tif 가 있으면 그대로 쓴다
  - 없고 freq_parts/{tag}_p*.tif 가 하나라도 있으면 그것들만 합친다
  - 둘 다 없으면 그 tag 는 건너뛴다 (dry 가 없으면 대조군 없이 저장)

실행
    python notebooks/finalize_susceptibility.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from notebooks.build_susceptibility_v2 import COV_DIR, OUT, PART_DIR, combine  # noqa: E402
from src.features import zonal  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


def raster_for(tag: str) -> Path | None:
    merged = COV_DIR / f"freq_{tag}.tif"
    if merged.exists():
        return merged
    parts = sorted(PART_DIR.glob(f"{tag}_p*.tif"))
    if not parts:
        return None
    print(f"  {tag}: 부분 {len(parts)}개만 있음 — 그것까지로 합친다")
    return combine(parts, COV_DIR / f"freq_{tag}_partial.tif")


def main() -> None:
    parcels = gpd.read_parquet(
        REPO_ROOT / "data" / "processed" / "farmmap" / "chungnam_2021.parquet",
        columns=["farmmap_id", "class_nm", "sgg_nm", "area_m2", "geometry"],
    ).reset_index(drop=True)
    print(f"필지 {len(parcels):,}개")

    index = None
    frames = []
    for tag in ("wet", "dry"):
        path = raster_for(tag)
        if path is None:
            print(f"  {tag}: 자료 없음 — 건너뜀")
            continue
        if index is None:
            print("필지 인덱스 래스터화...")
            index = zonal.build_index(path, parcels)
        stats = zonal.parcel_means_v2(path, parcels, names=["n_flag", "n_obs", "freq"], index=index)
        got = stats["n_obs"].fillna(0) > 0
        print(f"  {tag}: 값이 있는 필지 {got.mean()*100:.1f}% "
              f"(면적집계 {(stats['method']=='area').mean()*100:.1f}%)")
        frames.append(stats.rename(columns={c: f"{tag}_{c}" for c in stats.columns}))

    if not frames:
        print("합칠 자료가 없다.")
        return

    out = pd.concat(frames, axis=1)
    for col in ("farmmap_id", "class_nm", "sgg_nm", "area_m2"):
        out[col] = parcels[col].to_numpy()
    if "dry_freq" not in out.columns:
        print("\n주의: 대조군(dry)이 없다. 오탐 여부를 가릴 수 없으므로 화면에 그렇게 밝혀야 한다.")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT)
    print(f"\n{len(out):,} 필지 -> {OUT}")


if __name__ == "__main__":
    main()
