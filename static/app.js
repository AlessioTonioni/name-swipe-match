const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const FLAGS = { CH: "🇨🇭", IT: "🇮🇹", FR: "🇫🇷", DE: "🇩🇪", AT: "🇦🇹", UK: "🇬🇧", ES: "🇪🇸", BE: "🇧🇪", NO: "🇳🇴" };
const DEFAULT_FILTERS = { sex: "m", countries: [], minLen: 2, maxLen: 14, pop: "any" };

let me = null;
let meta = null;
let filters = { ...DEFAULT_FILTERS };
let queue = [];
let busy = false;
let rankMode = "mine";

// ---------- api ----------
async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401) { showLogin(); throw new Error("not logged in"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.add("hidden"), 2200);
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------- per-user saved filters (convenience only) ----------
function loadFilters() {
  try {
    const saved = JSON.parse(localStorage.getItem(`nm-filters-${me.username}`) || "null");
    filters = { ...DEFAULT_FILTERS, ...(saved || {}) };
  } catch { filters = { ...DEFAULT_FILTERS }; }
}
function saveFilters() {
  try { localStorage.setItem(`nm-filters-${me.username}`, JSON.stringify(filters)); } catch {}
}

// ---------- login ----------
function showLogin() {
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
  $("#username").focus();
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#login-error").textContent = "";
  try {
    await api("/api/login", { method: "POST", body: { username: $("#username").value } });
    await start();
  } catch (err) { $("#login-error").textContent = err.message; }
});

$("#logout").addEventListener("click", async (e) => {
  e.preventDefault();
  await api("/api/logout", { method: "POST" });
  queue = [];
  $("#deck").innerHTML = "";
  showLogin();
});

$("#reset-me").addEventListener("click", async (e) => {
  e.preventDefault();
  const m = await api("/api/me");
  $("#reset-user").textContent = m.username;
  $("#reset-counts").textContent = `${m.swiped} swipes (${m.liked} likes)`;
  $("#reset-overlay").classList.remove("hidden");
});
$("#reset-cancel").addEventListener("click", () => $("#reset-overlay").classList.add("hidden"));
$("#reset-overlay").addEventListener("click", (e) => { if (e.target.id === "reset-overlay") $("#reset-overlay").classList.add("hidden"); });
$("#reset-confirm").addEventListener("click", async () => {
  try {
    const res = await api("/api/reset-me", { method: "POST" });
    $("#reset-overlay").classList.add("hidden");
    showTab("swipe");
    await start();
    toast(`Reset: ${res.swipes} swipes and ${res.duels} duels deleted`);
  } catch (err) { toast(err.message); }
});

async function start() {
  me = await api("/api/me");
  meta = meta || await api("/api/meta");
  loadFilters();
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#me-name").textContent = me.username;
  updateBadge(me.new_matches);
  renderFiltersSummary();
  queue = [];
  $("#deck").innerHTML = "";
  await refill();
}

function updateBadge(n) {
  const b = $("#match-badge");
  b.textContent = n;
  b.classList.toggle("hidden", !n);
}

// ---------- tabs ----------
$$("nav.tabs button").forEach((btn) => btn.addEventListener("click", () => showTab(btn.dataset.tab)));

