# 검증 설계

연구 신뢰성 축. Winning MVP 8개 중 하나이며 제안서에 성능표로 들어간다.

## 분할 원칙

**동일 호우사건의 인접 픽셀을 train/test로 무작위 분리하는 leakage는 금지한다.**
`models/flood_forecast.py`의 `train()`은 random split을 지원하지 않는다.

### Spatial holdout
- 그룹 단위: **시·군 전체**를 통째로 test로 분리
- 목적: 옆 필지를 맞히는가가 아니라, 보지 못한 시·군에도 일반화되는가
- 대상 15개 시·군 (SGIS 시도코드 34):
  천안시 · 공주시 · 보령시 · 아산시 · 서산시 · 논산시 · 계룡시 · 당진시 ·
  금산군 · 부여군 · 서천군 · 청양군 · 홍성군 · 예산군 · **태안군**
- 경계는 `src/features/aoi.py`가 생성한 `data/processed/aoi/chungnam_adm_dong.parquet`
  (행정동 208개, EPSG:5179) 기준. 시군 배정은 `sigungu_nm`, 구 단위 분석은 `sgg_nm` 사용.
- 천안시는 원본에서 동남구·서북구로 분리되어 시군구 코드가 16개다.
  spatial holdout 그룹은 구가 아니라 **시** 단위(`sigungu_nm`)로 묶어야 leakage가 없다.
- 계룡시는 행정동 4개로 표본이 매우 적다. 단독 test fold로 쓰지 말고 결과 해석 시 주의한다.

### Temporal holdout
- Train: 과거 사건
- Validation: 가장 최근 과거연도
- Final untouched test: 2025 집중호우
- 2026 사건은 모델선정에 쓰지 않고 최종 demonstration only로 남긴다

### Event holdout
- Event A, B, C 학습 → Event D 전량 평가

## 평가지표

| 대상 | 핵심 지표 |
|---|---|
| SAR flood extent | IoU, F1, Recall |
| 필지 flooded / not | Precision, Recall, PR-AUC |
| 사전 flood probability | PR-AUC, ROC-AUC, Brier, ECE, calibration curve |
| impact / recovery | MAE, RMSE 또는 ordinal F1 |
| 현장 priority ranking | Precision@K, Recall@K, NDCG |
| 시나리오 | 고위험 면적 coverage, travel-hour |
| Agent | unsupported-claim rate, tool grounding rate |

침수 필지는 전체 필지보다 적으므로 **class imbalance 하에서 ROC-AUC만 보여주는 것은 불충분하다.**

## Baseline 대비 성능표 (제안서 수록)

| 모델 | PR-AUC | Recall | Brier | ECE |
|---|---|---|---|---|
| Baseline A (HAND + 누적강우 rule) | | | | |
| Baseline B (Logistic Regression) | | | | |
| **LightGBM + calibration** | | | | |

LightGBM이 baseline 대비 유의미한 이득을 내지 못하면 모델을 단순화한다.
필요한 기술만 쓰는 것 자체가 설명가능성을 높인다.

## 불확실성 분리 (4종)

| 불확실성 | 방법 | UI 표기 |
|---|---|---|
| 사전예측 | ensemble + probability calibration | 0.82 ± confidence |
| SAR 관측 | classifier ensemble, evidence agreement | High / Medium / Low |
| 피해지수 | bootstrap 또는 quantile | 범위 표시 |
| Scenario | 입력 가정별 sensitivity | P10 / P50 / P90 |
