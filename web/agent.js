// 물길잡이.agent — 화면 상태를 읽어 질문하고, 답변의 근거와 다음 조작을 함께 보여준다.
//
// app.js 와 같은 고전 스크립트다. app.js 가 먼저 로드되므로 그쪽의 최상위 바인딩
// (map, storms, events, selectStorm, setOverlay, setBasemap, emdIndex ...)을 그대로 쓴다.
//
// 답변 자체는 서버(/ai/chat)가 만든다. 서버가 없거나 실패해도 화면이 멈추지 않도록
// 여기서도 최소한의 안내를 낸다 — 발표 중 외부 호출 실패를 화면에 전가하지 않는다.

const AGENT_ENDPOINT = "/ai/chat";
const AGENT_OPENERS = [
  "이번 호우는 위성으로 확인할 수 있어?",
  "관측 시각이 결과를 얼마나 가르는지 보여줘",
  "반복해서 잠기는 지역이 어디야?",
  "판독 불가 필지는 왜 생겨?",
];

let agentHistory = [];
let agentBody, agentInput, agentSend, agentStatus, agentPanel;

function agentEl(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") node.textContent = v;
    else if (v != null) node.setAttribute(k, v);
  }
  for (const c of children) if (c) node.appendChild(c);
  return node;
}

function initAgent() {
  const launcher = agentEl("button", {
    class: "agent-launcher", type: "button", title: "물길잡이.agent", text: "AI 질의",
  });

  agentPanel = agentEl("aside", { class: "agent-panel", "aria-label": "물길잡이 에이전트" },
    agentEl("div", { class: "agent-head" },
      agentEl("div", {},
        agentEl("strong", { text: "물길잡이.agent" }),
        agentEl("span", { class: "agent-sub", text: "화면 자료를 읽고 근거와 함께 답합니다" })),
      agentEl("button", { class: "agent-close", type: "button", title: "닫기", text: "×" })),
    (agentBody = agentEl("div", { class: "agent-body" })),
    agentEl("form", { class: "agent-form" },
      (agentInput = agentEl("textarea", {
        class: "agent-input", rows: "2",
        placeholder: "예: 2025년 7월 호우는 제때 관측됐어?",
      })),
      agentEl("div", { class: "agent-row" },
        (agentStatus = agentEl("span", { class: "agent-status", text: "대기" })),
        (agentSend = agentEl("button", { class: "agent-send", type: "submit", text: "질문" })))));

  document.body.appendChild(launcher);
  document.body.appendChild(agentPanel);

  launcher.addEventListener("click", () => {
    agentPanel.classList.toggle("open");
    if (agentPanel.classList.contains("open")) agentInput.focus();
  });
  agentPanel.querySelector(".agent-close")
    .addEventListener("click", () => agentPanel.classList.remove("open"));

  const form = agentPanel.querySelector("form");
  form.addEventListener("submit", (e) => { e.preventDefault(); submitAgent(); });
  agentInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });

  addAgentMessage("assistant",
    "지금 화면에 있는 관측 아카이브와 필지 판독 결과를 읽고 답합니다. " +
    "없는 것은 없다고 말합니다.", { suggested: AGENT_OPENERS });
}

// --- 화면 상태 -------------------------------------------------------------

function agentContext() {
  const ctx = { zoom: Math.round(map.getZoom() * 10) / 10 };
  if (typeof activeStorm !== "undefined" && activeStorm) ctx.selected_storm = activeStorm.storm_id;
  if (typeof loadedEmd !== "undefined" && loadedEmd) ctx.selected_emd = loadedEmd;
  if (typeof overlayWanted !== "undefined" && overlayWanted) ctx.selected_event = overlayWanted;
  return ctx;
}

// --- 에이전트가 화면을 움직인다 ---------------------------------------------

function runAgentAction(a) {
  if (a.type === "storm" && a.id) {
    switchTab("storms");
    selectStorm(a.id);
  } else if (a.type === "event" && a.id) {
    setOverlay(a.id);
    setParcelBasis(a.id);
  } else if (a.type === "basemap" && a.kind) {
    setBasemap(a.kind);
  } else if (a.type === "region" && a.emd_cd) {
    const hit = emdIndex.find((e) => e.emd_cd === a.emd_cd);
    if (hit) {
      switchTab("emd");
      // fitBounds 는 카메라 애니메이션이라 스타일 로딩이 끝나기 전에는 조용히 무시된다.
      // 에이전트가 "이동" 이라고 말해 놓고 지도가 그대로면 답변이 거짓말이 되므로,
      // 범위에서 카메라를 직접 계산해 즉시 옮긴다.
      const [w, s2, e2, n] = hit.bbox;
      const cam = map.cameraForBounds([[w, s2], [e2, n]], { padding: 60 });
      map.jumpTo({
        center: cam ? cam.center : [(w + e2) / 2, (s2 + n) / 2],
        zoom: Math.min(cam ? cam.zoom : 13.5, 13.5),
      });
    }
  } else if (a.type === "tab" && a.id) {
    switchTab(a.id);
  }
}

