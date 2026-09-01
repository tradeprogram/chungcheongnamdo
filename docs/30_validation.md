# 검증 설계

연구 신뢰성 축. Winning MVP 8개 중 하나이며 제안서에 성능표로 들어간다.

## 분할 원칙

**동일 호우사건의 인접 픽셀을 train/test로 무작위 분리하는 leakage는 금지한다.**
`models/flood_forecast.py`의 `train()`은 random split을 지원하지 않는다.

### Spatial holdout
- Train: 천안·공주·보령·아산·서산·논산·계룡·당진·금산·부여·서천·청양·홍성·예산 일부
- Test: **특정 시·군 전체**
- 목적: 옆 필지를 맞히는가가 아니라, 보지 못한 시·군에도 일반화되는가

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
