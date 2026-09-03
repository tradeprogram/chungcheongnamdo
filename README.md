# 물길잡이

**농경지 침수 및 호우 위성관측 체계**

2026 충청남도 데이터 분석 아이디어 공모전 출품작 · 마감 2026-09-23 18:00

---

## 무엇을 하는가

호우가 지나가면 행정은 "농경지가 얼마나 잠겼는가"를 20일 안에 보고해야 한다.
위성이 대안으로 거론되지만 **위성은 언제나 볼 수 있는 것이 아니다.**
충남 전역을 덮는 Sentinel-1 궤도는 2개뿐이고 12일에 한 번 지나며, 지나가도 촬영되지 않는다.

그래서 이 시스템은 판독보다 **판독 가능 여부의 판정**을 앞에 세운다.

```
호우 사건 추출  (일강수 30mm 또는 3일 누적 50mm 이상)
   ↓
관측 가능성 판정  A: peak+48h 이내 / B: 48~120h / C: 120h 초과·미관측
   ↓
등급 A·B 사건만 침수 판독  (Sentinel-1 이중반사·개방수면 z-score)
   ↓
팜맵 필지 단위 집계  (필지 1,434,057개, 판독률 99.1%)
   ↓
등급 C 는 지도를 만들지 않고 "현장조사로 전환하라"고 알린다
```

2017년 이후 충남 호우 **77건 중 제때 확인할 수 있었던 사건은 17건(22%)** 이다.
나머지 78%에 대해 위성 결과를 기다리는 것은 대응 지연이 된다.

## 실측한 것

같은 호우(2025-07-17 peak)를 두 번 관측했다. 부여읍 농경지 필지 10,270개 기준:

| 관측 | peak 기준 | 등급 | 침수 후보 필지 |
|---|---|---|---|
| 07-19 06:31 | +40시간 | A | **2,426개 (23.6%)** |
| 07-24 18:31 | +172시간 | C | 121개 (1.2%) |

같은 호우, 같은 농경지인데 **관측 시각 5일 차이로 20배**가 갈린다.

## 하지 않는 것

**호우 전 필지별 침수를 예측하지 않는다.** 모델을 만들었고 검증에서 떨어졌다
(사건 홀드아웃 ROC-AUC 0.50~0.55, 무작위 수준). 근거 없는 예측을 화면에 올리지
않는 것이 이 시스템의 설계 원칙이다 — 폐기 경위는
[docs/50_scope_revision.md](docs/50_scope_revision.md).

같은 이유로 **판독값이 없는 필지에 근거가 있는 척하지 않는다.**
면적 집계 89.4% / 대표점 표본 9.7% / 판독 불가 0.9% 를 화면에서 구분해 표시한다.

## 저장소 구조

```
docs/         전략·데이터·검증 문서, 제출물 (90_submission/)
src/rs/       GEE 헬퍼, Sentinel-1 침수 판독, 카탈로그, 관측 가능성 판정
src/features/ 팜맵 필지, zonal 집계, 지형·강우 covariate, 취약도
src/models/   폐기한 사전예측 모델 (기록으로 남김)
src/agent/    질의 응답 에이전트 — 화면 자료로 근거를 만들고 그 위에서만 답한다
notebooks/    아카이브·라벨·타일·화면 자료 생성, 실험, 검산
web/          MapLibre 뷰어 — 지도는 사전 생성 파일만 읽는다. 채팅만 서버를 쓴다
scripts/      야간 재생성 체인, 화면+에이전트 서버
```

`src/decision` 과 `src/api` 는 폐기한 원안의 잔재다. 현재 파이프라인은 쓰지 않는다.

## 재현

```bash
python notebooks/build_archive.py            # 관측 427회 · 호우 77건 아카이브
python notebooks/build_event_labels_v2.py    # 필지별 침수 판독 (사건 6건)
python notebooks/build_susceptibility_v2.py  # 다년 침수 빈도 (GEE 분할 수출)
python notebooks/build_web_data.py           # 화면용 사건·통계·오버레이
python notebooks/build_parcel_tiles.py       # 읍면동별 필지 GeoJSON
python notebooks/make_figure.py              # 기획서 [그림 1]
python notebooks/verify_proposal_numbers.py  # 기획서 수치 검산 (36건)
python notebooks/check_page_count.py         # 3페이지 제한 실측
```

GEE 프로젝트 ID는 `EE_PROJECT` 환경변수로 지정한다.
팜맵 API 키는 `.env` 에 두며 `.env.example` 을 참고한다.

화면과 에이전트를 한 프로세스로 띄운다.

```bash
python scripts/serve_agent.py
```

`GEMINI_API_KEY` 가 있으면 gemini-3.6-flash 가 답을 쓰고, 없으면 같은 근거로
`local_answer()` 가 답한다. **키가 없어도 채팅은 동작한다.** 지도만 볼 때는
정적 서버로도 충분하다 (`python -m http.server 5173 --directory web`).

## 에이전트

질문에서 도구를 고르고 `web/data` 의 사전 생성 파일로 근거를 먼저 만든 뒤,
그 근거만 LLM 에 넘긴다. LLM 은 새 수치를 만들 수 없고 있는 수치를 읽어 설명한다.
답변에는 근거 카드와 도구 실행 기록이 함께 붙고, "07-19 관측 보기" 같은 단추로
지도를 직접 움직인다.

화면과 같은 규칙을 지킨다 — 등급 C 사건은 판독 지도를 말하지 않고, 관측이 없는
필지는 0%가 아니라 관측 부족이며, 예측은 하지 않는다.

## 데이터

전량 공개데이터다. Sentinel-1 SAR (Copernicus / Google Earth Engine),
팜맵 농경지 전자지도 (농림축산식품부 공공데이터포털), 행정동 경계 (통계청 SGIS),
Copernicus DEM · MERIT Hydro, ERA5 강우 (Open-Meteo).
상세는 [docs/20_data_sources.md](docs/20_data_sources.md),
수치 출처 검증은 [docs/83_source_verification.md](docs/83_source_verification.md).

## 문서

| 문서 | 내용 |
|---|---|
| [70_official_criteria.md](docs/70_official_criteria.md) | 공모전 심사기준·양식 제약·쪽수 실측 |
| [80_proposal_draft.md](docs/80_proposal_draft.md) | 기획서 본문 |
| [50_scope_revision.md](docs/50_scope_revision.md) | 예측 모델 폐기와 범위 재정의 |
| [40_experiments.md](docs/40_experiments.md) | 실험 기록 |
| [84_coverage_fix.md](docs/84_coverage_fix.md) | 필지 판독 커버리지 38.7% → 99.1% |
| [60_demo_script.md](docs/60_demo_script.md) | 3분 데모 동선 |
