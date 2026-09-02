"""관측 가능성 — 충남 상공 Sentinel-1 통과 시각 계산과 사건 관측 창 판정.

이 모듈이 답하는 질문은 하나다.
**"이 호우는 위성으로 확인할 수 있는 사건인가?"**

행정이 "위성으로 피해를 확인하자"고 결정할 때, 확인 가능한 사건인지 아닌지를
사전에 알려주는 곳이 없다. 그 결과:

    2023-07-14 충남 일강수 118.6mm (재난급) — orbit 127은 06-29 다음이 07-23.
                                              사건을 통째로 놓쳤다.
    2025-07-17 충남 일강수  74.9mm         — orbit 134가 07-19 06:32에 통과.
                                              peak +1.6일에 관측 성공.

같은 도, 같은 계절, 다른 결과다. 차이는 위성 궤도 주기와 사건 시각의 우연한 정렬뿐이다.
실험 04에서 이 차이가 판별 가능성을 21배 갈랐다 (논 25.20% vs 1.19%).

Sentinel-1 은 12일 반복 궤도다. 과거 통과 시각을 알면 미래 통과를 산술로 계산할 수 있다.
`data/reference/s1_acquisitions_chungnam.csv` 의 실측 이력으로 검증한다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSES_CSV = REPO_ROOT / "data" / "reference" / "s1_acquisitions_chungnam.csv"

REPEAT_DAYS = 12  # Sentinel-1 반복 주기
MIN_COVERAGE = 80.0

# 충남 전체를 덮는 궤도만 의미가 있다. orbit 54 는 충남을 2~3% 만 스친다 (docs/10_golden_event.md).
COVERING_ORBITS = (127, 134)


@dataclass
class Pass:
    """한 번의 위성 통과."""

    when: dt.datetime  # KST
    rel_orbit: int
    direction: str

    @property
    def label(self) -> str:
        return f"orbit {self.rel_orbit} {self.direction[:4]} {self.when:%Y-%m-%d %H:%M} KST"


def load_history(min_coverage: float = MIN_COVERAGE) -> pd.DataFrame:
    """전체커버 통과 이력."""
    df = pd.read_csv(PASSES_CSV, encoding="utf-8-sig")
    df = df[(df["coverage_pct"] >= min_coverage) & (df["rel_orbit"].isin(COVERING_ORBITS))].copy()
    df["when"] = pd.to_datetime(df["date"].astype(str) + " " + df["time_kst"])
    return df.sort_values("when").reset_index(drop=True)


def anchors(history: pd.DataFrame | None = None) -> dict[int, Pass]:
    """궤도별 기준 통과 — 가장 최근 실측 통과를 앵커로 쓴다."""
    history = load_history() if history is None else history
    out: dict[int, Pass] = {}
    for orbit, group in history.groupby("rel_orbit"):
        last = group.iloc[-1]
        out[int(orbit)] = Pass(last["when"].to_pydatetime(), int(orbit), str(last["direction"]))
    return out


def upcoming(start: dt.datetime, days: int = 14, history: pd.DataFrame | None = None) -> list[Pass]:
    """start 부터 days 일 안의 통과 예정 목록 (KST)."""
    out: list[Pass] = []
    for orbit, anchor in anchors(history).items():
        delta = (start - anchor.when).total_seconds() / 86400.0
        cycles = int(delta // REPEAT_DAYS) + 1
        when = anchor.when + dt.timedelta(days=REPEAT_DAYS * cycles)
        while when < start:
            when += dt.timedelta(days=REPEAT_DAYS)
        while when <= start + dt.timedelta(days=days):
            out.append(Pass(when, orbit, anchor.direction))
            when += dt.timedelta(days=REPEAT_DAYS)
    return sorted(out, key=lambda p: p.when)


def acquisition_reliability(history: pd.DataFrame | None = None, slots_per_summer: int = 9) -> dict[int, float]:
    """궤도별 촬영 신뢰도 — **통과 예정과 실제 촬영은 다르다.**

    Sentinel-1 은 12일마다 같은 지점 상공을 지나지만 매번 촬영하지는 않는다.
    ESA 관측계획에 따라 지역·시기별로 촬영 여부가 달라진다.
    충남 여름(6/1~9/15)에는 12일 주기로 최대 9회 통과 기회가 있는데 실제로는:

        orbit 127  2021년 8회, 2022년 7회, 2023년 6회, 2024년 5회, 2025년 7회 -> 평균 73%
        orbit 134  2021년 6회, 2022년 0회, 2023년 1회, 2024년 0회, 2025년 4회 -> 평균 24%

    orbit 134 는 2022·2024 여름에 전체커버 촬영이 **한 번도 없었다.**
    따라서 통과 예정 시각만 제시하면 안 되고 이 확률을 함께 표시해야 한다.
    """
    history = load_history() if history is None else history
    df = history.copy()
    df["year"] = df["when"].dt.year
    counts = df.pivot_table(index="year", columns="rel_orbit", values="when", aggfunc="count").fillna(0)
    return {int(orbit): float((counts[orbit] / slots_per_summer).mean()) for orbit in counts.columns}


def observation_window(peak: dt.datetime, days: int = 7, history: pd.DataFrame | None = None) -> dict:
    """호우 peak 시각에 대한 관측 창 판정.

    반환
        passes       peak 이후 days 일 안의 통과 목록
        best         peak 에 가장 가까운 통과
        lag_hours    peak 로부터 경과 시간
        grade        A: peak+48시간 이내 — 침수 관측 신뢰 가능
                     B: peak+48~120시간 — 잔존 침수만 관측, 논은 이미 배수됐을 수 있음
                     C: peak+120시간 초과 또는 통과 없음 — 위성 확인 불가, 현장조사 필요

    등급 경계는 실험 04의 실측에서 왔다.
        peak +1.6일(38시간) 관측 -> 논 이중반사 25.20%
        peak +7.0일(168시간) 관측 -> 논 1.19%
    """
    passes = list(upcoming(peak, days, history))
    if not passes:
        return {"passes": [], "best": None, "lag_hours": None, "grade": "C",
                "reliability": 0.0,
                "reason": f"peak 이후 {days}일 안에 충남 전체커버 통과 없음"}

    reliability = acquisition_reliability(history)
    best = passes[0]
    lag = (best.when - peak).total_seconds() / 3600.0
    prob = reliability.get(best.rel_orbit, 0.0)

    if lag <= 48:
        grade, reason = "A", "peak 48시간 이내 관측 — 침수 범위 판정 신뢰 가능"
    elif lag <= 120:
        grade, reason = "B", "peak 48~120시간 — 잔존 침수만 관측. 논은 배수됐을 수 있음"
    else:
        grade, reason = "C", "peak 120시간 초과 — 위성 확인 부적합. 현장조사 필요"

    # 통과 예정이어도 촬영되지 않을 수 있다. 촬영률이 낮은 궤도는 등급을 낮춘다.
    if grade == "A" and prob < 0.4:
        grade = "B"
        reason = f"시각은 좋으나 이 궤도(orbit {best.rel_orbit})의 여름 촬영률이 {prob*100:.0f}% 로 낮음"

    return {"passes": passes, "best": best, "lag_hours": round(lag, 1),
            "grade": grade, "reliability": round(prob, 2), "reason": reason}
