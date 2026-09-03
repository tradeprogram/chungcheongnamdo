// 충남 농업재해 위성관측 지원 — 정적 뷰어.
// 백엔드 없음. web/data 의 사전 생성 파일만 읽는다 (발표 중 API 실패 방지).

const DATA = "data/";
const PARCEL_ZOOM = 11.5; // 이 축척부터 필지를 불러온다

let storms = [], events = [], stats = null, emdIndex = [];
let filter = "all", tab = "storms", activeStorm = null, loadedEmd = null;

const el = (id) => document.getElementById(id);
const pct = (v) => (v === null || v === undefined ? "-" : `${(v * 100).toFixed(1)}%`);

const map = new maplibregl.Map({
  container: "map",
  style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#0b1218" } }] },
  // 도 전체(zoom 7.6)에서는 침수 후보 픽셀이 화면 1px 미만이라 보이지 않는다.
  center: [126.88, 36.35],
  zoom: 9.2,
  attributionControl: false,
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

// load 리스너는 fetch 보다 먼저 붙인다.
// fetch 를 await 하는 동안 load 가 이미 발생하면, 그 뒤에 리스너를 달아봐야
// 영원히 호출되지 않아 boot() 이 멈춘다.
const mapReady = new Promise((resolve) => {
  if (map.loaded()) resolve();
  else map.on("load", resolve);
});

// MapLibre 는 생성 시점의 컨테이너 크기를 그대로 쓴다.
// 레이아웃이 나중에 확정되면 캔버스가 몇 px 로 남고, 그 상태에서는 load 도 발생하지 않아
// boot() 이 통째로 멈춘다.
// ResizeObserver 의 최초 콜백은 map 생성이 끝나기 전에 돌아 무효가 될 수 있으므로,
// 다음 프레임과 짧은 타이머에서 한 번 더 명시적으로 맞춘다.
new ResizeObserver(() => map.resize()).observe(el("map"));
requestAnimationFrame(() => map.resize());
setTimeout(() => map.resize(), 200);
// 배경 탭에서 열면 컨테이너가 0px 로 잡히고, 그리기가 없으니 load 도 발생하지 않는다.
// 탭이 앞으로 나오는 순간 크기를 다시 잡아 준다.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) map.resize();
});

// 사건별 필지 침수율이 담긴 속성 이름. build_parcel_tiles.py 의 EVENT_COLS 와 같아야 한다.
const PARCEL_FIELD = {
  "o134_2025-07-19": "e2025",
  "o127_2025-07-24": "e2025late",
  "o127_2024-07-17": "e2024",
  "o127_2023-07-23": "e2023",
  "o127_2022-07-16": "e2022",
  "o127_2021-07-21": "e2021",
};

const STATUS = {
  observed_good: { text: "적기 관측", cls: "A" },
  observed_late: { text: "지연 관측", cls: "B" },
  missed: { text: "미관측", cls: "C" },
  pending: { text: "관측 대기", cls: "P" },
};

