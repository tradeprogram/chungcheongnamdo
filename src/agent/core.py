"""물길잡이 에이전트 코어 — 화면 자료를 읽어 근거를 묶고, 그 위에서만 설명한다.

설계는 `tradeprogram/policymaps` 의 에이전트를 본떴다. 핵심은 두 가지다.

**1. LLM 을 먼저 부르지 않는다.** 질문에서 도구를 고르고 `web/data` 의 사전 생성 파일로
근거(context)를 먼저 만든 뒤, 그 근거만 LLM 에 넘긴다. LLM 은 새로운 수치를 만들 수 없고
있는 수치를 읽어 설명한다.

**2. LLM 없이도 답한다.** 키가 없거나 호출이 실패하면 같은 근거로 `local_answer()` 가
결정적인 답을 만든다. 발표 중 외부 API 실패가 화면을 멈추게 두지 않는다는 이 시스템의
원칙이 채팅에도 그대로 적용된다.

이 프로젝트 고유의 규칙은 SYSTEM_PROMPT 와 `local_answer()` 양쪽에 같이 박아 둔다.
한쪽에만 두면 LLM 이 없을 때 규칙도 같이 사라진다.

실행
    python scripts/serve_agent.py
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "web" / "data"
DEFAULT_MODEL = "gemini-3.6-flash"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# 최근 3년 촬영률. 전체 기간(o127 87%, o134 63%)은 위성 구성이 바뀌기 전을 포함해
# 낙관적이므로, 앞으로의 판단에는 최근 값을 쓴다.
ORBIT_RELIABILITY_RECENT = {127: 0.67, 134: 0.37}
ORBIT_RELIABILITY_FULL = {127: 0.87, 134: 0.63}

# 필지 판독 근거 구성 (build_parcel_tiles.py 가 산출한 값)
READ_BASIS = {"area": 89.4, "point": 9.7, "none": 0.9}

SYSTEM_PROMPT = """너는 '물길잡이.agent'다. 충청남도 농경지 침수 위성관측 화면 안에서 담당자의 판단을 돕는다.