function showTab(tab) {
  $$("nav.tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $$(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== `tab-${tab}`));
  if (tab === "duel") loadDuel();
  if (tab === "ranking") loadRanking();
  if (tab === "matches") loadMatches();
}

// ---------- cards ----------
function popLabel(rank) {
  if (rank == null) return "";
  if (rank <= 20) return "🔥 Very popular";
  if (rank <= 100) return "⭐ Popular";
  if (rank <= 500) return "🌱 Uncommon";
  return "💎 Rare";
}

function flagsHtml(countries, only, limit = 99) {
  return Object.entries(countries)
    .filter(([cc]) => !only || !only.length || only.includes(cc))
    .sort((a, b) => a[1].rank - b[1].rank)
    .slice(0, limit)
    .map(([cc, c]) => `<span class="flag" title="${esc(meta.countries[cc])}: rank ${c.rank}, ${c.per10k} per 10,000 births">${FLAGS[cc] || cc} #${c.rank}</span>`)
    .join("");
}

function sexLabel(sex) { return sex === "m" ? "Boy" : "Girl"; }
function duels(n) { return `${n} duel${n === 1 ? "" : "s"}`; }

function cardEl(n) {
  const el = document.createElement("div");
  el.className = "card";
  el.dataset.id = n.id;
  el.innerHTML = `
    <div class="stamp like">LIKE</div><div class="stamp nope">NOPE</div>
    <button class="info-btn" title="Origin and meaning (i)">i</button>
    <div class="sex ${n.sex}">${sexLabel(n.sex)}</div>
    <div class="name">${esc(n.name)}</div>
    <div class="pop muted">${popLabel(n.best_rank)}</div>
    <div class="flags">${flagsHtml(n.countries, filters.countries)}</div>`;
  return el;
}

function renderDeck() {
  const deck = $("#deck");
  deck.innerHTML = "";
  queue.slice(0, 2).reverse().forEach((n, i, arr) => {
    const el = cardEl(n);
    if (i < arr.length - 1) el.classList.add("behind");
    deck.appendChild(el);
  });
  const top = deck.lastElementChild;
  if (top) attachDrag(top);
  $("#deck-empty").classList.toggle("hidden", queue.length > 0);
  $(".actions").classList.toggle("hidden", queue.length === 0);
}

function deckQuery() {
  const p = new URLSearchParams({
    sex: filters.sex, countries: filters.countries.join(","),
    min_len: filters.minLen, max_len: filters.maxLen, pop: filters.pop, limit: 30,
  });
  return `/api/deck?${p}`;
}

async function refill() {
  const data = await api(deckQuery());
  const have = new Set(queue.map((n) => n.id));
  queue.push(...data.cards.filter((n) => !have.has(n.id)));
  $("#remaining").textContent = `${data.remaining} names left`;
  renderDeck();
}

async function decide(liked) {
  if (busy || !queue.length) return;
  busy = true;
  const n = queue[0];
  const top = $("#deck").lastElementChild;
  if (top) {
    top.style.transform = `translateX(${liked ? 600 : -600}px) rotate(${liked ? 25 : -25}deg)`;
    top.style.opacity = "0";
  }
  try {
    const res = await api("/api/swipe", { method: "POST", body: { name_id: n.id, liked } });
    queue.shift();
    await new Promise((r) => setTimeout(r, 180));
    renderDeck();
    if (res.match && res.match.length) showMatch(n.name, res.match);
    if (queue.length < 6) refill();
  } catch (err) {
    toast(err.message);
    renderDeck();
  } finally { busy = false; }
}

$("#like-btn").addEventListener("click", () => decide(true));
$("#nope-btn").addEventListener("click", () => decide(false));
$("#undo-btn").addEventListener("click", async () => {
  try {
    const res = await api("/api/undo", { method: "POST" });
    queue = queue.filter((n) => n.id !== res.card.id);
    queue.unshift(res.card);
    renderDeck();
    toast(`Back to ${res.card.name}`);
  } catch (err) { toast(err.message === "nothing to undo" ? "Nothing to undo" : err.message); }
});

document.addEventListener("keydown", (e) => {
  if ($("#tab-swipe").classList.contains("hidden") || !$("#filters").classList.contains("hidden")) return;
  if (!$("#match-overlay").classList.contains("hidden") || e.target.tagName === "INPUT") return;
  if (!$("#info-overlay").classList.contains("hidden") || !$("#reset-overlay").classList.contains("hidden")) return;
  if (e.key === "i" && queue.length) showInfo(queue[0]);
  if (e.key === "ArrowRight") decide(true);
  if (e.key === "ArrowLeft") decide(false);
  if (e.key === "Backspace") $("#undo-btn").click();
});

function attachDrag(el) {
  let startX = 0, startY = 0, dx = 0, dragging = false;
  const likeStamp = el.querySelector(".stamp.like");
  const nopeStamp = el.querySelector(".stamp.nope");
  el.querySelector(".info-btn").addEventListener("pointerdown", (e) => e.stopPropagation());
  el.querySelector(".info-btn").addEventListener("click", () => showInfo(queue[0]));
  el.addEventListener("pointerdown", (e) => {
    dragging = true; startX = e.clientX; startY = e.clientY; dx = 0;
    el.setPointerCapture(e.pointerId);
    el.classList.add("dragging");
  });
  el.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    dx = e.clientX - startX;
    const dy = (e.clientY - startY) * 0.3;
    el.style.transform = `translate(${dx}px, ${dy}px) rotate(${dx / 18}deg)`;
    likeStamp.style.opacity = Math.max(0, Math.min(1, dx / 100));
    nopeStamp.style.opacity = Math.max(0, Math.min(1, -dx / 100));
  });
  const end = () => {
    if (!dragging) return;
    dragging = false;
    el.classList.remove("dragging");
    if (Math.abs(dx) > 100) decide(dx > 0);
    else {
      el.style.transform = "";
      likeStamp.style.opacity = 0;
      nopeStamp.style.opacity = 0;
    }
  };
  el.addEventListener("pointerup", end);
  el.addEventListener("pointercancel", end);
}

