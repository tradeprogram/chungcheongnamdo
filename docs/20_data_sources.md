# 데이터 출처와 취득 계획

모든 데이터는 공개데이터만 사용한다. 재현 가능성이 심사 항목의 일부다.

| 데이터 | 용도 | 출처 | 취득 방법 | 우선순위 | 상태 |
|---|---|---|---|---|---|
| Sentinel-1 GRD VV/VH | 실제 침수 evidence, 과거 event label | Copernicus | GEE 컬렉션 | P0 | 미착수 |
| Sentinel-2 L2A | 회복 anomaly (NDVI/EVI/NDMI) | Copernicus | GEE 컬렉션 | P1 | 미착수 |
| 팜맵 | 농경지 필지 경계, 논/밭/과수/시설 속성 | 농림축산식품부 | 대국민 개방 다운로드 | P0 | 미착수 |
| 기상 관측·예보 | 강우 1/3/6/24h, 선행강우 1/3/7d | 기상자료개방포털 | Open API | P0 | 미착수 |
| DEM | slope, HAND, TWI, 하천거리 | 국토지리정보원 | 다운로드 | P0 | 미착수 |
| ERA5-Land | 학습기간 기후 보조변수, gap filling | ECMWF | API | P2 | 미착수 |
| WAMIS | 저수지·양수장·양배수장 제원 | 국가수자원관리종합정보시스템 | 조회 | Could | 미착수 |
| Landsat C2 SR | 장기 baseline, 토지이용 변화 | USGS | GEE | Could | 미착수 |
| 공식 피해 집계 | 사건 서술, 정성 검증 참고 | 도·농업기술원 보도자료 | 수동 | P0 | 문서 인용분만 확보 |

## 좌표계·시간대 규약

- CRS: `EPSG:5179` (Korea 2000 / Unified CS)
- 시간대: `Asia/Seoul`, 모든 timestamp는 ISO8601로 TZ를 명시
- 저장 포맷: 벡터 GeoParquet, 래스터 COG

## 데이터 사용 시 금지사항

- **피해액(원) 예측 금지.** 공식 보상액 label이 없으므로 산출물은
  `Observed Flood Exposure`, `Crop Impact Index`, `Recovery Delay Probability`로 한정한다.
- GEE 분석준비 컬렉션 사용 시 SNAP 전처리를 중복 수행하지 않는다. 어떤 보정이
  이미 적용되어 있는지 파이프라인 문서에 기록한다.
- Demo용 데이터는 전량 캐시한다. 발표 중 API quota·GEE latency로 실패하면 안 된다.
