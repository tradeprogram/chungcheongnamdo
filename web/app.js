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
  observed_good: { text: "확인됨", cls: "A" },
  observed_late: { text: "늦은 관측", cls: "B" },
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
    map.on("click", "emd-fill", (e) => showEmd(e.features[0].properties));
  }

  map.addSource("sgg", { type: "geojson", data: sgg });
  map.addLayer({ id: "sgg-fill", type: "fill", source: "sgg", paint: { "fill-color": "#1b2a3c", "fill-opacity": 0.55 } });
  map.addLayer({ id: "sgg-line", type: "line", source: "sgg", paint: { "line-color": "#6b8bb5", "line-width": 0.8 } });
  addLabels(sgg);

  // 필지 레이어 — 데이터는 확대 시 채워 넣는다
  map.addSource("parcels", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: "parcel-fill", type: "fill", source: "parcels",
    paint: {
      "fill-color": ["case",
        [">=", ["coalesce", ["get", "e2025"], -1], 0.5], "#2563eb",
        [">=", ["coalesce", ["get", "e2025"], -1], 0.2], "#3b82f6",
        ["==", ["get", "class_nm"], "논"], "#2f4a63",
        "#3a4a3a"],
      "fill-opacity": 0.75,
    },
  });
  map.addLayer({
    id: "parcel-line", type: "line", source: "parcels",
    paint: { "line-color": "#0b1218", "line-width": 0.3 },
  });
  map.on("click", "parcel-fill", (e) => showParcel(e.features[0].properties));
  map.on("mouseenter", "parcel-fill", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "parcel-fill", () => (map.getCanvas().style.cursor = ""));
  map.on("moveend", maybeLoadParcels);

  renderStats();
  renderLive();
  renderStorms();
  renderCompare();
  renderEmdRanking(emd);

  const first = storms.find((x) => overlayFor(x)) || storms[0];
  selectStorm(first.storm_id);
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
  if (!on) { el("zoom-hint").textContent = "확대하면 필지 단위로 볼 수 있습니다"; return; }

  const c = map.getCenter();
  const hit = map.queryRenderedFeatures(map.project(c), { layers: ["emd-fill"] })[0]
    || queryEmdAt(c);
  if (!hit) { el("zoom-hint").textContent = "이 위치의 필지 데이터가 없습니다"; return; }
  const code = hit.properties ? hit.properties.emd_cd : hit.emd_cd;
  if (code === loadedEmd) return;

  el("zoom-hint").textContent = "필지 불러오는 중…";
  try {
    const fc = await fetch(`${DATA}parcels/${code}.json`).then((r) => r.json());
    map.getSource("parcels").setData(fc);
    loadedEmd = code;
    el("zoom-hint").textContent = `${fc.features[0]?.properties.emd_nm ?? code} · 필지 ${fc.features.length.toLocaleString()}개`;
  } catch {
    el("zoom-hint").textContent = "이 읍면동의 필지 파일이 없습니다";
  }
}

function queryEmdAt(lnglat) {
  const src = map.getSource("emd");
  if (!src || !src._data) return null;
  return null; // emd-fill 이 렌더되지 않은 경우는 건너뛴다
}

function renderStats() {
  el("stats").innerHTML = `
    <div><b>${stats.n_passes}</b><span>관측 이력</span></div>
    <div><b>${stats.n_storms}</b><span>호우 사건</span></div>
    <div class="hl"><b>${stats.pct_grade_a}%</b><span>제때 확인 가능</span></div>
    <div><b>${stats.n_missed}</b><span>미관측</span></div>`;
  el("archive-hint").textContent = `${stats.period} · 갱신 ${stats.generated_at}`;
}

// 관측 대기 중인 사건 = 지금 이 순간 행정이 답을 기다리는 사건
function renderLive() {
  const pending = storms.filter((s) => s.status === "pending");
  if (!pending.length) { el("live").style.display = "none"; return; }
  const s = pending[0];
  el("live").innerHTML = `
    <div class="live-tag">관측 대기</div>
    <div class="live-title">${s.peak_date} 호우 · 도평균 ${s.peak_mm}mm</div>
    <div class="live-body">
      3일 누적 ${s.total_mm}mm. 아직 충남 전체를 덮은 관측이 없습니다.<br>
      다음 통과 <b>${s.next_pass_kst ?? "미정"}</b>
      ${s.next_pass_lag_hours ? `(peak +${Math.round(s.next_pass_lag_hours / 24)}일)` : ""}
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
        <div class="event-meta">peak ${s.peak_mm}mm · 누적 ${s.total_mm}mm${
        s.lag_hours ? ` · 관측 +${Math.round(s.lag_hours / 24)}일` : ""}</div>
      </li>`;
    })
    .join("");
  document.querySelectorAll("li.event").forEach((li) =>
    li.addEventListener("click", () => selectStorm(li.dataset.id))
  );
}