// 탭 전환은 app.js 의 클릭 핸들러에 있다. 같은 경로를 쓰도록 단추를 눌러 준다.
function switchTab(id) {
  const btn = document.querySelector(`#tabs button[data-tab="${id}"]`);
  if (btn && !btn.classList.contains("on")) btn.click();
}

// --- 대화 ------------------------------------------------------------------

async function submitAgent() {
  const text = agentInput.value.trim();
  if (!text) return;
  agentInput.value = "";
  await askAgent(text);
}

async function askAgent(text) {
  addAgentMessage("user", text);
  setAgentBusy(true);
  try {
    const res = await fetch(AGENT_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        context: agentContext(),
        history: agentHistory.slice(-8),
      }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!data.answer) throw new Error(data.detail || data.error || "빈 응답");
    addAgentMessage("assistant", data.answer, {
      actions: data.actions,
      evidence: data.evidence,
      trace: data.tool_trace,
      suggested: data.suggested,
    });
    // 키가 없는 상태를 오류로 적지 않는다. 그건 고장이 아니라 구성이다.
    agentStatus.textContent =
      data.llm === "ok" && data.model ? data.model
      : data.llm === "no_key" ? "화면 자료 기반 (LLM 키 없음)"
      : data.llm === "error" ? `LLM 실패 · 화면 자료로 답변`
      : "화면 자료로 답변";
  } catch (err) {
    addAgentMessage("assistant",
      "에이전트 서버에 닿지 못했습니다. `python scripts/serve_agent.py` 로 실행하면 " +
      "채팅이 붙습니다. 지도와 판독 결과는 서버 없이도 그대로 동작합니다.");
    agentStatus.textContent = `연결 실패 · ${String(err.message).slice(0, 40)}`;
  } finally {
    clearInterval(agentTimer);
    setAgentBusy(false);
  }
}

let agentTimer = null;

// 응답이 20~30초 걸린다. 그동안 한 문구만 떠 있으면 멈춘 것처럼 보이므로
// 무엇을 기다리는지와 경과 시간을 같이 보여준다. 근거 수집은 순식간이고
// 오래 걸리는 쪽은 LLM이라, 그렇게 적어야 사실과 맞다.
function setAgentBusy(busy) {
  agentSend.disabled = busy;
  agentInput.disabled = busy;
  clearInterval(agentTimer);
  if (!busy) return;
  const t0 = Date.now();
  agentStatus.textContent = "근거를 모으는 중";
  agentTimer = setInterval(() => {
    const sec = Math.round((Date.now() - t0) / 1000);
    agentStatus.textContent = sec < 2 ? "근거를 모으는 중" : `답변 작성 중 · ${sec}초`;
  }, 500);
}

function addAgentMessage(role, text, meta = {}) {
  agentHistory.push({ role, text });
  const wrap = agentEl("div", { class: `agent-msg agent-${role}` });
  for (const para of String(text).split(/\n{2,}/)) {
    if (para.trim()) wrap.appendChild(agentEl("p", { text: para.trim() }));
  }

  const evidence = meta.evidence || [];
  if (evidence.length) {
    const box = agentEl("div", { class: "agent-evidence" },
      agentEl("div", { class: "agent-label", text: "근거" }));
    for (const e of evidence.slice(0, 3)) {
      box.appendChild(agentEl("div", { class: "agent-ev" },
        agentEl("b", { text: e.title }),
        agentEl("span", { text: e.summary })));
    }
    wrap.appendChild(box);
  }

  const trace = meta.trace || [];
  if (trace.length) {
    const row = agentEl("div", { class: "agent-trace" });
    for (const t of trace) {
      row.appendChild(agentEl("span", {
        class: `agent-chip agent-${t.status}`,
        text: `${t.tool}${t.status === "missing" ? " · 자료 없음" : ""}`,
      }));
    }
    wrap.appendChild(row);
  }

  const actions = meta.actions || [];
  if (actions.length) {
    const row = agentEl("div", { class: "agent-actions" });
    for (const a of actions) {
      const b = agentEl("button", {
        class: `agent-act${a.primary ? " primary" : ""}`, type: "button", text: a.label,
      });
      b.addEventListener("click", () => runAgentAction(a));
      row.appendChild(b);
    }
    wrap.appendChild(row);
  }

  const suggested = meta.suggested || [];
  if (suggested.length) {
    const row = agentEl("div", { class: "agent-followups" });
    for (const s of suggested.slice(0, 3)) {
      const b = agentEl("button", { class: "agent-follow", type: "button", text: s });
      b.addEventListener("click", () => askAgent(s));
      row.appendChild(b);
    }
    wrap.appendChild(row);
  }

  agentBody.appendChild(wrap);
  agentBody.scrollTop = agentBody.scrollHeight;
}

initAgent();
