"""실험 01 — 2025-07 충남 호우 SAR 변화탐지와 부정 대조군.

질문
    Sentinel-1 변화탐지만으로 충남 농경지 침수를 판별할 수 있는가?

설계
    A. orbit 134 (07-19 06:31 KST, peak +1.6일) vs 사건 후 평상상태 (08-12, 08-24, 모두 S1C)
    B. orbit 127 (07-24 18:31 KST, peak +7일) vs 동일계절 다년 baseline (2021~2024 7월, S1A)
    C. **부정 대조군** — orbit 127 (06-30, peak **-17일**) vs 동일 baseline
       사건 전 영상이므로 침수 신호가 나오면 안 된다.

실행
    python notebooks/exp01_flood_change_detection.py

결과 해석은 docs/40_experiments.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.rs import gee, sentinel1_flood as s1f  # noqa: E402

OFFICIAL_RICE_KM2 = 149.4  # 2025-07 충남 벼 침수 14,944ha (전략문서 인용, 원문 대조 필요)
OFFICIAL_SOYBEAN_KM2 = 13.8  # 논콩 1,381ha


def main() -> None:
    gee.init()
    aoi = gee.chungnam_aoi()
    valid = gee.analysis_mask()
    crop = gee.cropland_mask()

    print(f"AOI {gee.area_km2(valid.multiply(0).add(1), aoi):,.1f} km2")
    print(f"분석유효역 {gee.area_km2(valid, aoi):,.1f} km2 | 농경지 {gee.area_km2(crop.And(valid), aoi):,.1f} km2")
    print(f"공식 집계 참고: 벼 {OFFICIAL_RICE_KM2} + 논콩 {OFFICIAL_SOYBEAN_KM2} = {OFFICIAL_RICE_KM2 + OFFICIAL_SOYBEAN_KM2} km2\n")

    def report(label: str, z) -> None:
        print(f"[{label}]")
        for mode, name in (("open", "개방수면형 z<-2"), ("double", "이중반사형 z>+2"), ("both", "합계")):
            m = s1f.flood_candidates(z, mode=mode, z_threshold=2.0, mask=valid)
            print(f"   {name:<18} 전체 {gee.area_km2(m, aoi):7.1f}   농경지 {gee.area_km2(m.And(crop), aoi):7.1f} km2")
        print()

    # --- A. orbit 134, 사건 후 평상상태 기준 -------------------------------
    ev134 = gee.s1_collection(aoi, *gee.kst_window("2025-07-19T00:00", "2025-07-19T23:59"), 134, "C").mosaic()
    ref134 = gee.s1_collection(aoi, *gee.kst_window("2025-08-07", "2025-08-31"), 134, "C")
    med134, mad134 = ref134.median(), ref134.map(lambda im: im.subtract(ref134.median()).abs()).median()
    report("A. orbit 134  07-19(peak+1.6일)  vs 8월 평상상태", s1f.robust_z(ev134, med134, mad134))

    # --- B/C. orbit 127, 동일계절 다년 baseline ----------------------------
    med127, mad127 = s1f.same_season_baseline(aoi, [2021, 2022, 2023, 2024], rel_orbit=127, platform="A")
    ev127 = gee.s1_collection(aoi, *gee.kst_window("2025-07-24", "2025-07-25"), 127, "A").mosaic()
    pre127 = gee.s1_collection(aoi, *gee.kst_window("2025-06-30", "2025-07-01"), 127, "A").mosaic()

    report("B. orbit 127  07-24(peak+7일)   vs 동일계절 2021-24", s1f.robust_z(ev127, med127, mad127))
    report("C. 부정 대조군 06-30(peak-17일) vs 동일계절 2021-24", s1f.robust_z(pre127, med127, mad127))

    print("C가 B보다 크면 이 방법은 침수가 아니라 논 물관리 주기를 탐지하고 있는 것이다.")


if __name__ == "__main__":
    main()
