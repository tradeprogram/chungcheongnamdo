"""필지별 사전 침수확률 예측.

Baseline을 반드시 함께 유지한다. LightGBM이 baseline보다 얼마나 나아졌는가를
보여주는 것이 Transformer를 썼다는 주장보다 강하다.

    Baseline A : HAND + 누적강우 rule
    Baseline B : Logistic Regression
    Main       : LightGBM + probability calibration

평가는 class imbalance를 전제로 PR-AUC, Recall, Brier, ECE, calibration curve를
함께 보고한다. ROC-AUC 단독 제시는 불충분하다.

검증 분할은 반드시 spatial(시군 holdout), temporal(연도), event(사건) 단위로 한다.
동일 호우사건의 인접 픽셀을 무작위 분리하는 leakage는 금지한다.

TODO(P1): baseline 성능표 -> LightGBM 성능표 -> event holdout pipeline
"""

from __future__ import annotations


def baseline_rule(features):
    """HAND + 누적강우 규칙 기반 위험도."""
    raise NotImplementedError


def train(features, labels, split: str = "event"):
    """split 은 event / spatial / temporal 중 하나. random split은 지원하지 않는다."""
    raise NotImplementedError


def predict(model, features):
    """calibrated probability 와 uncertainty 반환."""
    raise NotImplementedError


def explain(model, features):
    """SHAP reason code. 조작 가능 변수와 불가능 변수를 구분해 반환한다."""
    raise NotImplementedError
