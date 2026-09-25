/* Rule mesh console — front end.
 *
 * The page is a pure viewer: it opens one Server-Sent Events stream per run and
 * paints whatever the engine emits. It never decides anything itself, which is
 * the point of the whole project.
 */

const $ = (id) => document.getElementById(id);

const state = {
  rules: [],
  backend: "jev",
  stream: null, // the EventSource of the run currently streaming
  layers: new Map(), // layer index -> card element
  raw: [],
};

/* ── tiny DOM helpers ─────────────────────────────────────────────────── */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function fill(node, text) {
  node.textContent = "";
  if (text) node.append(...text);
  return node;
}

const TYPE_LABEL = { choice: "choice", score: "score 0–N", noul: "yes / no" };

/* ── rule list ────────────────────────────────────────────────────────── */
async function loadRules() {
  const data = await (await fetch("/api/rules")).json();
  state.rules = data.rules;
  const select = $("rule");
  select.innerHTML = "";
  for (const rule of data.rules) {
    const option = el("option", null, rule.broken ? `${rule.stem}  (broken)` : `${rule.stem} — ${rule.title}`);
    option.value = rule.stem;
    select.append(option);
  }
  const wanted = new URLSearchParams(location.search).get("rule");
  if (wanted && data.rules.some((r) => r.stem === wanted)) select.value = wanted;
  describeRule();
}

function currentRule() {
  return state.rules.find((r) => r.stem === $("rule").value) || {};
}

function describeRule() {
  const rule = currentRule();
  const bits = rule.broken
    ? [`does not parse: ${rule.error}`]
    : [
        rule.title,
        `${rule.layers} layers`,
        `${rule.endings} endings`,
        `${rule.questions} model questions`,
        `suggests ${rule.backend.toUpperCase()}`,
      ];
  $("ruleMeta").textContent = bits.filter(Boolean).join(" · ");
  if (rule.about) $("ruleMeta").textContent += `\n${rule.about}`;

  const chips = $("examples");
  chips.innerHTML = "";
  for (const seed of rule.examples || []) {
    const button = el("button", null, seed.length > 64 ? seed.slice(0, 61) + "…" : seed);
    button.title = seed;
    button.onclick = () => ($("seed").value = seed);
    chips.append(button);
  }
  chips.classList.toggle("muted", !(rule.examples || []).length);
  if (!$("seed").value && (rule.examples || []).length) $("seed").value = rule.examples[0];

  // A stored choice wins; otherwise follow the rule's suggestion. You can
  // always override it with the toggle, which is the point of the switch.
  const stored = localStorage.getItem("rule-mesh-backend");
  if (stored) state.userPickedBackend = true;
  setBackend(stored || rule.backend || "jev");
}

function setBackend(kind) {
  state.backend = kind;
  for (const button of $("backend").children) button.classList.toggle("on", button.dataset.kind === kind);
  $("backendMeta").textContent =
    kind === "laya"
      ? "local energy model — first call downloads weights"
      : "remote energy model — needs OPENROUTER_API_KEY in .env.local";
}

/* ── source editor ───────────────────────────────────────────────────── */
async function loadSource() {
  const stem = $("rule").value;
  const data = await (await fetch(`/api/rules/${stem}`)).json();
  $("src").value = data.text;
  $("saveMeta").textContent = `loaded rules/${stem}.rule`;
}

async function checkSource() {
  const stem = $("rule").value;
  const response = await fetch(`/api/rules/${stem}/check`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text: $("src").value }),
  });
  const data = await response.json();
  $("saveMeta").textContent = data.ok
    ? `parses · ${data.layers} layers · ${data.endings} endings · not saved yet`
    : `parse error: ${data.error}`;
}

async function saveSource() {
  const stem = $("rule").value;
  const response = await fetch(`/api/rules/${stem}`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text: $("src").value }),
  });
  if (!response.ok) {
    const problem = await response.json();
    $("saveMeta").textContent = `rejected: ${problem.detail}`;
    return;
  }
  $("saveMeta").textContent = `saved rules/${stem}.rule`;
  await loadRules();
  $("rule").value = stem;
  describeRule();
}

