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

const STATUS = {
  observed_good: { text: "적기 관측", cls: "A" },
  observed_late: { text: "지연 관측", cls: "B" },
  missed: { text: "미관측", cls: "C" },
  pending: { text: "관측 대기", cls: "P" },
};

async function boot() {
  const [s, ev, st, sgg, emd, idx] = await Promise.all([
    fetch(DATA + "storms.json").then((r) => r.json()),
    fetch(DATA + "events.json").then((r) => r.json()),
    fetch(DATA + "stats.json").then((r) => r.json()),
    fetch(DATA + "sgg.geojson").then((r) => r.json()),
    fetch(DATA + "emd.geojson").then((r) => r.json()).catch(() => null),
    fetch(DATA + "parcel_index.json").then((r) => r.json()).catch(() => []),
  ]);
  storms = s.sort((a, b) => (a.peak_date < b.peak_date ? 1 : -1));
  events = ev;
  stats = st;
  emdIndex = idx;

  await mapReady;

  // 위성 배경. 농지 마스크를 실제 지형 위에 얹으면 "이게 진짜 농지구나"가 바로 전달된다.
  // 외부 타일이라 네트워크가 필요하다. 끊기면 '단색'으로 전환해 데모를 이어간다.
  map.addSource("sat", {
    type: "raster", tileSize: 256,
    tiles: ["https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
    attribution: "Esri, Maxar, Earthstar Geographics",
  });
  map.addLayer({ id: "sat", type: "raster", source: "sat", paint: { "raster-opacity": 1 } });

  if (emd) {
    map.addSource("emd", { type: "geojson", data: emd });
    map.addLayer({
      id: "emd-fill", type: "fill", source: "emd",
      paint: {
        "fill-color": ["interpolate", ["linear"], ["coalesce", ["get", "wet_freq"], 0],
          0.05, "#1b3a5c", 0.08, "#2563eb", 0.11, "#f59e0b", 0.14, "#ef4444"],
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
    paint: {
      "fill-color": ["case",
        [">=", ["coalesce", ["get", "e2025"], -1], 0.5], "#2563eb",
        [">=", ["coalesce", ["get", "e2025"], -1], 0.2], "#60a5fa",
        ["==", ["get", "class_nm"], "논"], "#f5d90a",
        "#4ade80"],
      "fill-opacity": 0.55,
    },
  });
  map.addLayer({
    id: "parcel-line", type: "line", source: "parcels",
    paint: { "line-color": "#ffffff", "line-width": 0.6, "line-opacity": 0.55 },
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
async function maybeLoadParcels() {
  const z = map.getZoom();
  const on = z >= PARCEL_ZOOM;
  map.setLayoutProperty("parcel-fill", "visibility", on ? "visible" : "none");
  map.setLayoutProperty("parcel-line", "visibility", on ? "visible" : "none");
  // 필지 축척에서는 래스터 오버레이를 끈다. 20m 격자를 확대하면 뭉개져 필지를 가리고,
  // 이미 필지 색이 같은 사건의 침수율을 담고 있다.
  if (map.getLayer("overlay-layer")) {
    map.setLayoutProperty("overlay-layer", "visibility", on ? "none" : "visible");
  }
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
    const fc = await fetch(`${DATA}parcels/${hit.emd_cd}.json`).then((r) => r.json());
    map.getSource("parcels").setData(fc);
    loadedEmd = hit.emd_cd;
    el("zoom-hint").textContent = `${hit.sgg_nm} ${hit.emd_nm} · 필지 ${fc.features.length.toLocaleString()}개`;
  } catch {
    el("zoom-hint").textContent = "해당 읍면동의 필지 자료가 없습니다";
  }
}

function renderStats() {
  el("stats").innerHTML = `
    <div><b>${stats.n_passes}</b><span>누적 관측</span></div>
    <div><b>${stats.n_storms}</b><span>호우 사건</span></div>
    <div class="hl"><b>${stats.pct_grade_a}%</b><span>적기 관측률</span></div>
    <div><b>${stats.n_missed}</b><span>미관측 사건</span></div>`;
  el("archive-hint").textContent = `분석 기간 ${stats.period} · 최종 갱신 ${stats.generated_at}`;
}

// 관측 대기 중인 사건 = 지금 이 순간 행정이 답을 기다리는 사건
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

async function setOverlay(id) {
  if (map.getLayer("overlay-layer")) map.removeLayer("overlay-layer");
  if (map.getSource("overlay")) map.removeSource("overlay");
  if (!id) { el("overlay-state").textContent = "· 해당 사건은 판독 지도가 생성되지 않았습니다"; return; }
  el("overlay-state").textContent = "";
  const bounds = await fetch(`${DATA}overlays/${id}.json`).then((r) => r.json()).catch(() => null);
  if (!bounds) return;
  map.addSource("overlay", { type: "image", url: `${DATA}overlays/${id}.png`, coordinates: bounds.coordinates });
  map.addLayer({ id: "overlay-layer", type: "raster", source: "overlay", paint: { "raster-opacity": 0.95 } }, "sgg-line");
}

const stat = (label, value) => `<div class="stat"><span>${label}</span><b>${value}</b></div>`;

function selectStorm(id) {
  activeStorm = storms.find((s) => s.storm_id === id);
  document.querySelectorAll("li.event").forEach((li) => li.classList.toggle("active", li.dataset.id === id));

  const s = activeStorm;
  const ev = overlayFor(s);
  el("detail-title").textContent = `${s.peak_date} 호우`;

  let html = stat("사건 기간", `${s.start} ~ ${s.end}`);
  html += stat("최대 일강수량 (도 평균)", `${s.peak_mm} mm`);
  html += stat("사건 누적 강수량", `${s.total_mm} mm`);
  if (s.observed) {
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
  html += `<div class="reason">${s.reason}</div>`;
  if (!ev && s.observed) {
    html += `<p class="hint">해당 사건의 판독 지도는 생성되지 않았습니다.
      관측 이력과 등급은 전 사건에 대해 산출되어 있으며, 판독 지도는 선별 사건에 한해 처리되었습니다.</p>`;
  }
  el("detail").innerHTML = html;
  setOverlay(ev ? ev.id : null);
}

function showEmd(p) {
  el("detail-title").textContent = `${p.sgg_nm} ${p.emd_nm}`;
  el("detail").innerHTML =
    stat("농경지 면적", `${p.area_km2} km²`) +
    stat("필지 수", Number(p.parcels).toLocaleString()) +
    stat("다년 침수 빈도", pct(p.wet_freq)) +
    stat("도내 순위", `${p.rank} / ${emdIndex.length || "-"}`) +
    `<p class="hint">강우 관측 15회에서 해당 읍면동 농경지가 침수 후보로 판정된 평균 비율입니다.
     축척을 확대하면 개별 필지를 확인할 수 있습니다.</p>`;
}

function showParcel(p) {
  el("detail-title").textContent = `필지 ${p.farmmap_id}`;
  let html = stat("소재지", `${p.sgg_nm} ${p.emd_nm}`);
  html += stat("농경지 구분", p.class_nm);
  html += stat("면적", `${Number(p.area_m2).toLocaleString()} m²`);
  html += stat("다년 침수 빈도", pct(p.wet_freq));
  html += stat("관측 횟수", `${p.wet_n_obs ?? "-"} 회`);
  html += `<div class="sep">사건별 침수율</div>`;
  html += stat("2025-07-19 (지연 40시간)", pct(p.e2025));
  html += stat("2025-07-24 (지연 172시간)", pct(p.e2025late));
  html += stat("2023-07-23 (지연 7시간)", pct(p.e2023));
  html += `<p class="hint">침수율은 필지 내에서 이중반사 지표(z&gt;2)로 판정된 픽셀의 비율입니다.
    판독 단위가 20m 격자이므로 면적이 작은 필지는 표본이 부족할 수 있습니다.</p>`;
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

// 배경지도 전환 — 위성 타일이 안 뜰 때 단색으로 넘어가 데모를 이어간다
el("basemap-switch").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-base]");
  if (!btn) return;
  const sat = btn.dataset.base === "sat";
  document.querySelectorAll("#basemap-switch button").forEach((b) => b.classList.toggle("on", b === btn));
  if (map.getLayer("sat")) map.setLayoutProperty("sat", "visibility", sat ? "visible" : "none");
  if (map.getLayer("sgg-fill")) map.setLayoutProperty("sgg-fill", "visibility", sat ? "none" : "visible");
  // 위성 위에서는 시군 경계선을 밝게, 단색 배경에서는 원래대로
  if (map.getLayer("sgg-line")) {
    map.setPaintProperty("sgg-line", "line-color", sat ? "#ffffff" : "#6b8bb5");
    map.setPaintProperty("sgg-line", "line-width", sat ? 1.2 : 0.8);
  }
});

document.querySelector(".compare").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-overlay]");
  if (btn) setOverlay(btn.dataset.overlay);
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
