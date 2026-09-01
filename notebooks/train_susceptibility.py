"""상습 침수 취약도 모델 — 다년 빈도 라벨.

실험 05에서는 젖은 관측 2건이 일치하는 필지를 라벨로 썼고 ROC-AUC 0.629 였다.
여기서는 젖은 관측 15건의 침수 빈도를 라벨로 쓴다.

**마른 관측 대조군을 반드시 함께 본다.**
어떤 필지가 젖은 관측에서 자주 잡히는데 마른 관측에서도 자주 잡힌다면
그 신호는 침수가 아니라 그 필지의 후방산란 특성(시설물·금속구조 등)이다.
취약도라고 부르기 전에 걸러낸다.

실행
    python notebooks/train_susceptibility.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import GroupKFold

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.features import zonal  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
SUS = REPO_ROOT / "data" / "processed" / "features" / "parcel_susceptibility.parquet"
STATIC = REPO_ROOT / "data" / "processed" / "cov" / "static_terrain.tif"
OUT_CSV = REPO_ROOT / "data" / "reference" / "susceptibility_model.csv"

TERRAIN = ["elevation", "slope", "hand", "upa", "twi", "dist_stream"]
MIN_OBS = 10.0
# 침수빈도는 절대 임계로 자르면 안 된다. 젖은 관측 15건의 강우 강도가 제각각이라
# (rain3d 40~143mm) 빈도 상한이 데이터에 따라 달라진다. 실제 분포는 중앙값 0.081,
# 99분위 0.267, 최대 0.600 이었고 절대 임계 0.4 로는 양성이 243개로 붕괴했다.
# 따라서 분위수로 자른다.
POS_PERCENTILE = 90  # 상위 10% = 상습 침수
NEG_PERCENTILE = 25  # 하위 25% = 비침수
DRY_MAX = 0.15  # 마른 관측에서 이보다 자주 잡히면 강우와 무관한 오탐


def main() -> None:
    df = pd.read_parquet(SUS)
    parcels = gpd.read_parquet(
        REPO_ROOT / "data" / "processed" / "farmmap" / "chungnam_2021.parquet",
        columns=["farmmap_id", "geometry"],
    ).reset_index(drop=True)
    pts = parcels.geometry.representative_point()
    terrain = zonal.sample_points(STATIC, pts.x.to_numpy(), pts.y.to_numpy(), names=TERRAIN)
    df = pd.concat([df.reset_index(drop=True), terrain], axis=1)
    df["sigungu"] = df["sgg_nm"].str.replace(r"^천안시.*", "천안시", regex=True)

    print(f"필지 {len(df):,}")
    ok = df[(df["wet_n_obs"] >= MIN_OBS) & (df["dry_n_obs"] >= MIN_OBS)].dropna(subset=TERRAIN).copy()
    print(f"유효필지(젖은/마른 관측 각 {MIN_OBS:.0f}건 이상) {len(ok):,}")

    print("\n[침수빈도 분포]")
    print(ok[["wet_freq", "dry_freq"]].describe(percentiles=[.5, .75, .9, .95, .99]).round(3).to_string())

    print("\n[분류별 평균 빈도]")
    print(ok.groupby("class_nm", observed=True)[["wet_freq", "dry_freq"]].mean().round(3).to_string())

    # --- 오탐 대조 -------------------------------------------------------
    pos_cut = float(np.percentile(ok["wet_freq"], POS_PERCENTILE))
    neg_cut = float(np.percentile(ok["wet_freq"], NEG_PERCENTILE))
    print(f"\n[분위수 임계] 상위 {100-POS_PERCENTILE}% = wet_freq >= {pos_cut:.3f}"
          f" | 하위 {NEG_PERCENTILE}% = wet_freq <= {neg_cut:.3f}")

    high_wet = ok["wet_freq"] >= pos_cut
    print(f"[오탐 대조] 상위 필지 {high_wet.sum():,} 중 dry_freq>{DRY_MAX}:"
          f" {(high_wet & (ok['dry_freq'] > DRY_MAX)).sum():,} (제외)")
    print("  wet_freq 와 dry_freq 상관:", round(float(ok["wet_freq"].corr(ok["dry_freq"])), 3),
          "— 0에 가까우면 신호가 필지 고유 특성이 아니라 강우 기반이라는 뜻")

    ok["y"] = (high_wet & (ok["dry_freq"] <= DRY_MAX)).astype(int)
    neg = (ok["wet_freq"] <= neg_cut) & (ok["dry_freq"] <= DRY_MAX)
    train = ok[ok["y"].eq(1) | neg].copy()
    print(f"\n라벨: 취약 {int(train['y'].sum()):,} / 비취약 {int((~train['y'].astype(bool)).sum()):,}"
          f" (중간대 {len(ok) - len(train):,} 제외)")

    # --- 지형 모델 -------------------------------------------------------
    X, y, g = train[TERRAIN], train["y"], train["sigungu"]
    rows = []
    for fold, (tr, te) in enumerate(GroupKFold(n_splits=5).split(X, y, groups=g), 1):
        model = lgb.LGBMClassifier(n_estimators=400, learning_rate=0.05, num_leaves=63,
                                   min_child_samples=200, n_jobs=-1, verbose=-1)
        model.fit(X.iloc[tr], y.iloc[tr])
        p = model.predict_proba(X.iloc[te])[:, 1]
        rows.append({"fold": fold, "n_test": len(te), "pos_rate": round(float(y.iloc[te].mean()), 4),
                     "roc_auc": round(float(roc_auc_score(y.iloc[te], p)), 4),
                     "pr_auc": round(float(average_precision_score(y.iloc[te], p)), 4)})
        print(f"  fold{fold}: n={len(te):>7,} 양성률 {rows[-1]['pos_rate']:.3f} "
              f"ROC-AUC {rows[-1]['roc_auc']:.3f} PR-AUC {rows[-1]['pr_auc']:.3f}")

    res = pd.DataFrame(rows)
    res.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n[시군 GroupKFold 평균] ROC-AUC {res['roc_auc'].mean():.3f} ± {res['roc_auc'].std():.3f}"
          f" | PR-AUC {res['pr_auc'].mean():.3f} (양성률 {res['pos_rate'].mean():.3f},"
          f" lift {res['pr_auc'].mean()/res['pos_rate'].mean():.2f}x)")
    print("  실험 05 대비: 관측 2건 일치 라벨은 ROC-AUC 0.629 였다")

    print("\n[단일 feature]")
    for c in TERRAIN:
        a = roc_auc_score(y, X[c])
        print(f"  {c:<12} {max(a, 1 - a):.3f} {'(역방향)' if a < 0.5 else ''}")

    # --- 연속값 회귀도 함께 본다 (임계 선택에 의존하지 않는 평가) ---------
    from scipy.stats import spearmanr
    Xa, ya, ga = ok[TERRAIN], ok["wet_freq"], ok["sigungu"]
    rhos = []
    for tr, te in GroupKFold(n_splits=5).split(Xa, ya, groups=ga):
        reg = lgb.LGBMRegressor(n_estimators=400, learning_rate=0.05, num_leaves=63,
                                min_child_samples=200, n_jobs=-1, verbose=-1)
        reg.fit(Xa.iloc[tr], ya.iloc[tr])
        rhos.append(spearmanr(ya.iloc[te], reg.predict(Xa.iloc[te])).statistic)
    print(f"\n[회귀: wet_freq 직접 예측, 시군 GroupKFold] Spearman rho "
          f"{np.mean(rhos):.3f} ± {np.std(rhos):.3f}  (전체 {len(ok):,} 필지, 임계값 없음)")

    model = lgb.LGBMClassifier(n_estimators=400, learning_rate=0.05, num_leaves=63,
                               min_child_samples=200, n_jobs=-1, verbose=-1).fit(X, y)
    print("\n[feature importance]")
    print(pd.Series(model.feature_importances_, index=TERRAIN).sort_values(ascending=False).to_string())


if __name__ == "__main__":
    main()
