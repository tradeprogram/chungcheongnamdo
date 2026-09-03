// 물길잡이.agent — 화면 상태를 읽어 질문하고, 답변의 근거와 다음 조작을 함께 보여준다.
//
// app.js 와 같은 고전 스크립트다. app.js 가 먼저 로드되므로 그쪽의 최상위 바인딩
// (map, storms, events, selectStorm, setOverlay, setBasemap, emdIndex ...)을 그대로 쓴다.
//
// 답변 자체는 서버(/ai/chat)가 만든다. 서버가 없거나 실패해도 화면이 멈추지 않도록
// 여기서도 최소한의 안내를 낸다 — 발표 중 외부 호출 실패를 화면에 전가하지 않는다.

const AGENT_ENDPOINT = "/ai/chat";
const AGENT_STREAM = "/ai/chat/stream";
// SSE 는 이벤트를 빈 줄로 끊는다 (LF 두 개).
const SSE_SEP = String.fromCharCode(10, 10);
// 빈 줄로 문단을 나눈다.
const PARA_SPLIT = /\n{2,}/;

let agentTimer = null;
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
  // 답변이 완성되기를 기다렸다가 한 번에 붙이면 20~30초 동안 화면이 비어 있다.
  // 서버가 조각으로 보내는 대로 이어 붙여, 첫 문장이 몇 초 만에 읽히게 한다.
  const view = beginAssistantMessage();
  let got = false;
  try {
    const res = await fetch(AGENT_STREAM, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        context: agentContext(),
        history: agentHistory.slice(-8),
      }),
    });
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let done = null;
    for (;;) {
      const { value, done: finished } = await reader.read();
      if (finished) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE 는 빈 줄로 이벤트를 끊는다. 마지막 조각은 아직 안 끝났을 수 있다.
      const blocks = buffer.split(SSE_SEP);
      buffer = blocks.pop();
      for (const block of blocks) {
        const ev = /^event:\s*(.+)$/m.exec(block);
        const dt = /^data:\s*([\s\S]+)$/m.exec(block);
        if (!ev || !dt) continue;
        let data;
        try { data = JSON.parse(dt[1]); } catch { continue; }
        if (ev[1] === "meta") view.setMeta(data);
        else if (ev[1] === "text") { got = true; view.append(data.t); }
        else if (ev[1] === "reset") { got = false; view.reset(); }
        else if (ev[1] === "done") done = data;
      }
    }
    if (!got) throw new Error("빈 응답");
    view.finish();
    agentStatus.textContent =
      done && done.llm === "ok" && done.model ? done.model
      : done && done.llm === "no_key" ? "화면 자료 기반 (LLM 키 없음)"
      : done && done.llm === "error" ? "LLM 실패 · 화면 자료로 답변"
      : "답변 완료";
  } catch (err) {
    view.reset();
    view.append("에이전트 서버에 닿지 못했습니다. python scripts/serve_agent.py 로 실행하면 "
      + "채팅이 붙습니다. 지도와 판독 결과는 서버 없이도 그대로 동작합니다.");
    view.finish();
    agentStatus.textContent = `연결 실패 · ${String(err.message).slice(0, 40)}`;
  } finally {
    clearInterval(agentTimer);
    setAgentBusy(false);
  }
}

// 비어 있는 답변 말풍선을 먼저 만들고, 도착하는 대로 채운다.
function beginAssistantMessage() {
  const wrap = agentEl("div", { class: "agent-msg agent-assistant agent-typing" });
  const body = agentEl("div", { class: "agent-text" });
  const extra = agentEl("div");
  wrap.appendChild(body);
  wrap.appendChild(extra);
  agentBody.appendChild(wrap);
  let text = "";
  let meta = {};
  const render = () => {
    body.innerHTML = "";
    for (const para of text.split(PARA_SPLIT)) {
      if (para.trim()) body.appendChild(agentEl("p", { text: para.trim() }));
    }
    // 새 글자가 보이도록 따라 내려간다. 사용자가 위로 올렸으면 방해하지 않는다.
    const near = agentBody.scrollHeight - agentBody.scrollTop - agentBody.clientHeight < 80;
    if (near) agentBody.scrollTop = agentBody.scrollHeight;
  };
  return {
    append(t) { text += t; render(); },
    reset() { text = ""; render(); },
    setMeta(m) { meta = m; },
    finish() {
      wrap.classList.remove("agent-typing");
      agentHistory.push({ role: "assistant", text });
      renderMessageExtras(extra, meta);
      agentBody.scrollTop = agentBody.scrollHeight;
    },
  };
}

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

  renderMessageExtras(wrap, meta);
  agentBody.appendChild(wrap);
  agentBody.scrollTop = agentBody.scrollHeight;
}

function renderMessageExtras(wrap, meta = {}) {
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
}

initAgent();