/* ── rendering the trace ──────────────────────────────────────────────── */
function resetTrace() {
  state.layers.clear();
  state.raw = [];
  $("trace").innerHTML = "";
  $("trace").append(el("div", "empty", "Waiting for the first layer…"));
  $("ledger").textContent = "—";
  $("actions").innerHTML = "";
  $("actions").append(el("li", "muted", "none yet"));
  $("stats").innerHTML = "";
  $("raw").textContent = "";
}

function cardFor(event) {
  const existing = state.layers.get(event.layer);
  if (existing) return existing;

  const card = el("div", "layer");
  const head = el("div", "head");
  head.append(el("span", "lvl", `L${event.layer}`), el("span", "node", event.node));
  if (event.terminal) head.append(el("span", "term", "terminal"));
  const body = el("div", "body");
  card.append(head, body);
  $("trace").querySelector(".empty")?.remove();
  $("trace").append(card);
  state.layers.set(event.layer, card);
  return card;
}

function renderLayer(event) {
  const card = cardFor(event);
  const body = card.querySelector(".body");
  body.innerHTML = "";

  if (event.ask) body.append(el("div", "ask", event.ask));
  if (event.note) body.append(el("div", "note", event.note));

  const pills = el("div", "pills");
  for (const effect of event.effects || []) {
    pills.append(el("span", "pill effect", `! ${effect.action}${effect.args.length ? " " + effect.args.join(" ") : ""}`));
  }
  for (const signal of event.signals || []) {
    pills.append(el("span", "pill", `${signal.name} · ${TYPE_LABEL[signal.type] || signal.type}`));
  }
  if (pills.children.length) body.append(pills);

  body.append(el("div", "note", "asking the model…"));
}

function renderDecision(event) {
  const card = cardFor(event);
  const body = card.querySelector(".body");
  body.innerHTML = "";

  if (event.ask) body.append(el("div", "ask", event.ask));

  const chose = el("div", "chose");
  chose.append(el("span", "k", event.chosen));
  chose.append(el("span", "s", event.semantics || ""));
  chose.append(el("span", "c", `confidence ${Number(event.confidence).toFixed(3)}`));
  body.append(chose);

  body.append(barChart(event.probabilities, event.chosen));

  const signals = (event.signals || []).filter((s) => s.value !== undefined);
  if (signals.length) body.append(signalBlock(signals));

  const route = el("div", "route");
  const verb = event.routing === "action" ? "calls" : "goes to";
  route.append(document.createTextNode(`${verb} `), el("b", null, event.routing === "action" ? event.target : `${event.target}`));
  if (event.routing === "action" && event.args.length) {
    route.append(document.createTextNode(`  →  ${event.args.join(" · ")}`));
  }
  body.append(route);
}

function barChart(probabilities, chosen) {
  const wrap = el("div", "bars");
  const entries = Object.entries(probabilities || {}).sort((a, b) => b[1] - a[1]);
  for (const [key, value] of entries) {
    const win = key === chosen;
    const row = el("div", "pbar" + (win ? " win" : ""));
    row.append(el("span", "mark", win ? ">" : ""));
    row.append(el("span", "name", key));
    const track = el("div", "track");
    const fillBar = el("div", "fill");
    fillBar.style.width = `${Math.max(0, Math.min(1, value)) * 100}%`;
    track.append(fillBar);
    row.append(track);
    row.append(el("span", "num", Number(value).toFixed(3)));
    wrap.append(row);
  }
  return wrap;
}

/* A score reads as a band, a noul reads as a lean, a choice reads as a word. */
function describeSignal(signal) {
  const value = signal.value;
  if (signal.type === "score") {
    const bands = signal.criteria || [];
    const index = Math.max(0, Math.min(bands.length - 1, Math.round(value)));
    return { shown: Number(value).toFixed(3), gloss: bands[index] ? `→ ${bands[index]}` : "" };
  }
  if (signal.type === "noul") {
    const lean = Number(value) < 0.5 ? "no" : "yes";
    const labels = { no: (signal.criteria || {}).false, yes: (signal.criteria || {}).true };
    return { shown: Number(value).toFixed(3), gloss: `→ leans ${lean}${labels[lean] ? ` (${labels[lean]})` : ""}` };
  }
  const label = (signal.criteria || {})[value];
  return { shown: String(value), gloss: label ? `→ ${label}` : "" };
}