async function boot() {
  // 자료 파일은 갱신 주기가 짧다. 브라우저 캐시에 걸리면 옛 인덱스를 읽어
  // 필지가 로드되지 않는 식으로 조용히 깨지므로 캐시를 쓰지 않는다.
  const get = (name) => fetch(DATA + name, { cache: "no-cache" }).then((r) => r.json());
  const [s, ev, st, sgg, emd, idx, susMeta] = await Promise.all([
    get("storms.json"),
    get("events.json"),
    get("stats.json"),
    get("sgg.geojson"),
    get("emd.geojson").catch(() => null),
    get("parcel_index.json").catch(() => []),
    get("sus_meta.json").catch(() => null),
  ]);
  storms = s.sort((a, b) => (a.peak_date < b.peak_date ? 1 : -1));
  events = ev;
  stats = st;
  emdIndex = idx;
  if (susMeta && susMeta.wet_obs_max) {
    el("region-hint").textContent =
      `표시된 수치는 강우 관측 최대 ${susMeta.wet_obs_max}회에서 해당 지역 농경지가 ` +
      "침수 후보로 판정된 평균 비율입니다. 시군 항목을 선택하면 하위 읍면동이 전개되며, " +
      "읍면동 항목을 선택하면 지도가 해당 지역으로 이동합니다.";
  }

  await mapReady;

  // 위성 배경. 농지 마스크를 실제 지형 위에 얹으면 "이게 진짜 농지구나"가 바로 전달된다.
  // 외부 타일이라 네트워크가 필요하다. 끊기면 '단색'으로 전환해 데모를 이어간다.
  map.addSource("sat", {
    type: "raster", tileSize: 256,
    tiles: ["https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
    attribution: "Esri, Maxar, Earthstar Geographics",
  });
  map.addLayer({ id: "sat", type: "raster", source: "sat", paint: { "raster-opacity": 1 } });

  // 발표장에서 외부 타일이 막히면 배경만 검게 남고 이유가 화면에 남지 않는다.
  // 실패를 한 번 감지하면 단색으로 넘기고 그 사실을 적는다. 판독 결과 자체는
  // 사전 생성 파일에서 오므로 배경이 없어도 그대로 읽을 수 있다.
  let satFailed = false;
  map.on("error", (e) => {
    if (satFailed || !e.error || !String(e.error.url || e.error.message || "").includes("arcgisonline")) return;
    satFailed = true;
    setBasemap("plain");
    el("overlay-state").textContent = "위성영상 배경을 불러오지 못해 단색 배경으로 전환했습니다";
  });

  if (emd) {
    map.addSource("emd", { type: "geojson", data: emd });
    map.addLayer({
      id: "emd-fill", type: "fill", source: "emd",
      paint: {
        // 값이 없는 읍면동은 회색으로 둔다. 0 으로 보간하면 관측 공백이
        // 가장 안전한 지역처럼 칠해진다.
        "fill-color": ["case", ["==", ["coalesce", ["get", "wet_freq"], -1], -1], "#475569",
          ["interpolate", ["linear"], ["get", "wet_freq"],
          0.05, "#1b3a5c", 0.08, "#2563eb", 0.11, "#f59e0b", 0.14, "#ef4444"]],
        "fill-opacity": 0.55,
      },
      layout: { visibility: "none" },
    });
    map.addLayer({
      id: "emd-line", type: "line", source: "emd",
      paint: { "line-color": "#8ba6c4", "line-width": 0.5, "line-opacity": 0.6 },
      layout: { visibility: "none" },
    });
  }

  map.addSource("sgg", { type: "geojson", data: sgg });
  map.addLayer({
    id: "sgg-fill", type: "fill", source: "sgg",
    paint: { "fill-color": "#1b2a3c", "fill-opacity": 0.55 },
    layout: { visibility: "none" },  // 기본은 위성 배경
  });
  map.addLayer({ id: "sgg-line", type: "line", source: "sgg", paint: { "line-color": "#6b8bb5", "line-width": 0.8 } });
  addLabels(sgg);

  // 필지 레이어 — 데이터는 확대 시 채워 넣는다
  map.addSource("parcels", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  // 위성 영상 위에 얹는 마스크이므로 반투명으로 둔다. 영상이 비쳐야 농지임이 확인된다.
  map.addLayer({
    id: "parcel-fill", type: "fill", source: "parcels",
    paint: { "fill-color": parcelPaint(null), "fill-opacity": 0.55 },
  });
  map.addLayer({
    id: "parcel-line", type: "line", source: "parcels",
    // 테두리로 **판독 근거**를 구분한다. 채움색은 침수율을 나타내므로
    // 근거의 등급까지 같은 채널에 실으면 두 정보가 섞인다.
    //   흰색  면적 집계 (필지 안 화소를 모아 비율을 냄)
    //   주황  대표점 표본 (화소가 배정되지 않아 한 점만 읽음)
    //   적색  급경사 20도 초과 (SAR 기하 왜곡으로 신뢰도 낮음)
    paint: {
      "line-color": ["case",
        ["==", ["get", "stp"], 1], "#ef4444",
        ["==", ["get", "mth"], 1], "#f59e0b",
        "#ffffff"],
      "line-width": ["case",
        ["any", ["==", ["get", "stp"], 1], ["==", ["get", "mth"], 1]], 1.1, 0.6],
      "line-opacity": 0.75,
    },
  });
  // 레이어 지정 핸들러(map.on("click","parcel-fill",...)) 대신
  // 지도 전체 클릭에서 직접 조회한다. 레이어가 아직 없거나 순서가 바뀌어도 동작한다.
  map.on("click", (e) => {
    const layers = ["parcel-fill", "emd-fill"].filter((l) => map.getLayer(l));
    const hit = map.queryRenderedFeatures(e.point, { layers })[0];
    if (!hit) return;
    if (hit.layer.id === "parcel-fill") showParcel(hit.properties);
    else showEmd(hit.properties);
  });
  map.on("mousemove", (e) => {
    const layers = ["parcel-fill", "emd-fill"].filter((l) => map.getLayer(l));
    const hit = layers.length ? map.queryRenderedFeatures(e.point, { layers })[0] : null;
    map.getCanvas().style.cursor = hit ? "pointer" : "";
  });
  map.on("moveend", maybeLoadParcels);

  renderStats();
  renderLive();
  renderStorms();
  renderCompare();
  emdData = emd;
  renderRegions();

  const first = storms.find((x) => overlayFor(x)) || storms[0];
  selectStorm(first.storm_id);
  // 초기 상태를 한 번 맞춘다. moveend 에만 의존하면 boot 이 끝나기 전에 지도를 옮긴 경우
  // (예: 링크로 특정 지역 진입) 필지가 영영 로드되지 않는다.
  maybeLoadParcels();
}

// 필지 색 규칙. 선택한 사건의 침수율 속성(field)으로 칠한다.
// field 가 없으면(그 사건의 판독 결과가 없으면) 농경지 구분만 보여준다.
function parcelPaint(field) {
  const byClass = ["==", ["get", "class_nm"], "논"];
  // 판독 지도가 없는 사건(77건 중 74건)에서는 농경지 구분만 보여준다.
  // 전부 회색으로 칠하면 지도가 아무 정보도 주지 못한다.
  if (!field) return ["case", byClass, "#f5d90a", "#4ade80"];

  const value = ["coalesce", ["get", field], -1];
  return ["case",
    ["==", value, -1], "#94a3b8",              // 그 사건에서 판독 불가 (표본 부족)
    [">=", value, 0.5], "#2563eb",             // 침수율 50% 이상
    [">=", value, 0.2], "#60a5fa",             // 20~50%
    byClass, "#f5d90a",
    "#4ade80"];
}

// 시군명은 symbol 레이어 대신 HTML 마커로 그린다.
// symbol 의 text-field 는 스타일에 glyphs(폰트) URL 을 요구하는데,
// 외부 폰트 서버에 의존하면 오프라인 데모가 깨진다.
function addLabels(sgg) {
  for (const f of sgg.features) {
    const name = f.properties.sgg_nm;
    if (!name) continue;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    const walk = (c) => {
      if (typeof c[0] === "number") {
        minX = Math.min(minX, c[0]); maxX = Math.max(maxX, c[0]);
        minY = Math.min(minY, c[1]); maxY = Math.max(maxY, c[1]);
      } else c.forEach(walk);
    };
    walk(f.geometry.coordinates);
    const div = document.createElement("div");
    div.className = "sgg-label";
    div.textContent = name;
    new maplibregl.Marker({ element: div }).setLngLat([(minX + maxX) / 2, (minY + maxY) / 2]).addTo(map);
  }
}

// --- 필지 지연 로딩 ---------------------------------------------------
// 143만 필지를 한 번에 보내지 않는다. 화면 중심이 속한 읍면동 파일 하나만 받는다.
// setLayoutProperty 는 스타일 로딩이 끝나기 전에 부르면 예외를 던진다.
// moveend 는 그 전에도 날아오므로, 가시성 설정 하나 때문에 필지 로딩 전체가
// 중단되지 않도록 분리한다. 배경 타일이 늦거나 실패해도 필지는 떠야 한다.
function setVisible(layer, on) {
  if (!map.getLayer(layer)) return;
  try {
    map.setLayoutProperty(layer, "visibility", on ? "visible" : "none");
  } catch {
    map.once("idle", () => setVisible(layer, on));
  }
}

async function maybeLoadParcels() {
  const z = map.getZoom();
  const on = z >= PARCEL_ZOOM;
  setVisible("parcel-fill", on);
  setVisible("parcel-line", on);
  // 필지 축척에서는 래스터 오버레이를 끈다. 20m 격자를 확대하면 뭉개져 필지를 가리고,
  // 이미 필지 색이 같은 사건의 침수율을 담고 있다.
  setVisible("overlay-layer", !on);
  if (!on) { el("zoom-hint").textContent = "축척을 확대하면 필지 단위 결과가 표시됩니다"; return; }

  // 읍면동 레이어가 아니라 인덱스의 bbox 로 찾는다.
  // 레이어 렌더 여부에 의존하면 choropleth 를 끈 상태에서 필지가 안 뜬다.
  const c = map.getCenter();
  const hit = emdIndex.find(
    (e) => c.lng >= e.bbox[0] && c.lng <= e.bbox[2] && c.lat >= e.bbox[1] && c.lat <= e.bbox[3]
  );
  if (!hit) { el("zoom-hint").textContent = "해당 위치에 농경지 필지가 없습니다"; return; }
  if (hit.emd_cd === loadedEmd) return;

  el("zoom-hint").textContent = `${hit.sgg_nm} ${hit.emd_nm} 필지 자료 불러오는 중`;
  try {
    const fc = await fetch(`${DATA}parcels/${hit.emd_cd}.json`, { cache: "no-cache" }).then((r) => r.json());
    map.getSource("parcels").setData(fc);
    loadedEmd = hit.emd_cd;
    el("zoom-hint").textContent = `${hit.sgg_nm} ${hit.emd_nm} · 필지 ${fc.features.length.toLocaleString()}개`;
  } catch {
    el("zoom-hint").textContent = "해당 읍면동의 필지 자료가 없습니다";
  }
}

function renderStats() {
  el("stats").innerHTML = `
    <div title="2017년 이후 충남 전역을 80% 이상 덮은 Sentinel-1 통과 횟수"><b>${stats.n_passes}</b><span>누적 관측</span></div>
    <div title="일강수 30mm 또는 3일 누적 50mm 이상으로 추출한 호우 사건 수"><b>${stats.n_storms}</b><span>호우 사건</span></div>
    <div class="hl" title="최대 강수 후 48시간 이내에 관측된 사건의 비율 (등급 A). 나머지는 관측이 늦었거나 촬영되지 않았다"><b>${stats.pct_grade_a}%</b><span>적기 관측률</span></div>
    <div title="충남 전역을 덮는 관측이 한 번도 없었던 사건 수"><b>${stats.n_missed}</b><span>미관측 사건</span></div>`;
  el("archive-hint").textContent = `분석 기간 ${stats.period} · 최종 갱신 ${stats.generated_at}`;
}

// 관측 대기 중인 사건 = 지금 이 순간 행정이 답을 기다리는 사건
// 통과 예정만 알려주면 부족하다. 그 궤도가 실제로 찍을 확률을 함께 적어야
// 담당자가 "기다릴지 지금 나갈지"를 판단할 수 있다. 이것이 ③ 독창성의 핵심이다.
// 최근 3년 기준을 쓴다 — 전체 기간 값은 위성 구성이 바뀌기 전을 포함해 낙관적이다.
const ORBIT_RELIABILITY_RECENT = { 127: 0.67, 134: 0.37 };

function passProbability(s) {
  const p = ORBIT_RELIABILITY_RECENT[s.next_pass_orbit];
  if (!s.next_pass_kst || p === undefined) return "";
  const warn = p < 0.5 ? " live-warn" : "";
  return `<br><span class="live-prob${warn}">orbit ${s.next_pass_orbit} · 최근 3년 촬영 확률
    <b>${Math.round(p * 100)}%</b>${p < 0.5 ? " — 통과해도 촬영되지 않을 수 있습니다" : ""}</span>`;
}

function renderLive() {
  const pending = storms.filter((s) => s.status === "pending");
  if (!pending.length) { el("live").style.display = "none"; return; }
  const s = pending[0];
  el("live").innerHTML = `
    <div class="live-tag">관측 대기</div>
    <div class="live-title">${s.peak_date} 호우 · 최대 일강수량 ${s.peak_mm}mm</div>
    <div class="live-body">
      사건 누적 강수량 ${s.total_mm}mm. 현재까지 충청남도 전역을 포함하는 관측이 없습니다.<br>
      다음 통과 예정 <b>${s.next_pass_kst ?? "미정"}</b>
      ${s.next_pass_lag_hours ? `(최대 강수 시점 후 ${Math.round(s.next_pass_lag_hours / 24)}일)` : ""}
      ${passProbability(s)}
    </div>`;
}

function overlayFor(storm) {
  if (!storm.obs_kst) return null;
  const day = storm.obs_kst.slice(0, 10);
  return events.find((e) => e.observed_kst.slice(0, 10) === day) || null;
}

function renderStorms() {
  const list = storms.filter((s) =>
    filter === "all" ? true : filter === "missed" ? s.status === "missed" : s.grade === filter
  );
  el("storm-list").innerHTML = list
    .map((s) => {
      const st = STATUS[s.status] || STATUS.missed;
      const hasMap = overlayFor(s) ? '<span class="pin">지도</span>' : "";
      return `<li class="event ${s.status}" data-id="${s.storm_id}">
        <div class="event-top">
          <span class="event-label">${s.peak_date}</span>
          ${hasMap}<span class="grade ${st.cls}">${st.text}</span>
        </div>
        <div class="event-meta">최대 ${s.peak_mm}mm · 누적 ${s.total_mm}mm${
        s.lag_hours ? ` · 관측 지연 ${Math.round(s.lag_hours / 24)}일` : ""}</div>
      </li>`;
    })
    .join("");
  document.querySelectorAll("li.event").forEach((li) =>
    li.addEventListener("click", () => selectStorm(li.dataset.id))
  );
}

// 지역 목록 — 시군으로 묶고 그 안에 읍면동을 넣는다.
// 도 전체 249개를 한 줄로 늘어놓으면 담당자가 자기 지역을 찾을 수 없다.
let emdData = null;
const openSgg = new Set();

function renderRegions(query = "") {
  const box = el("region-list");
  if (!emdData) { box.innerHTML = "<p class='hint'>읍면동 데이터 없음</p>"; return; }

  const q = query.trim();
  const rows = emdData.features.map((f) => ({ p: f.properties, g: f.geometry }));
  const matched = q
    ? rows.filter((r) => (r.p.sgg_nm + " " + r.p.emd_nm).includes(q))
    : rows;

  const bySgg = new Map();
  for (const r of matched) {
    if (!bySgg.has(r.p.sgg_nm)) bySgg.set(r.p.sgg_nm, []);
    bySgg.get(r.p.sgg_nm).push(r);
  }
  if (!bySgg.size) { box.innerHTML = "<p class='hint'>검색 결과가 없습니다.</p>"; return; }

  // 시군은 소속 읍면동의 면적가중 평균 침수빈도로 정렬한다
  const groups = [...bySgg.entries()]
    .map(([sgg, list]) => {
      const area = list.reduce((s, r) => s + r.p.area_km2, 0);
      const freq = list.reduce((s, r) => s + r.p.wet_freq * r.p.area_km2, 0) / (area || 1);
      return { sgg, list: list.sort((a, b) => b.p.wet_freq - a.p.wet_freq), area, freq };
    })
    .sort((a, b) => b.freq - a.freq);

  box.innerHTML = groups
    .map((g) => {
      const open = q || openSgg.has(g.sgg);
      const items = g.list
        .map((r) => `<li class="emd-row" data-cd="${r.p.emd_cd}">
            <span class="rank">${r.p.rank}</span>
            <span class="emd-name">${r.p.emd_nm}</span>
            <span class="emd-val">${pct(r.p.wet_freq)}</span>
          </li>`)
        .join("");
      return `<div class="sgg-group">
        <button class="sgg-head${open ? " open" : ""}" data-sgg="${g.sgg}">
          <span class="caret">${open ? "▾" : "▸"}</span>
          <span class="sgg-title">${g.sgg}</span>
          <span class="sgg-meta">${g.list.length}개 · ${g.area.toFixed(0)}km²</span>
          <span class="emd-val">${pct(g.freq)}</span>
        </button>
        <ul class="ranking" ${open ? "" : 'style="display:none"'}>${items}</ul>
      </div>`;
    })
    .join("");

  box.querySelectorAll(".sgg-head").forEach((btn) =>
    btn.addEventListener("click", () => {
      const name = btn.dataset.sgg;
      openSgg.has(name) ? openSgg.delete(name) : openSgg.add(name);
      renderRegions(el("search").value);
    })
  );
  box.querySelectorAll(".emd-row").forEach((li) =>
    li.addEventListener("click", () => {
      const f = emdData.features.find((x) => x.properties.emd_cd === li.dataset.cd);
      if (!f) return;
      showEmd(f.properties);
      zoomTo(f.geometry);
    })
  );
}

function zoomTo(geom) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  const walk = (c) => {
    if (typeof c[0] === "number") {
      minX = Math.min(minX, c[0]); maxX = Math.max(maxX, c[0]);
      minY = Math.min(minY, c[1]); maxY = Math.max(maxY, c[1]);
    } else c.forEach(walk);
  };
  walk(geom.coordinates);
  map.fitBounds([[minX, minY], [maxX, maxY]], { padding: 60, maxZoom: 13 });
}

// 스타일 로딩이 끝난 뒤에 실행한다. addSource/addLayer 는 그 전에 부르면 예외를
// 던지는데, async 함수 안에서 던지면 처리되지 않은 거부로 사라지고 오버레이만
// 조용히 빠진다. 배경 타일이 느린 환경에서 실제로 그렇게 됐다.
function whenStyleReady(fn) {
  if (map.isStyleLoaded()) { fn(); return; }
  map.once("idle", () => whenStyleReady(fn));
}

let overlayWanted = null;

function clearOverlay() {
  if (map.getLayer("overlay-layer")) map.removeLayer("overlay-layer");
  if (map.getSource("overlay")) map.removeSource("overlay");
}

// 도 전역 축척에서 침수 후보 화소는 위성영상의 지형에 묻혀 거의 보이지 않는다.
// 발표자가 배경을 바꿔 주던 동작인데, 설명자 없이 혼자 보는 사람에게는
// "판독 지도를 켰는데 아무것도 없다"로 읽힌다. 화면이 스스로 알려주고 바꿔 준다.
function showOverlayHint() {
  const box = el("overlay-state");
  box.textContent = "";
  const satOn = !map.getLayer("sat") || map.getLayoutProperty("sat", "visibility") !== "none";
  if (!satOn || map.getZoom() >= PARCEL_ZOOM) return;
  box.textContent = "· 위성영상 위에서는 침수 화소가 잘 보이지 않습니다 ";
  const btn = document.createElement("button");
  btn.className = "inline-act";
  btn.type = "button";
  btn.textContent = "단색 배경으로 보기";
  btn.addEventListener("click", () => { setBasemap("plain"); box.textContent = ""; });
  box.appendChild(btn);
}

// MapLibre 는 스타일 로딩이 끝나기 전 addSource/addLayer 에 예외를 던진다.
// isStyleLoaded() 를 기다리는 방식은 배경 타일이 느린 환경에서 콜백이 쌓여
// 순서를 보장하지 못했다(늦게 깨어난 옛 요청이 새 선택을 덮어썼다).
// 그냥 짧은 간격으로 다시 시도한다. 상태가 하나뿐이라 순서가 뒤집히지 않는다.
async function setOverlay(id) {
  overlayWanted = id;
  if (!id) {
    el("overlay-state").textContent = "· 해당 사건은 판독 지도가 생성되지 않았습니다";
    try { clearOverlay(); } catch {}
    return;
  }
  showOverlayHint();
  const bounds = await fetch(`${DATA}overlays/${id}.json`).then((r) => r.json()).catch(() => null);
  if (!bounds || overlayWanted !== id) return;

  for (let attempt = 0; attempt < 40; attempt++) {
    if (overlayWanted !== id) return;        // 그 사이 다른 사건이 선택됐다
    try {
      clearOverlay();
      map.addSource("overlay", {
        type: "image", url: `${DATA}overlays/${id}.png`, coordinates: bounds.coordinates,
      });
      // 기준 레이어가 아직 없을 수 있다(부팅 중 호출). 그때는 맨 위에 얹고,
      // sgg-line 이 생기면 그 아래로 내린다. beforeId 를 고집하면
      // "Cannot add layer before non-existing layer" 로 오버레이가 통째로 빠진다.
      const before = map.getLayer("sgg-line") ? "sgg-line" : undefined;
      map.addLayer({ id: "overlay-layer", type: "raster", source: "overlay",
                     paint: { "raster-opacity": 0.95 } }, before);
      if (!before) map.once("idle", () => {
        if (map.getLayer("overlay-layer") && map.getLayer("sgg-line")) {
          map.moveLayer("overlay-layer", "sgg-line");
        }
      });
      return;
    } catch {
      await new Promise((r) => setTimeout(r, 250));
    }
  }
  el("overlay-state").textContent = "· 판독 지도를 표시하지 못했습니다";
}

const stat = (label, value) => `<div class="stat"><span>${label}</span><b>${value}</b></div>`;

// obs 를 주면 그 관측을 기준으로 상세를 그린다.
// 같은 호우를 두 번 관측한 사건에서 지도만 바꾸고 패널을 두면, 화면이
// "07-24 기준"이라고 하면서 07-19 의 수치를 보여주는 상태가 된다.
function selectStorm(id, obs = null) {
  activeStorm = storms.find((s) => s.storm_id === id);
  document.querySelectorAll("li.event").forEach((li) => li.classList.toggle("active", li.dataset.id === id));

  const s = activeStorm;
  const ev = obs ? events.find((e) => e.id === obs.id) || overlayFor(s) : overlayFor(s);
  el("detail-title").textContent = `${s.peak_date} 호우`;

  let html = stat("사건 기간", `${s.start} ~ ${s.end}`);
  html += stat("최대 일강수량 (도 평균)", `${s.peak_mm} mm`);
  html += stat("사건 누적 강수량", `${s.total_mm} mm`);
  if (obs && ev) {
    html += stat("관측 일시", ev.observed_kst);
    html += stat("관측 지연", `${obs.lag} 시간 (${Math.round(obs.lag / 24)}일) · 등급 ${obs.grade}`);
    html += stat("관측 궤도", `orbit ${ev.rel_orbit}`);
    html += stat("해당 궤도 촬영 성공률", `${Math.round(ev.acquisition_reliability * 100)}%`);
  } else if (s.observed) {
    html += stat("관측 일시", s.obs_kst);
    html += stat("관측 지연", `${s.lag_hours} 시간 (${Math.round(s.lag_hours / 24)}일)`);
    html += stat("관측 궤도", `orbit ${s.rel_orbit}`);
    html += stat("해당 궤도 촬영 성공률", `${Math.round(s.acquisition_reliability * 100)}%`);
  } else if (s.status === "pending") {
    html += stat("다음 통과 예정", s.next_pass_kst ?? "미정");
    if (s.next_pass_lag_hours) html += stat("예상 관측 지연", `${Math.round(s.next_pass_lag_hours / 24)}일`);
  }
  if (ev) {
    html += stat("논 침수 후보 필지 비율", `${ev.paddy_pct} %`);
    html += stat("밭 침수 후보 필지 비율", `${ev.upland_pct} %`);
  }
  // 사유 문구는 호우의 대표 관측을 설명한다. 다른 관측을 보고 있을 때 그대로 두면
  // "40시간 경과, 적합한 관측"이라는 문장이 172시간짜리 관측 옆에 붙는다.
  const GRADE_REASON = {
    A: "최대 강수 시점 후 48시간 이내 관측. 침수 범위 판정에 적합",
    B: "최대 강수 시점 후 48~120시간 관측. 잔존 침수만 관측됨",
    C: "최대 강수 시점 후 120시간 초과 관측. 침수 범위를 대표하지 않음",
  };
  html += `<div class="reason">${obs ? GRADE_REASON[obs.grade] ?? s.reason : s.reason}</div>`;
  if (!ev && s.observed) {
    html += `<p class="hint">해당 사건의 판독 지도는 생성되지 않았습니다.
      관측 이력과 등급은 전 사건에 대해 산출되어 있으며, 판독 지도는 선별 사건에 한해 처리되었습니다.</p>`;
  }
  el("detail").innerHTML = html;
  setOverlay(ev ? ev.id : null);
  setParcelBasis(ev ? ev.id : null);
}

// 필지 색과 범례 표기를 선택한 사건에 맞춘다.
// 이것을 하지 않으면 사건을 바꿔도 필지 색이 그대로여서 지도가 선택과 무관해 보인다.
function setParcelBasis(eventId) {
  const field = eventId ? PARCEL_FIELD[eventId] || null : null;
  if (map.getLayer("parcel-fill")) {
    map.setPaintProperty("parcel-fill", "fill-color", parcelPaint(field));
  }
  const basis = el("legend-basis");
  if (!basis) return;
  if (field) {
    const ev = events.find((e) => e.id === eventId);
    basis.textContent = ` · ${ev ? ev.observed_kst.slice(0, 10) : eventId} 관측 기준`;
  } else {
    basis.textContent = " · 선택 사건 판독 지도 없음 · 농경지 구분만 표시";
  }
}

function showEmd(p) {
  el("detail-title").textContent = `${p.sgg_nm} ${p.emd_nm}`;
  el("detail").innerHTML =
    stat("농경지 면적", `${p.area_km2} km²`) +
    stat("필지 수", Number(p.parcels).toLocaleString()) +
    stat("다년 침수 빈도", p.wet_freq == null ? "관측 부족" : pct(p.wet_freq)) +
    stat("도내 순위", p.rank == null ? "-" : `${p.rank} / ${emdIndex.length || "-"}`) +
    (p.read_pct != null ? stat("필지 판독률", `${p.read_pct}%`) : "") +
    `<p class="hint">다년 침수 빈도는 강우 관측에서 해당 읍면동 농경지가 침수 후보로
     판정된 평균 비율입니다. 축척을 확대하면 개별 필지를 확인할 수 있습니다.</p>
     <p class="hint caution"><b>순위를 그대로 투자 우선순위로 쓰기에는 이릅니다.</b>
     273개 읍면동의 침수 빈도는 6.7%에서 19.6% 사이(중앙값 12.0%)로 최고와 최저의 차이가
     약 2.9배에 그칩니다. 관측이 누적될수록 근거가 강해지는 지표이며, 현 단계에서는
     현장 확인 대상을 좁히는 참고 자료로 쓰는 것이 적절합니다.</p>`;
}

function showParcel(p) {
  el("detail-title").textContent = `필지 ${p.farmmap_id}`;
  let html = stat("소재지", `${p.sgg_nm} ${p.emd_nm}`);
  html += stat("농경지 구분", p.class_nm);
  html += stat("면적", `${Number(p.area_m2).toLocaleString()} m²`);
  // 관측이 없으면 빈도 0% 가 아니라 모르는 것이다.
  html += stat("다년 침수 빈도", p.wet_freq == null ? "관측 부족" : pct(p.wet_freq));
  html += stat("관측 횟수", `${p.wet_n_obs ?? "-"} 회`);
  // 사건 목록은 events.json 에서 끌어온다. 화면에 사건을 추가할 때
  // 여기를 같이 고치는 것을 잊으면 필지 상세만 옛 세 건에 멈춘다.
  // 표본 크기를 숨기지 않는다.
  // 팜맵 필지는 평균 1,500 m² 라 20m 격자에서 화소가 몇 개 안 나온다. 실제로
  // 면적 집계 필지의 절반이 화소 4개 이하이고 11.9%는 화소 1개다. 화소 2개에서
  // 나온 값을 "50.0%" 로 적으면 필지 전체를 재서 얻은 비율처럼 읽힌다.
  //   화소 1개  -> 비율이 존재할 수 없다. 침수 여부로만 적는다
  //   화소 10개 이하 -> 몇 개 중 몇 개인지 같이 적는다
  //   그 이상   -> 비율로 적는다
  const npx = p.mth === 1 ? 1 : (p.npx || 0);
  const single = npx <= 1;
  const coarse = npx > 1 && npx <= 10;
  const readValue = (v) => {
    if (v === null || v === undefined) return "판독값 없음";
    if (single) return v >= 0.5 ? "침수 판정" : "비침수 판정";
    if (coarse) return `화소 ${npx}개 중 ${Math.round(v * npx)}개 (${(v * 100).toFixed(0)}%)`;
    return pct(v);
  };
  html += `<div class="sep">사건별 ${single ? "판정" : "침수율"}</div>`;
  for (const [id, field] of Object.entries(PARCEL_FIELD)) {
    const ev = events.find((x) => x.id === id);
    html += stat(ev ? ev.label : id, readValue(p[field]));
  }
  const basisText = {
    0: `면적 집계 · 유효 화소 ${p.npx ?? "-"}개`,
    1: "대표점 표본 · 화소 1개",
    2: "판독 불가",
  }[p.mth] ?? "판독 근거 미기재";
  html += `<div class="sep">판독 근거</div>`;
  html += stat("집계 방식", basisText);
  html += stat("지형 신뢰도", p.stp === 1 ? "낮음 (경사 20° 초과)" : "정상");
  html += `<p class="hint">침수 여부는 벼 줄기와 수면의 <b>이중반사</b>로 후방산란이 평년보다
    뚜렷하게 커졌는지로 판정합니다(표준화 편차 z&gt;2). ${single
      ? `이 필지는 화소를 하나만 얻어 비율이 성립하지 않으므로 침수 여부로만 제시합니다.
         화소가 많은 필지와 같은 무게로 쓰면 안 됩니다.`
      : coarse
      ? `이 필지는 화소가 ${npx}개뿐이라 비율의 단위가 큽니다. 몇 개 중 몇 개인지를 함께 적습니다.`
      : `필지 안의 화소 ${npx}개 중 그렇게 판정된 화소의 비율입니다.`}</p>`;
  el("detail").innerHTML = html;
}

function renderCompare() {
  const a = events.find((x) => x.id === "o134_2025-07-19");
  const b = events.find((x) => x.id === "o127_2025-07-24");
  if (!a || !b) return;
  const max = Math.max(a.paddy_pct, b.paddy_pct);
  const bar = (e, name) => `<div class="bar-row">
      <div class="bar-label"><span>${name}</span><b>${e.paddy_pct}%</b></div>
      <div class="bar"><div style="width:${(e.paddy_pct / max) * 100}%"></div></div>
    </div>`;
  el("compare-bars").innerHTML =
    bar(a, "07-19 관측 (지연 40시간)") + bar(b, "07-24 관측 (지연 172시간)") +
    `<p class="hint">동일 사건임에도 논 침수 후보 비율이 약 ${(a.paddy_pct / b.paddy_pct).toFixed(0)}배 차이를 보입니다.</p>`;
}

el("filters").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-filter]");
  if (!btn) return;
  filter = btn.dataset.filter;
  document.querySelectorAll("#filters button").forEach((b) => b.classList.toggle("on", b === btn));
  renderStorms();
});

