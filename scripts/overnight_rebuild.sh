#!/usr/bin/env bash
# 침수빈도 수출이 끝나면 타일과 화면 자료를 이어서 다시 만든다.
#
# 무인 실행이므로 "프로세스가 없다"는 판정을 한 번 보고 진행하면 안 된다.
# powershell 조회가 일시적으로 실패하면 수출이 도는 중에 부분 결과로 마무리해 버리고,
# 나중에 진짜로 끝나도 아무도 다시 만들지 않는다. 연속 3회 확인을 요구한다.
cd "C:/for_chungcheongnamdo" || exit 1
S="C:/Users/user/AppData/Local/Temp/claude/C--for-chungcheongnamdo/a265ad38-9b0b-4bf7-9246-80b7c4ca964e/scratchpad"
export PYTHONIOENCODING=utf-8

running() {
  powershell -NoProfile -Command \
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*susceptibility_v2*' } | Measure-Object | Select-Object -ExpandProperty Count" \
    2>/dev/null | tr -d '\r\n '
}

echo "[$(date +%H:%M)] 침수빈도 수출 종료 대기"
gone=0
while [ "$gone" -lt 3 ]; do
  n=$(running)
  if [ -z "$n" ]; then
    echo "[$(date +%H:%M)] 프로세스 조회 실패 — 판정 보류"
  elif [ "$n" = "0" ]; then
    gone=$((gone + 1))
    echo "[$(date +%H:%M)] 프로세스 없음 확인 $gone/3"
  else
    gone=0
  fi
  [ "$gone" -lt 3 ] && sleep 120
done

echo "[$(date +%H:%M)] 수출 종료"
if [ ! data/processed/features/parcel_susceptibility.parquet -nt notebooks/build_susceptibility_v2.py ]; then
  echo "[$(date +%H:%M)] parquet 미갱신 — 부분 결과로 마무리"
  python -u notebooks/finalize_susceptibility.py > "$S/finalize.log" 2>&1 \
    || { echo "[$(date +%H:%M)] 마무리 실패 — 로그 확인 필요"; exit 1; }
fi

echo "[$(date +%H:%M)] 필지 타일 재생성"
python -u notebooks/build_parcel_tiles.py > "$S/tiles_v4.log" 2>&1 || echo "  타일 실패"
echo "[$(date +%H:%M)] 화면 자료 재생성"
python -u notebooks/build_web_data.py > "$S/webdata_v4.log" 2>&1 || echo "  화면 자료 실패"
echo "[$(date +%H:%M)] 수치 검산"
python -u notebooks/verify_proposal_numbers.py > "$S/verify.log" 2>&1 \
  && echo "  검산 통과" || echo "  검산 불일치 — verify.log 확인"
echo "[$(date +%H:%M)] 완료"
