"""기획서에 쓴 숫자를 자료와 대조한다.

숫자 하나가 틀리면 나머지도 의심받는다. 실제로 필지 판독 비율(10.6%)이 틀렸던 적이
있고, 그것은 분류 버그였지 반올림이 아니었다. 그래서 본문에서 검증 가능한 수치를
뽑아 원본 자료로 다시 계산해 맞춰 본다.

외부 출처 수치(농식품부 침수 면적, 경남 인력 투입 등)는 여기서 검증할 수 없다.
그것들은 `docs/83_source_verification.md` 에 출처를 남겨 두었다.

실행
    python notebooks/verify_proposal_numbers.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]
PROPOSAL = REPO_ROOT / "docs" / "80_proposal_draft.md"
STATS_JSON = REPO_ROOT / "web" / "data" / "stats.json"
FIELD_STATS = REPO_ROOT / "data" / "processed" / "features" / "field_event_stats.parquet"
FARMMAP = REPO_ROOT / "data" / "processed" / "farmmap" / "chungnam_2021.parquet"

PRIMARY = "o134_2025-07-19"
LATE = "o127_2025-07-24"


def check(name: str, claimed, actual, tol: float = 0.0) -> bool:
    if isinstance(claimed, (int, float)) and isinstance(actual, (int, float)):
        ok = abs(claimed - actual) <= tol
    else:
        ok = claimed == actual
    mark = "OK  " if ok else "틀림"
    print(f"  [{mark}] {name:<34} 본문 {claimed}  자료 {actual}")
    return ok


def main() -> None:
    text = PROPOSAL.read_text(encoding="utf-8")
    stats = json.loads(STATS_JSON.read_text(encoding="utf-8"))
    fs = pd.read_parquet(FIELD_STATS, columns=["farmmap_id", "event_id", "n_valid",
                                               "method", "double_fraction", "steep", "emd_nm"])
    prim = fs[fs["event_id"] == PRIMARY]
    n = len(prim)

    print("본문에 있는 문자열이 자료와 맞는지 확인한다\n")
    results = []

    results.append(check("누적 관측 횟수", 427, stats["n_passes"]))
    results.append(check("호우 사건 수", 77, stats["n_storms"]))
    results.append(check("등급 A 사건 수", 17, stats["n_grade_a"]))
    results.append(check("등급 A 비율 %", 22, round(stats["pct_grade_a"])))
    results.append(check("등급 B·C 비율 %", 78, 100 - round(stats["pct_grade_a"])))

    parcels = pd.read_parquet(FARMMAP, columns=["farmmap_id"])
    results.append(check("충남 농경지 필지 수", 1_434_057, len(parcels)))

    area = (prim["method"] == "area").mean() * 100
    point = ((prim["method"] == "point") & (prim["n_valid"] > 0)).mean() * 100
    none = (prim["n_valid"] <= 0).mean() * 100
    results.append(check("면적 집계 %", 89.4, round(area, 1), 0.05))
    results.append(check("대표점 표본 %", 9.7, round(point, 1), 0.05))
    results.append(check("판독 불가 %", 0.9, round(none, 1), 0.05))
    results.append(check("판독값 산출 %", 99.1, round((prim["n_valid"] > 0).mean() * 100, 1), 0.05))

    buyeo = fs[(fs["emd_nm"] == "부여읍") & (fs["n_valid"] > 0) & (~fs["steep"])]
    b_prim = buyeo[buyeo["event_id"] == PRIMARY]
    b_late = buyeo[buyeo["event_id"] == LATE]
    results.append(check("부여읍 판독 필지", 10_270, len(b_prim)))
    results.append(check("07-19 침수 후보 필지", 2_426, int((b_prim["double_fraction"] >= 0.5).sum())))
    results.append(check("07-24 침수 후보 필지", 121, int((b_late["double_fraction"] >= 0.5).sum())))

    # 궤도별 여름 촬영률 — 지표를 다시 구현하지 않는다. 검산한다면서 다르게 세면
    # 본문이 아니라 검산이 틀린다. 실제로 여기서 한 번 헛경보를 냈다.
    from src.rs import observability as ob  # noqa: E402
    full = {k: round(v * 100) for k, v in ob.acquisition_reliability().items()}
    recent3 = {k: round(v * 100) for k, v in ob.acquisition_reliability(recent_years=3).items()}
    results.append(check("궤도 127 여름 촬영률 % (전체)", 87, full.get(127)))
    results.append(check("궤도 134 여름 촬영률 % (전체)", 63, full.get(134)))
    results.append(check("궤도 127 여름 촬영률 % (최근 3년)", 67, recent3.get(127)))
    results.append(check("궤도 134 여름 촬영률 % (최근 3년)", 37, recent3.get(134)))

    # 위성 한 대 고장 해에 한 궤도의 충남 전역 촬영이 0건이었는가
    hist = ob.load_history()
    hist = hist[hist["when"].dt.month.between(6, 9)]
    for year in (2022, 2024):
        yr = hist[hist["when"].dt.year == year]
        per_orbit = yr.groupby("rel_orbit").size().reindex([127, 134]).fillna(0).astype(int)
        results.append(check(f"{year} 여름 촬영 0건인 궤도 수", 1, int((per_orbit == 0).sum())))

    # 폐기한 예측 모델의 판별력.
    # 기획서가 말하는 모델은 LightGBM 이다. A_rule·B_logistic 은 비교용 기준선이므로
    # 셋을 한데 묶으면 0.43~0.58 이 나와 본문(0.50~0.55)이 틀린 것처럼 보인다.
    ev = pd.read_csv(REPO_ROOT / "data" / "reference" / "model_event_holdout.csv",
                     encoding="utf-8-sig")
    gbm = ev[ev["model"] == "C_lightgbm"]["roc_auc"]
    lo, hi = round(gbm.min(), 2), round(gbm.max(), 2)
    results.append(check("LightGBM 사건 홀드아웃 ROC-AUC 하한", 0.50, lo, 0.005))
    results.append(check("LightGBM 사건 홀드아웃 ROC-AUC 상한", 0.55, hi, 0.005))

    # 본문이 실제로 그 문자열을 담고 있는지도 본다 — 자료만 맞고 본문이 낡을 수 있다
    print("\n본문 문자열 존재 확인")
    for token in ("16,709ha", "1,434,057", "427", "77건", "17건(22%)", "99.1%",
                  "89.4%", "9.7%", "0.9%", "10,270", "2,426", "121",
                  "87%·63%", "67%·37%"):
        present = token in text
        print(f"  [{'OK  ' if present else '없음'}] {token}")
        results.append(present)

    bad = results.count(False)
    print(f"\n{len(results)}건 중 {bad}건 불일치")
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