el("tabs").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-tab]");
  if (!btn) return;
  tab = btn.dataset.tab;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b === btn));
  el("storms-pane").style.display = tab === "storms" ? "" : "none";
  el("emd-pane").style.display = tab === "emd" ? "" : "none";
  const showEmdLayer = tab === "emd";
  ["emd-fill", "emd-line"].forEach((l) => {
    if (map.getLayer(l)) map.setLayoutProperty(l, "visibility", showEmdLayer ? "visible" : "none");
  });
  if (map.getLayer("overlay-layer")) {
    map.setLayoutProperty("overlay-layer", "visibility", showEmdLayer ? "none" : "visible");
  }
});

el("search").addEventListener("input", (e) => renderRegions(e.target.value));

// 배경지도 전환 — 위성 타일이 안 뜰 때 단색으로 넘어가 데모를 이어간다.
// 타일 실패를 감지했을 때도 같은 경로로 넘어가야 하므로 함수로 둔다.
function setBasemap(kind) {
  const sat = kind === "sat";
  document.querySelectorAll("#basemap-switch button").forEach(
    (b) => b.classList.toggle("on", b.dataset.base === kind));
  // 스타일 로딩 전에는 예외가 난다. 단추만 눌린 채 배경이 그대로면 사용자는
  // 무엇이 잘못됐는지 알 수 없으므로 준비된 뒤 다시 적용한다.
  try {
    setVisible("sat", sat);
    setVisible("sgg-fill", !sat);
    // 위성 위에서는 시군 경계선을 밝게, 단색 배경에서는 원래대로
    if (map.getLayer("sgg-line")) {
      map.setPaintProperty("sgg-line", "line-color", sat ? "#ffffff" : "#6b8bb5");
      map.setPaintProperty("sgg-line", "line-width", sat ? 1.2 : 0.8);
    }
  } catch {
    map.once("idle", () => setBasemap(kind));
  }
}