function signalBlock(signals) {
  const wrap = el("div", "signals");
  for (const signal of signals) {
    const info = describeSignal(signal);
    const row = el("div", "sig");
    row.append(el("span", "lbl", `${signal.name}`));
    const value = el("span", "val");
    value.append(el("b", null, info.shown));
    if (info.gloss) value.append(document.createTextNode(`  ${info.gloss}`));
    row.append(value);
    wrap.append(row);
  }
  return wrap;
}

function renderAction(event) {
  const list = $("actions");
  list.querySelector(".muted")?.remove();
  const item = el("li", event.status || "");
  item.append(el("span", "a", `${event.at === "final" ? "final " : ""}${event.action}`));
  if (event.args && event.args.length) item.append(document.createTextNode(` ${event.args.join(" ")}`));
  item.append(el("span", "d", `  [${event.status}]  ${event.detail || ""}`));
  list.append(item);
}

function renderEndLayer(event) {
  const card = state.layers.get(event.layer);
  if (card && event.note) card.querySelector(".body").append(el("div", "note", `⏹ ${event.note}`));
}

function renderEnd(event) {
  const banner = el("div", "banner " + event.status);
  banner.textContent =
    `${event.status.toUpperCase()} · ${event.layers} layer${event.layers === 1 ? "" : "s"} · model ${event.model || "n/a"}` +
    (event.usage && Object.keys(event.usage).length ? ` · ${JSON.stringify(event.usage)}` : "");
  $("trace").prepend(banner);
  if (event.state) $("ledger").textContent = event.state;
  if (event.actions) {
    $("actions").innerHTML = "";
    for (const action of event.actions) renderAction(action);
  }
  $("stats").innerHTML = `run <b>${event.run || ""}</b><br>${event.status} · ${event.layers} layers`;
}

function renderError(event) {
  const banner = el("div", "banner failed");
  banner.textContent = event.message;
  $("trace").prepend(banner);
}

function renderStart(event) {
  $("stats").innerHTML =
    `flow <b>${event.flow}</b><br>backend <b>${event.backend}</b><br>run <b>${event.run}</b>`;
}

/* ── running ──────────────────────────────────────────────────────────── */
const HANDLERS = {
  start: renderStart,
  layer: renderLayer,
  decision: renderDecision,
  action: renderAction,
  "end-layer": renderEndLayer,
  end: renderEnd,
  error: renderError,
  plugins: (event) => $("stats").append(el("div", null, event.detail)),
};

function dispatch(event) {
  if ($("rawEvents").checked) {
    state.raw.push(event);
    $("raw").textContent = state.raw.map((e) => JSON.stringify(e)).join("\n");
    $("raw").scrollTop = $("raw").scrollHeight;
  }
  const handler = HANDLERS[event.kind];
  if (handler) handler(event);
}

async function run() {
  if (state.stream) state.stream.close();
  resetTrace();
  $("run").disabled = true;
  $("run").textContent = "running…";

  const body = {
    rule: $("rule").value,
    backend: state.backend,
    seed: $("seed").value,
    live: $("live").checked,
  };
  const started = await (await fetch("/api/run", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  })).json();

  const stream = new EventSource(`/api/runs/${started.run}`);
  state.stream = stream;

  for (const kind of ["start", "layer", "decision", "action", "end-layer", "end", "error", "plugins"]) {
    stream.addEventListener(kind, (message) => dispatch(JSON.parse(message.data)));
  }
  stream.addEventListener("closed", () => {
    stream.close();
    state.stream = null;
    $("run").disabled = false;
    $("run").textContent = "Run the mesh";
  });
  stream.onerror = () => {
    stream.close();
    state.stream = null;
    $("run").disabled = false;
    $("run").textContent = "Run the mesh";
  };
}

/* ── wiring ───────────────────────────────────────────────────────────── */
$("rule").onchange = () => { describeRule(); loadSource(); };
$("backend").onclick = (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  state.userPickedBackend = true;
  localStorage.setItem("rule-mesh-backend", button.dataset.kind);
  setBackend(button.dataset.kind);
};
$("run").onclick = run;
$("loadSrc").onclick = loadSource;
$("checkSrc").onclick = checkSource;
$("saveSrc").onclick = saveSource;
$("rawEvents").onchange = () => ($("raw").hidden = !$("rawEvents").checked);

loadRules().then(loadSource);
