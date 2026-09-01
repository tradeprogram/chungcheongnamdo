# 데이터 출처와 취득 계획

모든 데이터는 공개데이터만 사용한다. 재현 가능성이 심사 항목의 일부다.

| 데이터 | 용도 | 출처 | 취득 방법 | 우선순위 | 상태 |
|---|---|---|---|---|---|
| Sentinel-1 GRD VV/VH | 실제 침수 evidence, 과거 event label | Copernicus | OData 카탈로그 / GEE | P0 | **가용성 확인 완료**, 다운로드 계정 필요 |
| Sentinel-2 L2A | 회복 anomaly (NDVI/EVI/NDMI) | Copernicus | GEE 컬렉션 | P1 | 미착수 |
| 팜맵 | 농경지 필지 경계, 논/밭/과수/시설 속성 | 농림축산식품부 / EPIS | ① 공공데이터포털 파일(SHP) ② 팜맵 WFS | P0 | 경로 확인, **미확보** |

## 팜맵 확보 경로 — 두 가지, 성격이 다르다

**① 공공데이터포털 파일데이터 (대량 확보용)**
- `https://www.data.go.kr/data/15062415/fileData.do` — 시도별 SHP + 속성 CSV, 로그인 불필요
- 파일명 규칙 `팜맵정보_CSV_시도명_시군구명_년도.csv`
- **다만 최신 공개분이 2021년 기준이다.** Golden Event는 2025-07이므로 4년 시차가 있다.
  논/밭 전환·경지정리·시설 신축이 반영되지 않는다. MVP에서는 한계로 명시하고 쓰되,
  아래 WFS로 표본 대조해 변화율을 파악한다.

**② 팜맵 WFS (최신·표본 검증용)** — `src/features/farmmap.py`
- `https://agis.epis.or.kr/ASD/farmmapApi/wfs.do`
- 인증: `apiKey` + `domain` **두 개 모두 필수.** `domain`은 키 발급 시 등록한 URL이며
  값이 다르면 "요청서버의 도메인과 등록하신 도메인 정보가 다릅니다"로 거부된다.
  두 값은 `.env`에 두고 git에서 제외한다 (`.env.example` 참조).
- 1회 최대 **200건**, bbox + `startindex` 페이징. 전 충남을 이것만으로 받기에는 요청량이 크다.
- bbox 축 순서 주의: EPSG:5179/4326은 `ymin,xmin,ymax,xmax` (EPSG:3857과 반대).
- 제공 속성: `clsf_nm`(논/밭/과수/시설), `pnu`, `ldcg_cd`(지목), `area`, `flight_ymd`, `updt_ymd`

> 팜맵 사이트 공지: **2026-09-02(수) 18:00~24:00 시스템 작업으로 서비스 일시 중단 예정.**
| 기상 관측·예보 | 강우 1/3/6/24h, 선행강우 1/3/7d | 기상자료개방포털 | Open API | P0 | 미착수 |
| DEM | slope, HAND, TWI, 하천거리 | 국토지리정보원 | 다운로드 | P0 | 미착수 |
| ERA5 재분석 | **사건 window 특정 보조자료** (KMA 대체 아님) | Open-Meteo archive | API, 인증 불필요 | P2 | **확보** |
| WAMIS | 저수지·양수장·양배수장 제원 | 국가수자원관리종합정보시스템 | 조회 | Could | 미착수 |
| Landsat C2 SR | 장기 baseline, 토지이용 변화 | USGS | GEE | Could | 미착수 |
| 공식 피해 집계 | 사건 서술, 정성 검증 참고 | 도·농업기술원 보도자료 | 수동 | P0 | 문서 인용분만 확보 |
| 행정동 경계 | AOI 정의, 시군 spatial holdout 그룹 | 통계청 SGIS (BND_ADM_DONG_PG) | 다운로드 | P0 | **확보** (2025-06-30 기준) |

## AOI (충청남도)

`src/features/aoi.py` 가 SGIS 행정동 경계에서 충남만 잘라 3종을 만든다.

```bash
python src/features/aoi.py --src data/raw/admin_boundary/BND_ADM_DONG_PG.shp
```

| 산출물 | 내용 | git |
|---|---|---|
| `data/processed/aoi/chungnam_adm_dong.parquet` | 행정동 208개, EPSG:5179, 전체 해상도 | 제외 |
| `data/aoi/chungnam_sgg.geojson` | 시군구 16개 dissolve, EPSG:4326, 50m 단순화 | 포함 |
| `data/aoi/chungnam_boundary.geojson` | 충남 외곽, EPSG:4326, 50m 단순화 — GEE AOI | 포함 |

- 면적 합계 8,264.7 km², bbox `125.5409, 35.9783, 127.6397, 37.0800` (서쪽 끝은 태안군 관할 도서).
- 원본 코드 체계는 SGIS 8자리(시도 2 + 시군구 3 + 행정동 3), 시도코드 **34** = 충청남도.
  세종특별자치시(29)는 별도 시도이므로 포함하지 않는다.
- 원본에 시군구명 필드가 없어 소속 읍·면 이름으로 식별했다. `SGG_NAMES` 매핑은
  공식 행정구역 코드표와 한 번 대조할 것.
- 커밋된 GeoJSON 2종은 **표시·AOI 전용**이다. 필지를 시군에 배정하는 분석용 조인에는
  단순화되지 않은 parquet을 쓴다.
- 원본 shapefile 인코딩은 CP949, 크기 135MB로 `data/raw/`에 두고 git에서 제외한다.

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
