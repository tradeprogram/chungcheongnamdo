// 충남 농업재해 위성관측 지원 — 데모용 정적 뷰어.
// 백엔드가 없다. web/data 의 사전 생성 파일만 읽는다 (발표 중 API 실패 방지).

const DATA = "data/";
let events = [];
let activeId = null;

const el = (id) => document.getElementById(id);

const map = new maplibregl.Map({
  container: "map",
  style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#0b1218" } }] },
  // 도 전체(zoom 7.6)에서는 침수 후보 픽셀이 화면 1px 미만이라 보이지 않는다.
  // 패턴이 드러나는 축척에서 시작한다.
  center: [126.88, 36.35],
  zoom: 9.2,
  attributionControl: false,
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

async function boot() {
  const [evts, upcoming, sgg] = await Promise.all([
    fetch(DATA + "events.json").then((r) => r.json()),
    fetch(DATA + "upcoming.json").then((r) => r.json()),
    fetch(DATA + "sgg.geojson").then((r) => r.json()),
  ]);
  events = evts;

  await new Promise((res) => (map.loaded() ? res() : map.on("load", res)));

  map.addSource("sgg", { type: "geojson", data: sgg });
  map.addLayer({ id: "sgg-fill", type: "fill", source: "sgg", paint: { "fill-color": "#1b2a3c", "fill-opacity": 0.55 } });
  map.addLayer({ id: "sgg-line", type: "line", source: "sgg", paint: { "line-color": "#6b8bb5", "line-width": 0.8 } });
  // 시군명은 symbol 레이어 대신 HTML 마커로 그린다.
  // symbol 의 text-field 는 스타일에 glyphs(폰트) URL 을 요구하는데,
  // 외부 폰트 서버에 의존하면 오프라인 데모가 깨진다.
  addLabels(sgg);

  renderEvents();
  renderUpcoming(upcoming);
  select(events.find((e) => e.grade === "A")?.id || events[0].id);
}

function gradeBadge(e) {
  const g = e.grade || "none";
  const text = e.grade ? "등급 " + e.grade : "사건 아님";
  return `<span class="grade ${g}">${text}</span>`;
}

function renderEvents() {
  el("event-list").innerHTML = events
    .map(
      (e) => `<li class="event" data-id="${e.id}">
        <div class="event-top"><span class="event-label">${e.label}</span>${gradeBadge(e)}</div>
        <div class="event-meta">${e.observed_kst} · orbit ${e.rel_orbit} · 논 ${e.paddy_pct}%</div>
      </li>`
    )
    .join("");
  document.querySelectorAll("li.event").forEach((li) =>
    li.addEventListener("click", () => select(li.dataset.id))
  );
}

function renderUpcoming(list) {
  el("upcoming-list").innerHTML = list.length
    ? list
        .map(
          (p) => `<li><b>${p.when}</b> KST · orbit ${p.orbit}
            <br>촬영 확률 ${Math.round(p.reliability * 100)}%</li>`
        )
        .join("")
    : "<li>예정 통과 없음</li>";
}

async function setOverlay(id) {
  ["overlay-layer"].forEach((l) => { if (map.getLayer(l)) map.removeLayer(l); });
  if (map.getSource("overlay")) map.removeSource("overlay");

  const bounds = await fetch(`${DATA}overlays/${id}.json`).then((r) => r.json()).catch(() => null);
  if (!bounds) return;

  map.addSource("overlay", {
    type: "image",
    url: `${DATA}overlays/${id}.png`,
    coordinates: bounds.coordinates,
  });
  map.addLayer({ id: "overlay-layer", type: "raster", source: "overlay", paint: { "raster-opacity": 0.95 } }, "sgg-line");
}

// 폴리곤 bbox 중심에 시군명 라벨을 붙인다.
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

function stat(label, value) {
  return `<div class="stat"><span>${label}</span><b>${value}</b></div>`;
}

function renderDetail(e) {
  el("detail-title").textContent = e.label;
  let html = "";
  if (e.peak_kst) {
    html += stat("호우 peak", e.peak_kst);
    html += stat("관측 시각", e.observed_kst);
    html += stat("지연", `+${e.lag_hours} 시간`);
  } else {
    html += stat("관측 시각", e.observed_kst);
  }
  html += stat("궤도", `orbit ${e.rel_orbit}`);
  html += stat("이 궤도의 여름 촬영률", `${Math.round(e.acquisition_reliability * 100)}%`);
  if (e.rain3d_mm !== null) html += stat("선행 3일 강우", `${e.rain3d_mm} mm`);
  html += stat("논 침수 후보 필지", `${e.paddy_pct} %`);
  html += stat("밭 침수 후보 필지", `${e.upland_pct} %`);
  if (e.grade_reason) html += `<div class="reason">${e.grade_reason}</div>`;
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
    `<p class="hint">같은 호우인데 논 침수 후보 비율이 ${(a.paddy_pct / b.paddy_pct).toFixed(0)}배 차이납니다.
     위성이 언제 지나갔는지가 판별 가능성을 가릅니다.</p>`;
}

function select(id) {
  activeId = id;
  document.querySelectorAll("li.event").forEach((li) =>
    li.classList.toggle("active", li.dataset.id === id)
  );
  const e = events.find((x) => x.id === id);
  renderDetail(e);
  setOverlay(id);
}

document.getElementById("compare").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-event]");
  if (btn) select(btn.dataset.event);
});

boot().then(renderCompare);