el("basemap-switch").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-base]");
  if (!btn) return;
  setBasemap(btn.dataset.base);
  // 안내가 가리키던 상황이 끝났으면 안내도 사라져야 한다
  if (overlayWanted) showOverlayHint();
});

document.querySelector(".compare").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-overlay]");
  if (!btn) return;
  const id = btn.dataset.overlay;
  // 이 비교 위젯이 다루는 호우를 함께 갱신한다. 지도·범례·상세가 같은 관측을
  // 가리켜야 발표 중 화면이 서로 모순되지 않는다.
  // 호우를 관측일로 역추적하면 안 된다 — 늦은 관측(07-24)은 사건 기간
  // (07-17~07-20) 밖이라 어떤 호우에도 걸리지 않는다. 위젯이 대상을 명시한다.
  const stormId = e.currentTarget.dataset.storm;
  if (stormId) {
    // selectStorm 이 지도·범례까지 갱신한다. 여기서 또 부르면 같은 클릭에
    // 오버레이 요청이 두 번 들어가고 늦게 끝난 쪽이 이긴다.
    selectStorm(stormId, { id, lag: Number(btn.dataset.lag), grade: btn.dataset.grade });
  } else {
    setOverlay(id);
    setParcelBasis(id);
  }
});


// --- 이용 안내 -------------------------------------------------------
// 지도 조작을 처음 접하는 이용자를 위해 최초 방문 시 자동으로 표시한다.
// 표시 여부는 브라우저에만 저장되며 서버로 전송되지 않는다.
const HELP_KEY = "cn-obs-help-seen";

function setHelp(open) {
  el("help-overlay").hidden = !open;
  if (open) {
    try { localStorage.setItem(HELP_KEY, "1"); } catch (e) { /* 저장 불가 환경 무시 */ }
  }
}

el("help-btn").addEventListener("click", () => setHelp(true));
el("help-close").addEventListener("click", () => setHelp(false));
el("help-overlay").addEventListener("click", (e) => {
  if (e.target === el("help-overlay")) setHelp(false);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") setHelp(false);
});

let seen = false;
try { seen = localStorage.getItem(HELP_KEY) === "1"; } catch (e) { seen = false; }
if (!seen) setHelp(true);

boot();