function renderEmdRanking(emd) {
  if (!emd) { el("emd-list").innerHTML = "<li>읍면동 데이터 없음</li>"; return; }
  const rows = emd.features
    .map((f) => f.properties)
    .sort((a, b) => b.wet_freq - a.wet_freq);
  el("emd-list").innerHTML = rows
    .map((p, i) => `<li class="emd-row" data-cd="${p.emd_cd}">
        <span class="rank">${i + 1}</span>
        <span class="emd-name">${p.sgg_nm} ${p.emd_nm}</span>
        <span class="emd-val">${pct(p.wet_freq)}</span>
      </li>`)
    .join("");
  document.querySelectorAll(".emd-row").forEach((li) =>
    li.addEventListener("click", () => {
      const f = emd.features.find((x) => x.properties.emd_cd === li.dataset.cd);
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
  if (!id) { el("overlay-state").textContent = "· 이 사건은 판독 지도가 없습니다"; return; }
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
  html += stat("peak 일강수 (도평균)", `${s.peak_mm} mm`);
  html += stat("사건 누적", `${s.total_mm} mm`);
  if (s.observed) {
    html += stat("관측 시각", s.obs_kst);
    html += stat("지연", `+${s.lag_hours} 시간 (${Math.round(s.lag_hours / 24)}일)`);
    html += stat("궤도", `orbit ${s.rel_orbit}`);
    html += stat("이 궤도의 촬영률", `${Math.round(s.acquisition_reliability * 100)}%`);
  } else if (s.status === "pending") {
    html += stat("다음 통과", s.next_pass_kst ?? "미정");
    if (s.next_pass_lag_hours) html += stat("그때의 지연", `+${Math.round(s.next_pass_lag_hours / 24)}일`);
  }
  if (ev) {
    html += stat("논 침수 후보 필지", `${ev.paddy_pct} %`);
    html += stat("밭 침수 후보 필지", `${ev.upland_pct} %`);
  }
  html += `<div class="reason">${s.reason}</div>`;
  if (!ev && s.observed) {
    html += `<p class="hint">이 사건의 판독 지도는 아직 생성하지 않았습니다.
      관측 이력과 등급은 전량 산출돼 있고, 지도는 선별 사건만 처리한 상태입니다.</p>`;
  }
  el("detail").innerHTML = html;
  setOverlay(ev ? ev.id : null);
}

function showEmd(p) {
  el("detail-title").textContent = `${p.sgg_nm} ${p.emd_nm}`;
  el("detail").innerHTML =
    stat("농경지 면적", `${p.area_km2} km²`) +
    stat("필지 수", Number(p.parcels).toLocaleString()) +
    stat("다년 침수빈도", pct(p.wet_freq)) +
    stat("도내 순위", `${p.rank} / ${emdIndex.length || "-"}`) +
    `<p class="hint">젖은 관측 15건에서 이 읍면동 농경지가 침수 후보로 판정된 평균 비율입니다.
     확대하면 필지 하나하나를 볼 수 있습니다.</p>`;
}

function showParcel(p) {
  el("detail-title").textContent = `필지 ${p.farmmap_id}`;
  let html = stat("소재", `${p.sgg_nm} ${p.emd_nm}`);
  html += stat("농경지 구분", p.class_nm);
  html += stat("면적", `${Number(p.area_m2).toLocaleString()} m²`);
  html += stat("다년 침수빈도", pct(p.wet_freq));
  html += stat("관측 횟수", `${p.wet_n_obs ?? "-"} 회`);
  html += `<div class="sep">사건별 침수율</div>`;
  html += stat("2025-07-19 (peak +40h)", pct(p.e2025));
  html += stat("2025-07-24 (peak +172h)", pct(p.e2025late));
  html += stat("2023-07-23 (peak +7h)", pct(p.e2023));
  html += `<p class="hint">침수율은 필지 안에서 이중반사(z&gt;2)로 판정된 픽셀의 비율입니다.
    20m 격자라 작은 필지는 표본이 적습니다.</p>`;
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
    bar(a, "07-19 관측 (+40h)") + bar(b, "07-24 관측 (+172h)") +
    `<p class="hint">같은 호우인데 논 침수 후보 비율이 ${(a.paddy_pct / b.paddy_pct).toFixed(0)}배 차이납니다.</p>`;
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

document.querySelector(".compare").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-overlay]");
  if (btn) setOverlay(btn.dataset.overlay);
});

boot();