반드시 지킬 규칙:
- 한국어로 답한다.
- **등급 C 사건은 침수 판독 지도가 없다.** 그 사건의 필지 침수율을 말하지 말고, 현장조사로 전환하라고 안내한다.
- **관측이 없는 필지·읍면동은 침수 빈도가 0%가 아니라 '관측 부족'이다.** 공백을 0으로 말하지 않는다.
- 필지 값의 근거 등급을 구분한다. 면적 집계 89.4%, 대표점 표본 9.7%, 판독 불가 0.9%다. 대표점 표본은 화소 1개짜리 단일 표본이다.
- **호우 전 침수를 예측하지 않는다.** 예측 모델은 만들었다가 판별력이 무작위 수준(ROC-AUC 0.50~0.55)이어서 폐기했다. 예측을 요청받으면 그 사실을 말한다.
- 촬영 확률을 말할 때는 기준 기간을 밝힌다. 최근 3년은 orbit 127이 67%, orbit 134가 37%이고, 전체 기간은 87%와 63%다.
- 관측 지연이 결과를 가른다는 점을 근거로 설명한다. 2025년 7월 같은 호우에서 07-19 관측(지연 40시간, 등급 A)은 논 25.67%, 07-24 관측(지연 172시간, 등급 C)은 1.42%였다.
- 주어진 근거(context)에 없는 수치는 만들지 말고 '현재 화면 자료로는 확인 불가'라고 말한다.
- 답변은 담당자가 바로 행동할 수 있게 쓴다. 판정, 근거 수치, 한계, 다음에 볼 화면을 함께 제시한다.
"""


# --- 진입점 ---------------------------------------------------------------


def handle_chat(payload: dict) -> dict:
    load_env_files()
    agent = run_agent(payload)
    model = error = None
    # 키가 없는 것은 고장이 아니라 선택이다. 화면에 '오류'로 띄우면 발표 중
    # 없는 문제를 있는 것처럼 보이게 만든다.
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        answer, llm_state = local_answer(agent), "no_key"
    else:
        try:
            llm = call_gemini(payload, agent)
            answer = llm.get("answer") or local_answer(agent)
            model, llm_state = llm.get("model"), "ok"
        except Exception as exc:  # noqa: BLE001 — 실패해도 근거 기반 답은 낸다
            answer, llm_state = local_answer(agent), "error"
            error = {"type": type(exc).__name__, "message": str(exc)[:400]}

    out = {
        "answer": answer,
        "actions": agent["actions"],
        "evidence": agent["evidence"],
        "tool_trace": agent["tool_trace"],
        "suggested": agent["suggested"],
        "llm": llm_state,
    }
    if model:
        out["model"] = model
    if error:
        out["error"], out["detail"] = error["type"], error["message"]
    return out


def run_agent(payload: dict) -> dict:
    question = str(payload.get("message", ""))[:3000]
    client = payload.get("context") or {}
    plan = plan_tools(question)
    trace: list[dict] = []
    evidence: list[dict] = []

    stats = read_json(DATA / "stats.json") or {}
    ctx = {
        "question": question,
        "period": stats.get("period"),
        "generated_at": stats.get("generated_at"),
        "archive": {
            "passes": stats.get("n_passes"),
            "storms": stats.get("n_storms"),
            "grade_a": stats.get("n_grade_a"),
            "pct_grade_a": stats.get("pct_grade_a"),
            "missed": stats.get("n_missed"),
        },
        "read_basis_pct": READ_BASIS,
        "selected_storm": client.get("selected_storm"),
        "selected_event": client.get("selected_event"),
        "selected_parcel": client.get("selected_parcel"),
        "map_zoom": client.get("zoom"),
        "rules": [
            "등급 C 사건은 판독 지도 없음",
            "관측 없음은 0%가 아니라 관측 부족",
            "면적 집계와 대표점 표본은 근거 등급이 다름",
            "사전예측은 검증 실패로 폐기",
        ],
    }

    def record(tool: str, ok, detail: str = "") -> None:
        trace.append({"tool": tool, "status": "ok" if ok else "missing", "detail": detail})

    if "pending" in plan:
        ctx["pending"] = summarize_pending()
        record("관측 대기 사건", ctx["pending"])
        if ctx["pending"]:
            p = ctx["pending"]
            evidence.append({
                "title": f"{p['peak_date']} 호우 · 관측 대기",
                "kind": "pending",
                "summary": f"최대 일강수 {p['peak_mm']}mm · 다음 통과 {p['next_pass_kst']}"
                           f" · orbit {p['next_pass_orbit']} 촬영 확률 {p['reliability_recent_pct']}%",
            })

    if "events" in plan:
        ctx["events"] = summarize_events()
        record("사건별 판독 결과", ctx["events"], f"{len(ctx['events'] or [])}건")
        if ctx["events"]:
            evidence.append({
                "title": "같은 호우, 두 차례 관측",
                "kind": "event",
                "summary": "07-19(지연 40h, 등급 A) 논 25.67% 대 07-24(지연 172h, 등급 C) 논 1.42%",
            })

    if "storm" in plan:
        ctx["storm"] = summarize_storm(question, client)
        record("호우 사건 조회", ctx["storm"])
        if ctx["storm"]:
            s = ctx["storm"]
            evidence.append({
                "title": f"{s['peak_date']} 호우 · 등급 {s['grade']}",
                "kind": "storm",
                "summary": s["verdict"],
            })

    if "region" in plan:
        ctx["region"] = summarize_region(question, client)
        ctx["ranking"] = summarize_ranking()
        record("지역 침수 빈도", ctx["region"] or ctx["ranking"])
        if ctx["region"]:
            r = ctx["region"]
            evidence.append({
                "title": f"{r['sgg_nm']} {r['emd_nm']}",
                "kind": "region",
                "summary": f"다년 침수 빈도 {r['wet_freq_pct']} · 도내 {r['rank']}위 / {r['total']}"
                           f" · 필지 {r['parcels']:,}개 · 판독률 {r['read_pct']}%",
            })

    if "coverage" in plan:
        ctx["coverage"] = summarize_coverage()
        record("판독 근거 구성", ctx["coverage"])

    if "forecast" in plan:
        ctx["forecast_discarded"] = {
            "roc_auc": "0.50~0.55",
            "reason": "사건 내부 판별력이 무작위 수준이어서 폐기",
        }
        record("사전예측 모델", True, "폐기됨 — 근거 없음")

    actions = suggest_actions(plan, ctx)
    return {
        "context": ctx,
        "tool_trace": trace,
        "evidence": evidence,
        "actions": actions,
        "suggested": suggest_followups(plan, ctx),
    }


# --- 도구 선택 -------------------------------------------------------------


def place_names() -> tuple[set[str], set[str]]:
    """화면에 있는 읍면동·시군 이름. 키워드 목록으로는 '부여읍'을 못 잡는다."""
    feats = (read_json(DATA / "emd.geojson") or {}).get("features") or []
    emd = {f["properties"]["emd_nm"] for f in feats if f["properties"].get("emd_nm")}
    sgg = {f["properties"]["sgg_nm"] for f in feats if f["properties"].get("sgg_nm")}
    # '천안시동남구' 는 질문에 '천안' 으로 나온다
    sgg |= {re.sub(r"(시|군)(.*)$", "", n) for n in sgg if len(n) > 2}
    return emd, {n for n in sgg if len(n) >= 2}


def mentions_place(question: str) -> bool:
    emd, sgg = place_names()
    return any(n in question for n in emd) or any(n in question for n in sgg)


def plan_tools(question: str) -> list[str]:
    q = question.lower()
    plan: list[str] = []
    if any(k in q for k in ("지금", "이번", "대기", "다음 통과", "언제 볼", "볼 수 있", "확인 가능")):
        plan.append("pending")
    if any(k in q for k in ("사건", "호우", "등급", "관측", "지연", "비교", "07-19", "07-24")):
        plan += ["storm", "events"]
    if (any(k in q for k in ("지역", "읍면동", "시군", "어디", "반복", "빈도", "순위", "상습"))
            or mentions_place(question)):
        plan.append("region")
    if any(k in q for k in ("판독", "커버리지", "표본", "근거", "정확", "몇 %", "얼마나")):
        plan.append("coverage")
    if any(k in q for k in ("예측", "예보", "미리", "사전", "전망")):
        plan.append("forecast")
    if not plan:
        plan = ["pending", "events", "coverage"]
    return list(dict.fromkeys(plan))


# --- 도구 ------------------------------------------------------------------


def summarize_pending():
    storms = read_json(DATA / "storms.json") or []
    pending = [s for s in storms if s.get("status") == "pending"]
    if not pending:
        return None
    s = pending[0]
    orbit = int(s["next_pass_orbit"]) if s.get("next_pass_orbit") else None
    return {
        "peak_date": s.get("peak_date"),
        "peak_mm": s.get("peak_mm"),
        "total_mm": s.get("total_mm"),
        "next_pass_kst": s.get("next_pass_kst"),
        "next_pass_orbit": orbit,
        "next_pass_lag_days": round((s.get("next_pass_lag_hours") or 0) / 24, 1),
        "reliability_recent_pct": round(ORBIT_RELIABILITY_RECENT.get(orbit, 0) * 100),
        "storm_id": s.get("storm_id"),
    }


def summarize_events():
    events = read_json(DATA / "events.json") or []
    return [{
        "id": e["id"],
        "label": e.get("label"),
        "observed_kst": e.get("observed_kst"),
        "orbit": e.get("rel_orbit"),
        "paddy_pct": e.get("paddy_pct"),
        "upland_pct": e.get("upland_pct"),
    } for e in events]


def summarize_storm(question: str, client: dict):
    storms = read_json(DATA / "storms.json") or []
    if not storms:
        return None
    target = None
    m = re.search(r"(20\d\d)[-.\s/년]*(\d{1,2})[-.\s/월]*(\d{1,2})?", question)
    if m:
        year, month, day = m.group(1), int(m.group(2)), m.group(3)
        # 일까지 말했으면 그 날짜를 쓴다. 월만 보고 그 달의 첫 사건을 집으면
        # 2023-07-23 을 물었는데 07-14 를 설명하게 된다.
        if day:
            exact = f"{year}-{month:02d}-{int(day):02d}"
            target = next((s for s in storms if s.get("peak_date") == exact), None)
            if target is None:
                # 관측일로 물어보는 경우도 있다 (07-19 는 07-17 호우의 관측이다)
                target = next((s for s in storms if str(s.get("obs_kst", ""))[:10] == exact), None)
            if target is None:
                # 사건 기간 안에 드는 날짜인지 본다
                target = next((s for s in storms
                               if s.get("start") and s["start"] <= exact <= s.get("end", "")), None)
        if target is None:
            prefix = f"{year}-{month:02d}"
            cands = [s for s in storms if str(s.get("peak_date", "")).startswith(prefix)]
            # 같은 달에 여럿이면 가장 큰 사건을 고른다
            target = max(cands, key=lambda s: s.get("total_mm") or 0) if cands else None
    if target is None and client.get("selected_storm"):
        target = next((s for s in storms if s["storm_id"] == client["selected_storm"]), None)
    if target is None:
        return None

    grade = target.get("grade")
    if not target.get("observed"):
        verdict = "충남 전역을 덮는 관측이 없었습니다. 위성으로는 확인할 수 없는 사건입니다."
    elif grade == "A":
        verdict = f"최대 강수 후 {target['lag_hours']}시간 관측으로, 침수 범위 판정에 적합합니다."
    elif grade == "B":
        verdict = (f"최대 강수 후 {target['lag_hours']}시간 관측으로, 잔존 침수만 보입니다. "
                   "최대 침수 범위는 아닙니다.")
    else:
        verdict = (f"최대 강수 후 {target['lag_hours']}시간 관측으로 120시간을 넘겼습니다. "
                   "침수 범위를 대표하지 않으므로 판독 지도를 만들지 않습니다.")
    return {
        "storm_id": target.get("storm_id"),
        "peak_date": target.get("peak_date"),
        "peak_mm": target.get("peak_mm"),
        "total_mm": target.get("total_mm"),
        "grade": grade,
        "observed": target.get("observed"),
        "lag_hours": target.get("lag_hours"),
        "orbit": int(target["rel_orbit"]) if target.get("rel_orbit") else None,
        "obs_kst": target.get("obs_kst"),
        "has_reading": bool(overlay_for(target)),
        "verdict": verdict,
    }


def overlay_for(storm: dict):
    if not storm.get("obs_kst"):
        return None
    day = str(storm["obs_kst"])[:10]
    for e in read_json(DATA / "events.json") or []:
        if str(e.get("observed_kst", ""))[:10] == day:
            return e["id"]
    return None


def summarize_region(question: str, client: dict):
    feats = (read_json(DATA / "emd.geojson") or {}).get("features") or []
    rows = [f["properties"] for f in feats]
    if not rows:
        return None
    total = len(rows)
    hit = None
    for r in rows:
        if r.get("emd_nm") and r["emd_nm"] in question:
            hit = r
            break
    if hit is None:
        for r in rows:
            sgg = (r.get("sgg_nm") or "").replace("시", "").replace("군", "")
            if sgg and sgg in question:
                same = [x for x in rows if x.get("sgg_nm") == r["sgg_nm"] and x.get("wet_freq") is not None]
                hit = max(same, key=lambda x: x["wet_freq"]) if same else r
                break
    if hit is None and client.get("selected_emd"):
        hit = next((r for r in rows if r.get("emd_cd") == client["selected_emd"]), None)
    if hit is None:
        return None
    return {
        "emd_cd": hit.get("emd_cd"),
        "emd_nm": hit.get("emd_nm"),
        "sgg_nm": hit.get("sgg_nm"),
        "parcels": hit.get("parcels"),
        "area_km2": hit.get("area_km2"),
        "wet_freq_pct": "관측 부족" if hit.get("wet_freq") is None else f"{hit['wet_freq'] * 100:.1f}%",
        "rank": hit.get("rank"),
        "total": total,
        "read_pct": hit.get("read_pct"),
    }


def summarize_ranking(limit: int = 5):
    feats = (read_json(DATA / "emd.geojson") or {}).get("features") or []
    rows = [f["properties"] for f in feats if f["properties"].get("wet_freq") is not None]
    if not rows:
        return None
    rows.sort(key=lambda p: -p["wet_freq"])
    top = [{"rank": r["rank"], "name": f"{r['sgg_nm']} {r['emd_nm']}",
            "pct": round(r["wet_freq"] * 100, 1)} for r in rows[:limit]]
    lo, hi = rows[-1]["wet_freq"], rows[0]["wet_freq"]
    return {
        "top": top,
        "n_ranked": len(rows),
        "spread": f"{hi * 100:.1f}% ~ {lo * 100:.1f}% (약 {hi / lo:.1f}배)",
        "caveat": "이 정도 폭으로 투자 우선순위를 그대로 정하기에는 이릅니다. 관측이 쌓일수록 근거가 강해지는 종류의 지표입니다.",
    }


def summarize_coverage():
    meta = read_json(DATA / "sus_meta.json") or {}
    return {
        "면적 집계": f"{READ_BASIS['area']}%",
        "대표점 표본": f"{READ_BASIS['point']}% (화소 1개 단일 표본)",
        "판독 불가": f"{READ_BASIS['none']}%",
        "다년 침수 빈도가 산출된 필지": f"{meta.get('parcels_with_freq_pct')}%",
        "젖은 관측 수": meta.get("wet_obs_max"),
    }


# --- 화면 조작 제안 --------------------------------------------------------


def suggest_actions(plan: list[str], ctx: dict) -> list[dict]:
    out: list[dict] = []
    pending = ctx.get("pending")
    if "pending" in plan and pending:
        out.append({"type": "storm", "id": pending["storm_id"],
                    "label": f"{pending['peak_date']} 호우 보기", "primary": True})
    if "events" in plan:
        out.append({"type": "event", "id": "o134_2025-07-19", "label": "07-19 관측 (등급 A)"})
        out.append({"type": "event", "id": "o127_2025-07-24", "label": "07-24 관측 (등급 C)"})
        out.append({"type": "basemap", "kind": "plain", "label": "단색 배경으로 비교"})
    storm = ctx.get("storm")
    if storm and storm.get("storm_id"):
        out.append({"type": "storm", "id": storm["storm_id"],
                    "label": f"{storm['peak_date']} 호우 보기", "primary": True})
    region = ctx.get("region")
    if region and region.get("emd_cd"):
        out.append({"type": "region", "emd_cd": region["emd_cd"],
                    "label": f"{region['sgg_nm']} {region['emd_nm']} 이동", "primary": True})
    if "region" in plan and not region:
        out.append({"type": "tab", "id": "emd", "label": "지역 순위 열기"})
    # 같은 화면을 두 번 제안하지 않는다
    seen, uniq = set(), []
    for a in out:
        key = (a["type"], a.get("id"), a.get("emd_cd"), a.get("kind"))
        if key not in seen:
            seen.add(key)
            uniq.append(a)
    return uniq[:5]


def suggest_followups(plan: list[str], ctx: dict) -> list[str]:
    if "forecast" in plan:
        return ["예측 대신 무엇을 제공하는지 설명해줘",
                "등급 판정은 어떻게 하는지 알려줘",
                "관측이 늦으면 결과가 얼마나 달라져?"]
    if "pending" in plan and ctx.get("pending"):
        return ["지금 기다릴지 현장에 나갈지 판단해줘",
                "이 궤도 촬영 확률이 왜 낮아?",
                "최근 사건 중 등급 A는 몇 건이야?"]
    if "region" in plan:
        return ["상위 읍면동을 투자 우선순위로 써도 돼?",
                "이 지역 판독률이 100%가 아니면 왜야?",
                "같은 지역의 사건별 침수율을 비교해줘"]
    return ["이번 호우는 위성으로 확인할 수 있어?",
            "판독 불가 필지는 왜 생겨?",
            "관측 시각이 결과를 얼마나 가르는지 보여줘"]


# --- LLM -------------------------------------------------------------------


def call_gemini(payload: dict, agent: dict) -> dict:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY 환경변수가 없습니다.")
    model = os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
    prompt = {
        "instruction": SYSTEM_PROMPT,
        "user_question": str(payload.get("message", ""))[:3000],
        "agent_context": agent["context"],
        "tool_trace": agent["tool_trace"],
        "recent_history": (payload.get("history") or [])[-8:],
        "output_format": "한국어 순수 텍스트. 3~5개 문단, 400~700자. 마크다운 기호 금지. "
                         "근거로 쓴 수치는 문장 안에 그대로 적는다.",
    }
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps(prompt, ensure_ascii=False)}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2048},
    }
    req = Request(
        f"{GEMINI_BASE}/models/{model}:generateContent",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"content-type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    with urlopen(req, timeout=45) as res:
        data = json.loads(res.read().decode("utf-8"))
    return {"answer": extract_text(data), "model": model}


def extract_text(data: dict) -> str:
    for cand in data.get("candidates") or []:
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
        if text:
            return text
    return ""


# --- LLM 없이 답하기 -------------------------------------------------------


def local_answer(agent: dict) -> str:
    ctx = agent["context"]
    q = ctx.get("question") or ""
    arch = ctx.get("archive") or {}
    para: list[str] = []

    if ctx.get("forecast_discarded"):
        return ("호우 전에 어느 필지가 잠길지는 예측하지 않습니다. 모델을 만들었지만 사건 내부 "
                "판별력이 ROC-AUC 0.50~0.55로 무작위 수준이어서 폐기했습니다.\n\n"
                "대신 제공하는 것은 '이번 호우를 위성으로 확인할 수 있는가'의 판정입니다. "
                f"2017년 이후 충남 호우 {arch.get('storms')}건 중 제때 확인할 수 있었던 사건은 "
                f"{arch.get('grade_a')}건({arch.get('pct_grade_a')}%)뿐이었습니다.\n\n"
                "근거 없는 예측을 화면에 올리지 않는 것이 이 시스템의 설계 원칙입니다.")

    p = ctx.get("pending")
    if p:
        para.append(
            f"{p['peak_date']} 호우는 최대 일강수 {p['peak_mm']}mm, 사건 누적 {p['total_mm']}mm였고 "
            f"아직 충남 전역을 덮는 관측이 없습니다. 다음 통과는 {p['next_pass_kst']}로, "
            f"최대 강수 시점에서 약 {p['next_pass_lag_days']}일 뒤입니다.")
        para.append(
            f"다만 통과가 곧 촬영은 아닙니다. orbit {p['next_pass_orbit']}의 최근 3년 촬영 확률은 "
            f"{p['reliability_recent_pct']}%입니다. "
            + ("절반이 안 되므로 통과를 기다리기보다 현장조사를 함께 준비하는 편이 안전합니다."
               if p["reliability_recent_pct"] < 50 else "촬영될 가능성이 더 큽니다."))

    s = ctx.get("storm")
    if s:
        head = f"{s['peak_date']} 호우는 등급 {s['grade']}입니다. {s['verdict']}"
        para.append(head)
        if s["grade"] == "C" or not s["observed"]:
            para.append("화면에서도 농경지 구분만 표시됩니다. 근거가 없는 지도를 내지 않는 것이 "
                        "원칙이므로, 이 사건은 현장조사로 전환해야 합니다.")

    ev = ctx.get("events")
    if ev and not s:
        para.append(
            "관측 시각이 결과를 가릅니다. 2025년 7월 같은 호우에서 07-19 관측(지연 40시간, 등급 A)은 "
            "논 침수 후보 25.67%, 07-24 관측(지연 172시간, 등급 C)은 1.42%였습니다. "
            "같은 농경지인데 5일 차이로 18배입니다.")

    r = ctx.get("region")
    if r:
        name = f"{r['sgg_nm']} {r['emd_nm']}"
        para.append(
            f"{name}{josa(r['emd_nm'])} 농경지 필지 {r['parcels']:,}개, 다년 침수 빈도 "
            f"{r['wet_freq_pct']}로 도내 {r['rank']}위 / {r['total']}개입니다. "
            f"이 지역 필지의 {r['read_pct']}%에 판독값이 있습니다."
            + ("" if r["wet_freq_pct"] != "관측 부족"
               else " 침수 빈도는 관측이 부족해 산출하지 않았습니다. 0%가 아니라 모르는 값입니다."))
    rank = ctx.get("ranking")
    if rank and not r:
        top = " · ".join(f"{t['name']} {t['pct']}%" for t in rank["top"][:3])
        para.append(f"다년 침수 빈도 상위는 {top} 순입니다. 전체 범위는 {rank['spread']}입니다. "
                    f"{rank['caveat']}")

    cov = ctx.get("coverage")
    if cov:
        para.append(
            f"필지 값의 근거는 등급이 다릅니다. 면적 집계 {cov['면적 집계']}, "
            f"대표점 표본 {cov['대표점 표본']}, 판독 불가 {cov['판독 불가']}입니다. "
            "대표점 표본은 20m 격자에 화소가 배정되지 않은 작은 필지를 한 점에서 읽은 값이라 "
            "면적 비율과 같은 무게로 쓰면 안 됩니다.")

    if not para:
        para.append(
            f"충남에서 2017년 이후 호우 {arch.get('storms')}건, 위성 통과 {arch.get('passes')}회를 "
            f"모아 두었습니다. 이 중 제때 확인할 수 있었던 사건은 {arch.get('grade_a')}건"
            f"({arch.get('pct_grade_a')}%)입니다. 사건을 고르시면 등급과 판독 결과를 보여 드립니다.")

    para.append("(LLM 연결 없이 화면 자료만으로 정리한 답변입니다.)")
    return "\n\n".join(para)


# --- 유틸 ------------------------------------------------------------------


def load_env_files() -> None:
    for path in (ROOT / ".env",):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def josa(word: str, pair: str = "은는") -> str:
    """받침에 맞는 조사를 고른다. '부여읍는' 같은 문장이 나오면 자동 생성 티가 난다."""
    if not word:
        return pair[1]
    code = ord(word[-1])
    if not (0xAC00 <= code <= 0xD7A3):
        return pair[1]
    has_final = (code - 0xAC00) % 28 != 0
    return pair[0] if has_final else pair[1]


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 없는 자료는 도구가 missing 으로 기록한다
        return None