// ---------- match overlay ----------
function showMatch(name, who) {
  $("#match-who").textContent = who.join(" and ");
  $("#match-name").textContent = name;
  $("#match-overlay").classList.remove("hidden");
  api("/api/me").then((m) => updateBadge(m.new_matches)).catch(() => {});
}
$("#match-close").addEventListener("click", () => $("#match-overlay").classList.add("hidden"));

// ---------- filters ----------
function renderFiltersSummary() {
  const parts = [{ m: "Boys", f: "Girls", "": "Both" }[filters.sex]];
  if (filters.countries.length) parts.push(filters.countries.map((c) => FLAGS[c] || c).join(""));
  if (filters.minLen > 2 || filters.maxLen < 14) parts.push(`${filters.minLen}–${filters.maxLen}`);
  if (filters.pop !== "any") parts.push({ top100: "Top 100", top500: "101–500", rare: "Rarer" }[filters.pop]);
  $("#filters-summary").textContent = "· " + parts.join(" · ");
}

function syncFilterUi(f) {
  $$("#f-sex .seg-btn").forEach((b) => b.classList.toggle("active", b.dataset.v === f.sex));
  $$("#f-pop .seg-btn").forEach((b) => b.classList.toggle("active", b.dataset.v === f.pop));
  $("#f-countries").innerHTML = Object.entries(meta.countries).map(([cc, label]) =>
    `<button class="chip ${f.countries.includes(cc) ? "on" : ""}" data-cc="${cc}">${FLAGS[cc] || ""} ${esc(label)}</button>`).join("");
  $("#f-min-len").value = f.minLen;
  $("#f-max-len").value = f.maxLen;
  $("#f-len-label").textContent = `${f.minLen}–${f.maxLen}`;
}

let draft = null;
function openFilters() {
  draft = JSON.parse(JSON.stringify(filters));
  syncFilterUi(draft);
  $("#filters").classList.remove("hidden");
}
$("#filters-btn").addEventListener("click", openFilters);
$("#open-filters-empty").addEventListener("click", openFilters);
$("#filters").addEventListener("click", (e) => { if (e.target.id === "filters") $("#filters").classList.add("hidden"); });

$("#f-sex").addEventListener("click", (e) => {
  if (!e.target.dataset || e.target.dataset.v === undefined) return;
  draft.sex = e.target.dataset.v; syncFilterUi(draft);
});
$("#f-pop").addEventListener("click", (e) => {
  if (!e.target.dataset.v) return;
  draft.pop = e.target.dataset.v; syncFilterUi(draft);
});
$("#f-countries").addEventListener("click", (e) => {
  const cc = e.target.closest("[data-cc]")?.dataset.cc;
  if (!cc) return;
  draft.countries = draft.countries.includes(cc) ? draft.countries.filter((c) => c !== cc) : [...draft.countries, cc];
  syncFilterUi(draft);
});
["#f-min-len", "#f-max-len"].forEach((id) => $(id).addEventListener("input", () => {
  let lo = +$("#f-min-len").value, hi = +$("#f-max-len").value;
  if (lo > hi) { if (id === "#f-min-len") hi = lo; else lo = hi; }
  draft.minLen = lo; draft.maxLen = hi; syncFilterUi(draft);
}));
$("#filters-reset").addEventListener("click", () => { draft = { ...DEFAULT_FILTERS, countries: [] }; syncFilterUi(draft); });
$("#filters-apply").addEventListener("click", async () => {
  filters = draft;
  saveFilters();
  renderFiltersSummary();
  $("#filters").classList.add("hidden");
  queue = [];
  await refill();
});

