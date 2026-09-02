// 충남 농업재해 위성관측 지원 — 정적 뷰어.
// 백엔드 없음. web/data 의 사전 생성 파일만 읽는다 (발표 중 API 실패 방지).

const DATA = "data/";
let storms = [], events = [], stats = null, filter = "all", activeStorm = null;

const el = (id) => document.getElementById(id);
const fmtNum = (n) => (n === null || n === undefined ? "-" : n);

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
// 레이아웃이 나중에 확정되면 캔버스가 몇 px 로 남아 지도가 안 보인다.
// 컨테이너 크기 변화를 관찰해 항상 다시 맞춘다.
new ResizeObserver(() => map.resize()).observe(document.getElementById("map"));

const STATUS = {
  observed_good: { text: "확인됨", cls: "A" },
  observed_late: { text: "늦은 관측", cls: "B" },
  missed: { text: "미관측", cls: "C" },
  pending: { text: "관측 대기", cls: "P" },
};

async function boot() {
  const [s, ev, st, sgg] = await Promise.all([
    fetch(DATA + "storms.json").then((r) => r.json()),
    fetch(DATA + "events.json").then((r) => r.json()),
    fetch(DATA + "stats.json").then((r) => r.json()),
    fetch(DATA + "sgg.geojson").then((r) => r.json()),
  ]);
  storms = s.sort((a, b) => (a.peak_date < b.peak_date ? 1 : -1));
  events = ev;
  stats = st;

  await mapReady;
  map.addSource("sgg", { type: "geojson", data: sgg });
  map.addLayer({ id: "sgg-fill", type: "fill", source: "sgg", paint: { "fill-color": "#1b2a3c", "fill-opacity": 0.55 } });
  map.addLayer({ id: "sgg-line", type: "line", source: "sgg", paint: { "line-color": "#6b8bb5", "line-width": 0.8 } });
  addLabels(sgg);

  renderStats();
  renderLive();
  renderStorms();
  renderCompare();

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

function renderStats() {
  el("stats").innerHTML = `
    <div><b>${stats.n_passes}</b><span>관측 이력</span></div>
    <div><b>${stats.n_storms}</b><span>호우 사건</span></div>
    <div class="hl"><b>${stats.pct_grade_a}%</b><span>제때 확인 가능</span></div>
    <div><b>${stats.n_missed}</b><span>미관측</span></div>`;
  el("archive-hint").textContent =
    `${stats.period} · 갱신 ${stats.generated_at}`;
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
        s.lag_hours ? ` · 관측 +${Math.round(s.lag_hours / 24)}일` : ""
      }</div>
      </li>`;
    })
    .join("");
  document.querySelectorAll("li.event").forEach((li) =>
    li.addEventListener("click", () => selectStorm(li.dataset.id))
  );
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

document.querySelector(".compare").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-overlay]");
  if (btn) setOverlay(btn.dataset.overlay);
});

boot();
