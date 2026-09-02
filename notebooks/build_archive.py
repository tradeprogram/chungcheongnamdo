"""충남 호우 사건·위성관측 전체 아카이브 (2017 ~ 현재).

일부 사례만 보여주면 데모로 보인다. **운영되는 시스템으로 보이려면 아카이브가
과거부터 오늘까지 끊기지 않아야 하고, 놓친 사건도 함께 보여야 한다.**

두 개의 표를 만든다.

    passes.json   충남 전체를 덮은 Sentinel-1 통과 전량. 각 통과의 선행강우.
    storms.json   호우 사건 전량. 각 사건이 관측됐는지, 등급은 무엇인지,
                  **관측하지 못했다면 왜인지.**

두 번째가 핵심이다. "사건 N건 중 M건만 위성으로 확인 가능했다"는 문장은
이 시스템이 왜 필요한지를 한 줄로 설명한다.

실행
    python notebooks/build_archive.py --start-year 2017
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.features.rainfall_era5 import fetch_daily_precip  # noqa: E402
from src.rs import catalog, observability as obs  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
REF_DIR = REPO_ROOT / "data" / "reference"
WEB_DATA = REPO_ROOT / "web" / "data"

MIN_COVERAGE = 80.0
COVERING_ORBITS = (127, 134)

# 호우 사건 정의 — 도 평균 기준
STORM_DAY_MM = 30.0  # 일강수량이 이 값을 넘으면 사건일
STORM_3D_MM = 50.0  # 또는 3일 누적이 이 값을 넘으면 사건일
STORM_GAP_DAYS = 3  # 사건일 사이 간격이 이보다 크면 별개 사건


def build_passes(start_year: int, end: str) -> pd.DataFrame:
    hull, aoi_m = catalog.load_aoi(REPO_ROOT)
    frames = []
    for year in range(start_year, int(end[:4]) + 1):
        y0 = f"{year}-01-01"
        y1 = end if year == int(end[:4]) else f"{year}-12-31"
        scenes = catalog.query_products(hull, y0, y1)
        df = catalog.coverage_by_pass(scenes, aoi_m)
        frames.append(df)
        print(f"  {year}: pass {len(df)}")
    out = pd.concat(frames, ignore_index=True)
    out = out[(out["coverage_pct"] >= MIN_COVERAGE) & (out["rel_orbit"].isin(COVERING_ORBITS))]
    return out.sort_values("date").reset_index(drop=True)


def build_rain(start_year: int, end: str) -> pd.Series:
    frames = []
    for year in range(start_year, int(end[:4]) + 1):
        y1 = end if year == int(end[:4]) else f"{year}-12-31"
        frames.append(fetch_daily_precip(REPO_ROOT, f"{year}-01-01", y1))
    rain = pd.concat(frames)
    return rain.mean(axis=1)


def find_storms(daily: pd.Series) -> pd.DataFrame:
    """일강수 시계열에서 호우 사건(연속된 사건일 묶음)을 뽑는다."""
    roll3 = daily.rolling(3).sum()
    flag = (daily >= STORM_DAY_MM) | (roll3 >= STORM_3D_MM)
    days = daily.index[flag]
    if len(days) == 0:
        return pd.DataFrame()

    groups, current = [], [days[0]]
    for prev, cur in zip(days, days[1:]):
        if (cur - prev).days <= STORM_GAP_DAYS:
            current.append(cur)
        else:
            groups.append(current)
            current = [cur]
    groups.append(current)

    rows = []
    for g in groups:
        window = daily.loc[g[0] : g[-1]]
        peak_day = window.idxmax()
        rows.append({
            "storm_id": f"S{peak_day:%Y%m%d}",
            "start": g[0].strftime("%Y-%m-%d"),
            "end": g[-1].strftime("%Y-%m-%d"),
            "peak_date": peak_day.strftime("%Y-%m-%d"),
            "peak_mm": round(float(window.max()), 1),
            "total_mm": round(float(window.sum()), 1),
            "days": len(g),
        })
    return pd.DataFrame(rows)


def match_observations(storms: pd.DataFrame, passes: pd.DataFrame, reliability: dict) -> pd.DataFrame:
    """사건마다 peak 이후 가장 이른 통과를 붙이고 등급을 매긴다."""
    passes = passes.copy()
    passes["when"] = pd.to_datetime(passes["date"].astype(str) + " " + passes["time_kst"])

    rows = []
    for s in storms.itertuples():
        # peak 일의 강우 중심 시각을 15시로 가정한다 (일 단위 자료의 한계).
        peak = pd.Timestamp(s.peak_date) + pd.Timedelta(hours=15)
        later = passes[(passes["when"] >= peak) & (passes["when"] <= peak + pd.Timedelta(days=10))]
        rec = {**s._asdict()}
        rec.pop("Index", None)
        if later.empty:
            # 아직 기회가 남은 사건과 기회 없이 지나간 사건을 구분한다.
            # 아카이브 마지막 통과보다 최근인 사건은 '관측 대기'다.
            pending = peak > passes["when"].max() - pd.Timedelta(days=10)
            # 다음 기회는 '지금' 이후여야 한다. peak 기준으로 계산하면
            # 이미 지나갔지만 카탈로그에 없는(=미촬영) 통과가 다음 기회로 표시된다.
            ref = max(peak.to_pydatetime(), pd.Timestamp.now().to_pydatetime())
            # history 를 명시적으로 넘긴다. 기본값을 쓰면 아직 저장되지 않은 정본 CSV 를
            # 읽으러 가서 이전 실행의 값으로 계산된다.
            nxt = obs.upcoming(ref, 21, history=passes)
            nxt = nxt[0] if nxt else None
            rec.update({"observed": False, "grade": "C", "lag_hours": None, "rel_orbit": None,
                        "obs_kst": None, "acquisition_reliability": None,
                        "status": "pending" if pending else "missed",
                        "next_pass_kst": nxt.when.strftime("%Y-%m-%d %H:%M") if nxt else None,
                        "next_pass_orbit": nxt.rel_orbit if nxt else None,
                        "next_pass_lag_hours": round((nxt.when - peak).total_seconds() / 3600, 1) if nxt else None,
                        "reason": ("아직 관측 전 — 다음 통과 대기" if pending
                                   else "peak 이후 10일 안에 충남 전체커버 관측 없음 — 현장조사 외 확인수단 없음")})
        else:
            best = later.iloc[0]
            lag = (best["when"] - peak).total_seconds() / 3600
            graded = obs.grade_observation(lag, int(best["rel_orbit"]))
            rec.update({"observed": True, "grade": graded["grade"], "lag_hours": round(lag, 1),
                        "rel_orbit": int(best["rel_orbit"]),
                        "obs_kst": best["when"].strftime("%Y-%m-%d %H:%M"),
                        "acquisition_reliability": round(reliability.get(int(best["rel_orbit"]), 0), 2),
                        "status": {"A": "observed_good"}.get(graded["grade"], "observed_late"),
                        "next_pass_kst": None, "next_pass_orbit": None, "next_pass_lag_hours": None,
                        "reason": graded["reason"]})
        rows.append(rec)
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="호우·관측 전체 아카이브")
    parser.add_argument("--start-year", type=int, default=2017)
    parser.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    args = parser.parse_args()

    print(f"[1/4] 통과 이력 조회 {args.start_year} ~ {args.end}")
    passes = build_passes(args.start_year, args.end)
    print(f"  전체커버 통과 {len(passes)}건")

    print("[2/4] 일강수량 조회")
    daily = build_rain(args.start_year, args.end)
    for n in (1, 3, 7):
        passes[f"rain{n}d"] = [
            round(float(daily.loc[pd.Timestamp(d) - pd.Timedelta(days=n - 1) : pd.Timestamp(d)].sum()), 1)
            for d in passes["date"]
        ]

    print("[3/4] 호우 사건 추출")
    storms = find_storms(daily)
    print(f"  사건 {len(storms)}건")

    reliability = obs.acquisition_reliability(
        passes.assign(when=pd.to_datetime(passes["date"].astype(str) + " " + passes["time_kst"]))
    )
    matched = match_observations(storms, passes, reliability)

    print("[4/4] 저장")
    REF_DIR.mkdir(parents=True, exist_ok=True)
    WEB_DATA.mkdir(parents=True, exist_ok=True)
    passes.to_csv(REF_DIR / "s1_passes_rainfall.csv", index=False, encoding="utf-8-sig")
    matched.to_csv(REF_DIR / "storm_archive.csv", index=False, encoding="utf-8-sig")

    (WEB_DATA / "passes.json").write_text(
        passes.tail(400).to_json(orient="records", force_ascii=False), encoding="utf-8")
    (WEB_DATA / "storms.json").write_text(
        matched.to_json(orient="records", force_ascii=False), encoding="utf-8")

    # 화면 상단에 띄울 요약 — 이 시스템이 왜 필요한지를 한 줄로 설명하는 숫자들
    stats = {
        "period": f"{passes['date'].min()} ~ {passes['date'].max()}",
        "n_passes": int(len(passes)),
        "n_storms": int(len(matched)),
        "n_grade_a": int((matched["grade"] == "A").sum()),
        "pct_grade_a": round((matched["grade"] == "A").mean() * 100, 1),
        "n_missed": int((matched["status"] == "missed").sum()),
        "n_pending": int((matched["status"] == "pending").sum()),
        "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
    }
    (WEB_DATA / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")

    counts = matched["grade"].value_counts()
    n_obs = int(matched["observed"].sum())
    print(f"\n사건 {len(matched)}건 | 관측됨 {n_obs} ({n_obs/len(matched)*100:.0f}%) | 미관측 {len(matched)-n_obs}")
    print("등급 분포:", {k: int(v) for k, v in counts.items()})
    print("상태 분포:", {k: int(v) for k, v in matched["status"].value_counts().items()})
    print(f"제대로 확인 가능(A) 비율: {stats['pct_grade_a']}%")
    print("\n[최근 사건 12건]")
    cols = ["peak_date", "peak_mm", "total_mm", "observed", "obs_kst", "lag_hours", "grade"]
    print(matched.tail(12)[cols].to_string(index=False))
    print(f"\n통과 이력 {passes['date'].min()} ~ {passes['date'].max()}")


if __name__ == "__main__":
    main()