$("#reset-passed").addEventListener("click", async () => {
  const res = await api("/api/reset-passed", { method: "POST" });
  toast(`${res.restored} names are back in the deck`);
  await refill();
});

// ---------- duel ----------
async function loadDuel() {
  const box = $("#duel");
  const data = await api("/api/duel");
  if (!data.pair) {
    box.innerHTML = `<div class="list-empty">Like at least two names to start ranking them.<br>You have ${data.liked} so far.</div>`;
    $("#duel-info").textContent = "";
    return;
  }
  const [a, b] = data.pair;
  const tile = (n) => `<button class="duel-card" data-id="${n.id}">
      <div class="name">${esc(n.name)}</div>
      <div class="meta muted small">${sexLabel(n.sex)} · Elo ${n.elo} · ${duels(n.games)}</div></button>`;
  box.innerHTML = tile(a) + `<div class="vs">VS</div>` + tile(b);
  $("#duel-info").textContent = `Ranking ${data.liked} liked names. Tap your favourite.`;
  box.querySelectorAll(".duel-card").forEach((el) => el.addEventListener("click", async () => {
    const winner = +el.dataset.id;
    const loser = winner === a.id ? b.id : a.id;
    el.classList.add("win");
    await api("/api/duel", { method: "POST", body: { winner_id: winner, loser_id: loser } });
    setTimeout(loadDuel, 150);
  }));
}

// ---------- ranking ----------
$$("#tab-ranking .seg-btn").forEach((b) => b.addEventListener("click", () => {
  rankMode = b.dataset.rank;
  $$("#tab-ranking .seg-btn").forEach((x) => x.classList.toggle("active", x === b));
  loadRanking();
}));

async function loadRanking() {
  const list = $("#ranking");
  if (rankMode === "mine") {
    const { likes } = await api("/api/likes");
    list.innerHTML = likes.length ? likes.map((n, i) => `
      <li><span class="pos">${i + 1}</span>
        <span class="nm tappable" data-info="${n.id}">${esc(n.name)}<small>${sexLabel(n.sex)} · ${duels(n.games)}${n.matched_with.length ? " · 💞 " + esc(n.matched_with.join(", ")) : ""}</small></span>
        <span class="elo">${n.elo}</span>
        <button class="x" data-id="${n.id}" title="Remove from likes">✕</button></li>`).join("")
      : `<div class="list-empty">No liked names yet. Swipe right on some!</div>`;
    list.querySelectorAll(".x").forEach((x) => x.addEventListener("click", async () => {
      await api(`/api/likes/${x.dataset.id}`, { method: "DELETE" });
      loadRanking();
    }));
  } else {
    const { matches } = await api("/api/matches");
    updateBadge(0);
    list.innerHTML = matches.length ? matches.map((m, i) => `
      <li><span class="pos">${i + 1}</span>
        <span class="nm tappable" data-info="${m.id}">${esc(m.name)}<small>you ${m.my_elo} · ${esc(m.with)} ${m.their_elo}</small></span>
        <span class="elo">${m.joint_elo}</span></li>`).join("")
      : `<div class="list-empty">No shared names yet. When you both like a name, it is ranked here by your average Elo.</div>`;
  }
}

// ---------- matches ----------
async function loadMatches() {
  const { matches } = await api("/api/matches");
  updateBadge(0);
  matches.sort((a, b) => b.ts - a.ts);
  $("#matches").innerHTML = matches.length ? matches.map((m) => `
    <li class="${m.new ? "new" : ""}"><span>💞</span>
      <span class="nm tappable" data-info="${m.id}">${esc(m.name)}<small>You and ${esc(m.with)} both like it · ${new Date(m.ts * 1000).toLocaleDateString()}</small></span>
      <span class="flags">${flagsHtml(m.countries, null, 2)}</span></li>`).join("")
    : `<div class="list-empty">No matches yet. Keep swiping!</div>`;
}

