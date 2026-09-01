# RE:FIELD — 2026 충청남도 데이터 분석 아이디어 공모전 준비 개요

> **한 문장 Pitch**
> RE:FIELD는 Sentinel-1/2 위성, 팜맵, 기상·지형 데이터를 이용해 충남 농경지의 침수를 호우 전에 예측하고,
> 호우 직후 실제 피해를 확인하며, 한정된 현장 인력과 복구자원을 어디에 먼저 투입할지 설명하는 농업재해 의사결정 AI입니다.

- 저장소: https://github.com/tradeprogram/chungcheongnamdo
- 접수 마감: **2026-09-23 18:00** (오늘 2026-09-01 기준 **D-22**)
- 참가 형태: 개인 / 전용 GPU 없음 → GEE·공개 API·CPU(LightGBM) 중심

---

## 1. 출품 방향 결정 (문서 결론 요약)

| 항목 | 결정 |
|---|---|
| 출품작 | **RE:FIELD** — 충남 농경지 침수 선제예측 · 실피해 판별 · 복구 우선순위 AI |
| 탈락시킨 대안 | 산불 Agent (2025 최우수상 "기후요인 기반 화재 위험도"와 인지적 거리가 너무 가까움) |
| 핵심 서사 | 예측(호우 전) → 확인(SAR) → 행동(현장 Top-N·시나리오) |
| 심사 프레임 | "어디가 위험한가"가 아니라 **"48시간 뒤 어디가 잠기고, 실제 어디가 잠겼고, 어디부터 갈 것인가"** |
| 차별화 주장 | 위성 침수탐지의 신규성이 아니라 **충남 팜맵 필지 + 행정 대응 workflow와의 연결** |

근거가 되는 충남 현안: 2025년 7월 집중호우 시 벼 14,944ha·논콩 1,381ha 침수, 도 전문가 19명 5개 시군 파견,
농업기술원 반복피해 150개 집중관리지역 선정, 2026년 배수개선 국비 첫 1,000억 원 돌파(43개 지구·6,129억 원).

---

## 2. Winning MVP — 이 8개만 완성되면 제출

1. 충남 실제 호우 1건 (Golden Event: 2025-07)
2. Sentinel-1 before/after 실제 침수 탐지
3. 팜맵 필지별 침수율(flood fraction) 집계
4. LightGBM 사전 침수확률 예측 (+ baseline 비교)
5. 현장점검 Top-N priority queue
6. "왜 1순위인가" 근거설명 (SHAP reason code)
7. team 5/10/20 Scenario (coverage 변화)
8. Spatial / Temporal / Event holdout 검증표

**Drop 확정:** Transformer, GNN, Multi-Agent, 전국 hydrodynamic 시뮬레이션, 정밀 피해액 산정, 3D 시각화, 자동 행정명령.

---

## 3. 저장소 구조 (제안)

```
chungcheongnamdo/
├─ README.md                 # 한 문장 Pitch + architecture 다이어그램 (가장 먼저 작성)
├─ docs/
│  ├─ 00_PREP_OVERVIEW.md    # 본 문서
│  ├─ 10_golden_event.md     # 2025-07 충남 호우 사건 정의·공식 근거 1p
│  ├─ 20_data_sources.md     # 데이터 출처·라이선스·취득 방법
│  ├─ 30_validation.md       # holdout 설계 및 성능표
│  └─ 90_submission/         # 제안서·발표자료·심사기준 원문
├─ data/                     # (gitignore) raw/interim/processed
├─ src/
│  ├─ rs/sentinel1_flood.py       # Waterside Guard 이식
│  ├─ rs/sentinel2_recovery.py    # Waterside Guard 이식
│  ├─ features/parcel_features.py # 팜맵 zonal stats + DEM/HAND/TWI + KMA rain
│  ├─ models/flood_forecast.py    # baseline(rule/LR) + LightGBM + calibration
│  ├─ decision/priority_engine.py # Top-N ranking + reason codes
│  ├─ decision/scenario.py        # team/pump/deadline coverage
│  ├─ api/                        # FastAPI (AquaGuard 응답계약 이식)
│  └─ agent/tools.py              # Tool-calling Agent (9개 Tool)
├─ web/                      # Next.js + MapLibre/deck.gl (8화면, demo는 5화면)
└─ notebooks/                # 검증·성능표 재현용
```

**재사용 원칙:** Waterside Guard(S1/S2 anomaly·Top-N ranking·현장 feedback), AquaGuard(FastAPI 스키마·fallback_tier·provenance·What-if), PolicyMaps(Evidence 표기·MCP Tool), MOFOM(필지 단위 UX). 신규 개발이 아니라 **재조립**.

---

## 4. 데이터 확보 계획

| 데이터 | 용도 | 출처 | 상태 |
|---|---|---|---|
| Sentinel-1 GRD VV/VH | 실제 침수 evidence, 과거 event label | GEE / Copernicus | P0 |
| Sentinel-2 L2A | 회복 anomaly (NDVI/NDMI) | GEE | P1 |
| 팜맵 | 농경지 필지 경계·논/밭/과수/시설 속성 | 농림축산식품부 | P0 |
| KMA 기상 | 예보·실황 강우 1h/3h/6h/24h, 선행강우 | 기상자료개방포털 API | P0 |
| DEM | slope / HAND / TWI / 하천거리 | 국토지리정보원 등 | P0 |
| WAMIS | 배수시설·양수장 제원 | WAMIS | Could |
| 공식 피해 집계 | 사건 서술·검증 참고(예측 대상 아님) | 도·농업기술원 보도자료 | P0 |

