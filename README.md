# 물길잡이 (RE:FIELD)

**농경지 침수 및 호우 위성관측 체계**

> 시스템 범위는 `docs/50_scope_revision.md` 에서 재정의되었다.
> 아래 원안 중 예측 관련 서술은 검증 실패로 폐기된 상태다.
2026 충청남도 데이터 분석 아이디어 공모전 출품작

> RE:FIELD는 Sentinel-1/2 위성, 팜맵, 기상·지형 데이터를 이용해 충남 농경지의 침수를 호우 전에 예측하고,
> 호우 직후 실제 피해를 확인하며, 한정된 현장 인력과 복구자원을 어디에 먼저 투입할지 설명하는 농업재해 의사결정 AI입니다.

**예측 → 확인 → 행동**

---

## 왜 이 문제인가

2025년 7월 집중호우로 충남 15개 시·군에서 벼 14,944ha·논콩 1,381ha가 침수됐고, 도는 전문가 19명을
5개 시·군 생산단지에 파견해 생육 회복·재파종·병해충 대응을 현장에서 판단해야 했다.
충남농업기술원은 반복피해 지역 150곳을 집중관리 대상으로 선정했고, 2026년 배수개선 국비는 처음 1,000억 원을 넘었다.

행정이 실제로 답해야 하는 질문은 "어디가 위험한가"가 아니라 다음이다.

> **48시간 뒤 어디가 잠길 가능성이 높고, 실제로 어디가 잠겼으며,
> 한정된 농업재해 대응 인력과 장비를 어디부터 투입해야 하는가?**

## 파이프라인

```
호우 예보
   ↓
필지별 침수 가능성 사전예측        (LightGBM + calibration)
   ↓
Sentinel-1 SAR로 실제 침수 확인    (pre/post backscatter change)
   ↓
팜맵 단위 농작물 영향 추정          (parcel zonal aggregation)
   ↓
Sentinel-2/SAR 시계열 회복 추적     (same-season anomaly)
   ↓
현장점검·배수·방제·재파종 우선순위화 (constrained ranking)
   ↓
시나리오 비교 (인력/펌프/시각)      (coverage, travel-hour)
   ↓
AI Agent가 근거·불확실성·행동안 설명 (tool-grounded only)
```

## 아키텍처

```
공개데이터  Sentinel-1/2 · 팜맵 · KMA · DEM · WAMIS
    │
    ├─ Remote Sensing Engine ── 사건 전 baseline / SAR 실침수 / S2 회복 anomaly
    ├─ Weather · Terrain Engine ── 강우 · slope · HAND · TWI
    │
    └─→ Parcel Feature Store (GeoParquet)
            │
            ├─ Pre-event Flood Model   LightGBM + calibration
            ├─ Observed Impact Engine  필지별 침수율 · 생육영향
            ├─ Decision Engine         우선순위 · 시나리오 · 자원제약
            └─ Explainability          SHAP · Evidence · Uncertainty
                    │
                    ├─→ Tool-Calling AI Agent ─→ 자동 행정브리핑 / PDF
                    └─→ WebGIS Dashboard ──────→ 현장확인 Feedback Labels
```

## 설계 원칙

1. **LLM은 판단하지 않는다.** 분석엔진이 숫자를 만들고 Agent는 그 결과를 읽어 설명만 한다.
2. **모르는 것은 주장하지 않는다.** 공식 피해액 label이 없으므로 피해액을 예측하지 않는다.
   `Observed Flood Exposure` · `Crop Impact Index` · `Recovery Delay Probability`만 산출한다.
3. **위성이 후보를 좁히고 현장이 확정한다.** AI는 점검 우선순위와 근거를 제시하고 최종 결정은 담당자가 한다.
4. **모든 숫자에 출처를 붙인다.** OBSERVED / FORECAST / MODEL / ASSUMPTION을 화면에서 구분한다.
5. **가짜 인과추론을 하지 않는다.** "피해가 N% 줄어든다" 대신 "6시간 내 대응 가능한 고위험 필지 coverage"를 계산한다.

## 저장소 구조

```
docs/     전략·데이터·검증 문서, 제출물
src/rs/   Sentinel-1 침수탐지, Sentinel-2 회복탐지
src/features/   팜맵 필지 feature 생성
src/models/     baseline · LightGBM 사전예측
src/decision/   우선순위 엔진 · 시나리오 엔진
src/api/        FastAPI (status/data/warnings/provenance 응답계약)
src/agent/      Tool-calling Agent
web/            Next.js + MapLibre WebGIS
notebooks/      검증·성능표 재현
```

## 현재 상태

**D-22** (마감 2026-09-23 18:00). 준비 개요와 우선순위는 [docs/00_PREP_OVERVIEW.md](docs/00_PREP_OVERVIEW.md) 참조.

## 데이터 출처

Sentinel-1/2 (Copernicus), 팜맵 (농림축산식품부), 기상자료개방포털 (기상청), DEM, WAMIS (국가수자원관리종합정보시스템).
상세는 [docs/20_data_sources.md](docs/20_data_sources.md).