// ---------- name info (fetched only when asked) ----------
const infoCache = {};

function infoRow(label, value, cls = "") {
  if (!value || (Array.isArray(value) && !value.length)) return "";
  const v = Array.isArray(value) ? value.map(esc).join(", ") : esc(value);
  return `<div class="info-row"><div class="k">${label}</div><div class="v ${cls}">${v}</div></div>`;
}

function popularityHtml(p) {
  const who = p.sex === "f" ? "girls" : "boys";
  const max = Math.max(...p.countries.map((c) => c.per10k), 1);
  const rows = p.countries.map((c) => `
    <div class="pop-row">
      <span class="pop-flag">${FLAGS[c.cc] || c.cc}</span>
      <span class="pop-country">${esc(c.country)}</span>
      <span class="pop-rank">#${c.rank}</span>
      <span class="pop-bar"><i style="width:${Math.max(3, (100 * c.per10k) / max)}%"></i></span>
      <span class="pop-share" title="${c.count} births in the data">${c.per10k >= 10 ? Math.round(c.per10k) : c.per10k}</span>
    </div>`).join("");
  const missing = p.missing.map((m) => `${FLAGS[m.cc] || m.cc} ${esc(m.country)}${m.top_only ? ` (not in top ${m.top_only})` : ""}`);
  return `<div class="info-row"><div class="k">Where it's popular</div>
      <div class="pop-table">${rows || '<p class="muted small">No data</p>'}</div>
      <div class="muted small">Rank, and ${who} per 10,000 births with this name.${missing.length ? ` Not found in: ${missing.join(", ")}.` : ""}</div></div>`;
}

async function showInfo(n) {
  if (!n) return;
  $("#info-name").textContent = n.name;
  $("#info-foot").textContent = "";
  $("#info-overlay").classList.remove("hidden");
  const stillOpen = () => $("#info-name").textContent === n.name;  // closed or switched meanwhile
  $("#info-body").innerHTML = `<div id="info-pop"></div><div id="info-ai"></div>`;

  // Popularity comes from our own statistics: instant, no AI call
  api(`/api/popularity/${n.id}`)
    .then((p) => { if (stillOpen()) $("#info-pop").innerHTML = popularityHtml(p); })
    .catch(() => {});

  if (!infoCache[n.id]) {
    $("#info-ai").innerHTML = `<div class="spinner"></div><p class="muted center small">Looking up origin and meaning…</p>`;
  }
  try {
    const data = infoCache[n.id] || await api(`/api/info/${n.id}`);
    infoCache[n.id] = data;
    if (!stillOpen()) return;
    const i = data.info;
    $("#info-ai").innerHTML =
      infoRow("Meaning", i.meaning, "info-meaning") +
      infoRow("Origin", i.origin) +
      infoRow("Pronunciation", i.pronunciation) +
      infoRow("Variants", i.variants) +
      infoRow("Famous namesakes", i.namesakes) +
      infoRow("Good to know", i.note);
    const conf = i.confidence && i.confidence !== "high" ? `${i.confidence} confidence · ` : "";
    const src = i.sources && i.sources.length ? `Sources: ${i.sources.join(", ")}` : (i.searched ? "" : "No web search used");
    $("#info-foot").textContent = conf + src;
  } catch (err) {
    if (stillOpen()) $("#info-ai").innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
}

$("#info-close").addEventListener("click", () => { $("#info-overlay").classList.add("hidden"); $("#info-name").textContent = ""; });
$("#info-overlay").addEventListener("click", (e) => { if (e.target.id === "info-overlay") $("#info-close").click(); });
document.addEventListener("click", (e) => {
  const id = e.target.closest("[data-info]")?.dataset.info;
  if (id) showInfo({ id: +id, name: e.target.closest("[data-info]").firstChild.textContent });
});

// ---------- boot ----------
start().catch(() => showLogin());
