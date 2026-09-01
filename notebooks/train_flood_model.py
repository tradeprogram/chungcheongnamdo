"""사전예측 모델 — baseline 대비 LightGBM.

라벨
    y = (double_fraction >= 0.5)  — 관측 시점에 필지의 절반 이상이 침수 후보
    n_valid >= 3 인 필지만 사용 (20m 격자에서 표본이 너무 적은 필지 제외)

**평가에서 반드시 구분할 것 — pooled 지표는 부풀려진다.**
강우는 사건 단위 변수이고 사건별 양성률이 0.2%(마른 관측)에서 26%(젖은 관측)까지 벌어진다.
모든 사건을 섞어 계산한 PR-AUC 는 "비가 온 날이었는지"만 맞혀도 높게 나온다.
따라서 두 가지를 나눠 보고한다.

    pooled       모든 사건을 합친 지표. 사건 판별력이 섞여 있어 과대평가된다.
    within-event 사건 하나 안에서의 순위 판별력. **이것이 운영상 의미 있는 지표다.**
                 "같은 강우 아래 어느 필지가 먼저 잠기는가" 에 답하는 능력.

검증 분할
    event holdout   사건 하나를 통째로 빼고 학습 (leave-one-event-out)
    spatial holdout 시군 단위 GroupKFold. 천안시는 2개 구를 시 단위로 묶는다

baseline
    A. rule       HAND 와 선행강우만 쓴 규칙 (rain3d / (1 + hand))
    B. logistic   전체 수치형 feature 로 로지스틱 회귀
    main. LightGBM + isotonic calibration

실행
    python notebooks/train_flood_model.py
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]
FEATURES = REPO_ROOT / "data" / "processed" / "features" / "field_event_features.parquet"
OUT_DIR = REPO_ROOT / "data" / "reference"

NUMERIC = ["hand", "slope", "elevation", "upa", "twi", "wth", "area_m2",
           "rain1d", "rain3d", "rain7d", "rain14d", "rain30d"]
CATEGORICAL = ["class_nm"]
LABEL_THRESHOLD = 0.5
MIN_VALID_PIXELS = 3


def load() -> pd.DataFrame:
    df = pd.read_parquet(FEATURES)
    df = df[df["n_valid"] >= MIN_VALID_PIXELS].copy()
    df["y"] = (df["double_fraction"] >= LABEL_THRESHOLD).astype(int)
    df["class_nm"] = df["class_nm"].astype("category")
    # 천안시 동남구/서북구를 시 단위로 묶어 spatial holdout leakage 를 막는다
    df["sigungu"] = df["sgg_nm"].str.replace(r"^천안시.*", "천안시", regex=True)
    df = df.dropna(subset=NUMERIC)
    return df


def ece(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    """Expected Calibration Error."""
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    total = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            total += m.mean() * abs(y[m].mean() - p[m].mean())
    return float(total)


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    out = {"n": len(y), "pos_rate": round(float(y.mean()), 4)}
    if y.sum() == 0 or y.sum() == len(y):
        return {**out, "pr_auc": np.nan, "roc_auc": np.nan, "brier": np.nan, "ece": np.nan}
    return {
        **out,
        "pr_auc": round(float(average_precision_score(y, p)), 4),
        "roc_auc": round(float(roc_auc_score(y, p)), 4),
        "brier": round(float(brier_score_loss(y, p)), 5),
        "ece": round(ece(y, p), 4),
    }


def baseline_rule(df: pd.DataFrame) -> np.ndarray:
    """HAND 와 선행강우만 쓴 규칙. 확률이 아니라 순위 점수다."""
    score = df["rain3d"].to_numpy() / (1.0 + np.maximum(df["hand"].to_numpy(), 0))
    return (score - score.min()) / (np.ptp(score) + 1e-9)


def fit_predict(train: pd.DataFrame, test: pd.DataFrame) -> dict[str, np.ndarray]:
    preds: dict[str, np.ndarray] = {}
    preds["A_rule"] = baseline_rule(test)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        logit = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, n_jobs=-1))
        logit.fit(train[NUMERIC], train["y"])
        preds["B_logistic"] = logit.predict_proba(test[NUMERIC])[:, 1]

    model = lgb.LGBMClassifier(
        n_estimators=400, learning_rate=0.05, num_leaves=63,
        min_child_samples=200, subsample=0.8, subsample_freq=1,
        colsample_bytree=0.8, n_jobs=-1, verbose=-1,
    )
    model.fit(train[NUMERIC + CATEGORICAL], train["y"], categorical_feature=CATEGORICAL)
    preds["C_lightgbm"] = model.predict_proba(test[NUMERIC + CATEGORICAL])[:, 1]
    preds["_model"] = model
    return preds


def main() -> None:
    df = load()
    print(f"표본 {len(df):,}행 | 양성률 {df['y'].mean()*100:.2f}%")
    print("\n[사건별 양성률 %]")
    print((df.groupby("event_id")["y"].agg(["size", "mean"]).assign(mean=lambda d: (d["mean"] * 100).round(2))).to_string())

    # ---------- event holdout ----------
    rows, within = [], []
    for event in df["event_id"].unique():
        train, test = df[df["event_id"] != event], df[df["event_id"] == event]
        preds = fit_predict(train, test)
        model = preds.pop("_model")
        for name, p in preds.items():
            rows.append({"split": "event_holdout", "held_out": event, "model": name, **metrics(test["y"].to_numpy(), p)})
        # within-event: 그 사건 안에서의 순위 판별력
        for name, p in preds.items():
            within.append({"event": event, "model": name, **metrics(test["y"].to_numpy(), p)})
        if event == "o127_2023-07-23":
            imp = pd.Series(model.feature_importances_, index=NUMERIC + CATEGORICAL).sort_values(ascending=False)
            print(f"\n[feature importance — {event} 제외 학습]")
            print(imp.head(10).to_string())

    event_df = pd.DataFrame(rows)

    # ---------- spatial holdout (시군 GroupKFold) ----------
    spatial = []
    gkf = GroupKFold(n_splits=5)
    for fold, (tr, te) in enumerate(gkf.split(df, df["y"], groups=df["sigungu"]), 1):
        train, test = df.iloc[tr], df.iloc[te]
        preds = fit_predict(train, test)
        preds.pop("_model")
        held = sorted(test["sigungu"].unique())
        for name, p in preds.items():
            spatial.append({"split": "spatial_holdout", "fold": fold, "n_sigungu": len(held),
                            "model": name, **metrics(test["y"].to_numpy(), p)})
    spatial_df = pd.DataFrame(spatial)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    event_df.to_csv(OUT_DIR / "model_event_holdout.csv", index=False, encoding="utf-8-sig")
    spatial_df.to_csv(OUT_DIR / "model_spatial_holdout.csv", index=False, encoding="utf-8-sig")

    print("\n" + "=" * 78)
    print("[event holdout — 사건 하나를 통째로 빼고 학습]  ※ pooled 아님, 해당 사건 내부 지표")
    print(event_df.pivot(index="held_out", columns="model", values="pr_auc").to_string())
    print("\n  양성률 (기준선: 무작위 예측의 PR-AUC)")
    print(event_df[event_df["model"] == "C_lightgbm"].set_index("held_out")["pos_rate"].to_string())

    print("\n[spatial holdout — 시군 GroupKFold 5분할, 모든 사건 pooled]")
    print(spatial_df.groupby("model")[["pr_auc", "roc_auc", "brier", "ece"]].mean().round(4).to_string())
    print("\n※ spatial 결과는 사건이 섞여 있어 사건 판별력이 포함된다. 과대평가로 읽을 것.")


if __name__ == "__main__":
    main()
