"""공공데이터포털 팜맵 시도 파일(ZIP)에서 충남 필지를 추출한다.

입력
    data/raw/farmmap/farmmap_chungnam_2021.zip
    (공공데이터포털 15062415 "팜맵정보_충청남도_20211231", 431MB,
     시군구별 CSV + SHP 세트, 압축해제 6.6GB)

출력
    data/processed/farmmap/chungnam_2021.parquet   전체 필지, EPSG:5179
    data/reference/farmmap_chungnam_2021_summary.csv  시군 x 분류 집계 (커밋됨)

원본 스키마 (SHP, EPSG:5179, CP949)
    FMAP_INNB   팜맵ID
    PNU_LNM_CD  PNU (19자리)
    LGL_EMD_CD  법정동코드      LGL_EMD_NM  법정동명
    INTPR_CD    농경지분류코드   INTPR_NM    01=논 02=밭 03=과수 04=시설
    CHG_CFNM    갱신유형 (변경/유지)
    VDPT_YR     판독영상 촬영일자
    ITPINP_DE   판독입력일자

**논/밭 구분(INTPR_CD)이 이 데이터를 쓰는 이유다.**
실험 01에서 논 담수 confound 때문에 분리 없이는 침수 판별이 성립하지 않음이 확인됐다.

한계 — 이 파일은 **2021년 기준**이고 Golden Event는 2025-07이다. 4년 사이의
논/밭 전환·경지정리·시설 신축은 반영되지 않는다. 최신 갱신분은 팜맵 WFS
(`src/features/farmmap.py`)로 표본 대조한다.

압축을 통째로 풀지 않는다. 시군 하나씩 임시 추출 -> 필요한 컬럼만 남기고 -> 즉시 삭제한다.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ZIP = REPO_ROOT / "data" / "raw" / "farmmap" / "farmmap_chungnam_2021.zip"
WORK_CRS = "EPSG:5179"
SHP_EXTS = (".shp", ".shx", ".dbf", ".prj", ".cpg")

KEEP = {
    "FMAP_INNB": "farmmap_id",
    "PNU_LNM_CD": "pnu",
    "LGL_EMD_CD": "emd_cd",
    "LGL_EMD_NM": "emd_nm",
    "INTPR_CD": "class_cd",
    "INTPR_NM": "class_nm",
    "CHG_CFNM": "change_type",
    "VDPT_YR": "image_date",
    "ITPINP_DE": "interp_date",
}

CLASS_NAMES = {"01": "논", "02": "밭", "03": "과수", "04": "시설"}


def _decode(info: zipfile.ZipInfo) -> str:
    """ZIP 내부 한글 파일명은 CP949로 저장돼 있다."""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.orig_filename.encode("cp437", "ignore").decode("cp949")
    except Exception:
        return info.filename


def shp_sets(zf: zipfile.ZipFile) -> dict[str, list[tuple[str, zipfile.ZipInfo]]]:
    sets: dict[str, list[tuple[str, zipfile.ZipInfo]]] = {}
    for info in zf.infolist():
        name = _decode(info)
        if "_SHP_" in name and name.lower().endswith(SHP_EXTS):
            stem = name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            sets.setdefault(stem, []).append((name, info))
    return sets


def sgg_from_stem(stem: str) -> str:
    """'팜맵정보_SHP_충청남도_부여군_2021' -> '부여군'."""
    return stem.split("_")[-2]


def read_one(zf: zipfile.ZipFile, members: list[tuple[str, zipfile.ZipInfo]]) -> gpd.GeoDataFrame:
    tmp = Path(tempfile.mkdtemp(prefix="farmmap_"))
    try:
        for name, info in members:
            ext = name.rsplit(".", 1)[-1]
            with zf.open(info) as src, open(tmp / f"layer.{ext}", "wb") as dst:
                shutil.copyfileobj(src, dst)
        gdf = gpd.read_file(tmp / "layer.shp", encoding="cp949")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    cols = [c for c in KEEP if c in gdf.columns]
    out = gdf[cols + ["geometry"]].rename(columns=KEEP)
    if out.crs is None:
        out = out.set_crs(WORK_CRS)
    return out.to_crs(WORK_CRS)


def build(zip_path: Path, out_path: Path, summary_path: Path) -> None:
    zf = zipfile.ZipFile(zip_path)
    sets = shp_sets(zf)
    print(f"시군 세트 {len(sets)}개")

    parts = []
    for stem in sorted(sets):
        sgg = sgg_from_stem(stem)
        gdf = read_one(zf, sets[stem])
        gdf["sgg_nm"] = sgg
        gdf["area_m2"] = gdf.area
        parts.append(gdf)
        print(f"  {sgg:<12} 필지 {len(gdf):>8,}  면적 {gdf['area_m2'].sum()/1e6:>8.1f} km2")

    all_gdf = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=WORK_CRS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    all_gdf.to_parquet(out_path)

    summary = (
        all_gdf.groupby(["sgg_nm", "class_nm"])
        .agg(parcels=("farmmap_id", "size"), area_km2=("area_m2", lambda s: round(s.sum() / 1e6, 2)))
        .reset_index()
    )
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")

    print(f"\n총 필지 {len(all_gdf):,}개, 면적 {all_gdf['area_m2'].sum()/1e6:,.1f} km2")
    print(f"-> {out_path}  ({out_path.stat().st_size/1e6:.1f} MB)")
    print("\n[농경지분류]")
    pivot = summary.groupby("class_nm")[["parcels", "area_km2"]].sum()
    print(pivot.to_string())


def main() -> None:
    parser = argparse.ArgumentParser(description="팜맵 시도 ZIP -> GeoParquet")
    parser.add_argument("--zip", type=Path, default=DEFAULT_ZIP)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "processed" / "farmmap" / "chungnam_2021.parquet")
    parser.add_argument(
        "--summary", type=Path, default=REPO_ROOT / "data" / "reference" / "farmmap_chungnam_2021_summary.csv"
    )
    args = parser.parse_args()
    build(args.zip, args.out, args.summary)


if __name__ == "__main__":
    main()