**금지:** 피해액(원) 예측. 대신 `Observed Flood Exposure`, `Crop Impact Index`, `Recovery Delay Probability`만 산출.

---

## 5. 검증 설계 (연구 신뢰성 축)

- **Spatial holdout**: 특정 시·군 전체를 test로 분리 (인접 필지 맞히기 금지)
- **Temporal holdout**: 과거 사건 학습 → 최근 연도 validation → 2025 집중호우 final untouched test
- **Event holdout**: Event A/B/C 학습 → Event D 전량 평가. 동일 사건 픽셀 random split 금지(leakage)
- **지표**: SAR extent(IoU/F1/Recall), 필지 분류(PR-AUC/Precision/Recall), 확률(Brier/ECE/calibration curve), ranking(P@K, NDCG), 시나리오(coverage·travel-hour), Agent(unsupported-claim rate)

---

## 6. 22일 일정 (D-22 → D-0)

| 구간 | 날짜 | 산출물 |
|---|---|---|
| Winning Core | 09/01–09/09 | Golden Event 확정, 팜맵·S1·KMA·DEM 파이프라인, SAR 침수탐지·필지집계, 과거 label |
| Validation | 09/09–09/13 | Baseline → LightGBM, spatial/temporal/event 검증표 |
| Decision Demo | 09/13–09/19 | Priority Engine, Scenario Engine, WebGIS 통합, Agent Tools |
| Submission | 09/19–09/23 | 3분 Demo 동선 고정, 제안서·도표·검증표, 캐시·버그픽스, 제출 |

---

## 7. 3분 Demo 동선 (고정)

`문제 제시(0:20)` → `T-48 예측(0:45)` → `SAR before/after 확인(1:10)` → `팜맵 필지 변환(1:35)` → `현장팀 10명 Top-N 재정렬(2:05)` → `What-if 10→20팀 coverage(2:30)` → `Agent 근거 설명(2:50)` → `1-page 브리핑 PDF(3:00)`

핵심 화면은 **Satellite Evidence(전/후 slider + OBSERVED/FORECAST/MODEL/ASSUMPTION 범례)**. 이 화면 하나가 원격탐사 필연성을 증명한다.

---

## 8. 리스크와 방어책 (상위 5)

| 위험 | 방어책 |
|---|---|
| 2026 실제 심사배점 미확인 | **제출 전 공고 첨부(HWP/PDF) 수동 확보 → 제안서 목차 재가중** ← 미해결 P0 |
| 논(畓)의 SAR 오탐 | 논/밭 strata 분리 + same-season median/MAD baseline + VV/VH·HAND·강우 fusion |
| 관측시각 ≠ 침수 peak | "최대 침수"가 아닌 **"관측시점 침수"**로 표기 |
| 피해 ground truth 부족 | 피해액 예측 포기, exposure·recovery index만 주장 |
| Agent가 주인공이 됨 | Agent 없이도 workflow 완결. Agent는 Tool 결과 설명만 |

---

## 9. 지금 바로 할 일 (P0, 순서 고정)

- [ ] 2026 공모전 공고 첨부에서 **공식 심사항목·배점·제출서식** 확보 (문서에서 유일하게 미확인된 항목)
- [x] **Golden Event 확정** — 2025-07-16~19 호우 (peak 7/17, 도 평균 74.9mm), `docs/10_golden_event.md`
- [x] **Sentinel-1 가용성 확인** — 충남 전체커버 궤도는 127 ASC / 134 DESC 2개뿐.
      침수 관측 주영상 = orbit 134, 2025-07-19 06:32 KST (peak +1.6일, 99% 커버)
- [ ] KMA 인증키 발급 → ERA5 보조자료를 실관측 강우로 교체
- [ ] Copernicus 계정 발급 → 실제 영상 다운로드 (카탈로그 조회는 인증 불필요)
- [x] 저장소 초기화 + README에 Pitch·architecture 먼저 작성
- [x] **충남 AOI 확정** — SGIS 행정동 경계에서 시도코드 34 추출, 행정동 208개 / 시군 15개,
      `src/features/aoi.py`, 8,264.7 km²
- [ ] Waterside Guard의 S1/S2/GEE 코드 분리 이식 → `src/rs/*.py` 실행 확인
- [ ] 팜맵 충남 필지 확보 → AOI로 clip → GeoParquet 변환 → 지도에 polygon 표시
- [ ] KMA 사건강우 ingest → event별 1h/3h/24h feature
- [ ] DEM → slope/HAND/TWI → `field_event_features.parquet` 완성

**가장 먼저 만들 단 하나의 화면: "2025 충남 호우 Event Replay"** — 호우 전 AI 예측 + Sentinel-1 실제 침수 + 팜맵 필지 + 현장대응 Top-N이 한 화면에 뜨는 순간 작품의 핵심이 완성된다.
