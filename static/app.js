"use strict";

// ---------------------------------------------------------------- constants
const SUIT_SYM = ["♠", "♥", "♦", "♣"];
const SUIT_NAME = ["Spades", "Hearts", "Diamonds", "Clubs"];
const RANK_LBL = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"];
const NAMES = ["You", "West", "North", "East"];
const DISPLAY_SUIT_ORDER = [0, 1, 3, 2];  // alternate black / red
const TEN = 8;

const suitOf = c => Math.floor(c / 13);
const rankOf = c => c % 13;
const isRed = s => s === 1 || s === 2;
const cardLabel = c => RANK_LBL[rankOf(c)] + SUIT_SYM[suitOf(c)];
const sleep = ms => new Promise(r => setTimeout(r, ms));
const $ = sel => document.querySelector(sel);

function store(key, val) {
  try {
    if (val === undefined) return JSON.parse(localStorage.getItem("dehla." + key));
    localStorage.setItem("dehla." + key, JSON.stringify(val));
  } catch (e) { return undefined; }
}

// ---------------------------------------------------------------- state
let S = null;          // last state from the server
let D = null;          // what is currently drawn (advances event by event)
let busy = false;      // a request or animation is running
let thinking = null;   // seat currently thinking
let hint = null;       // last hint response
let resultClosed = false;
let reveal = !!store("reveal");
let selected = null;   // touch screens: card raised by the first tap
const TOUCH = window.matchMedia("(pointer: coarse)").matches;

function syncDisplay() {
  D = {
    hands: (S.hands || [[], [], [], []]).map(h => h.slice()),
    trick: (S.trick || []).map(t => t.slice()),
    tens_cards: (S.tens_cards || [[], []]).map(t => t.slice()),
    tricks_won: (S.tricks_won || [0, 0]).slice(),
    trump: S.trump ?? null,
    turn: S.turn,
    pot: (S.pot || []).slice(),      // two-in-a-row: cards waiting in the middle
    streak: S.streak ?? null,        // two-in-a-row: seat one win from collecting
  };
}

const isStreak = () => S && S.variant === "streak";

// Desktop: requests go to the Python server. Standalone (phone app): the same
// Python Session runs in a background worker via Pyodide (see worker.js).
const STANDALONE = !!window.DEHLA_STANDALONE;
let engineWorker = null, nextReq = 1;
const pendingReqs = new Map();

function engine() {
  if (!engineWorker) {
    engineWorker = new Worker("static/worker.js");
    engineWorker.onmessage = e => {
      const { id, ok, result, error, progress } = e.data;
      if (progress !== undefined) { showLoading(progress); return; }
      const p = pendingReqs.get(id);
      pendingReqs.delete(id);
      if (p) ok ? p.resolve(result) : p.reject(new Error(error));
    };
  }
  return engineWorker;
}

function showLoading(msg) {
  let el = $("#loading");
  if (!msg) { if (el) el.remove(); return; }
  if (!el) {
    el = document.createElement("div");
    el.id = "loading";
    el.innerHTML = `<div class="panel"><div class="spinner"></div><div class="msg"></div>
      <div class="sub">First start downloads the game engine (~10 MB); after that it works offline.</div></div>`;
    $("#table").appendChild(el);
  }
  el.querySelector(".msg").textContent = msg;
}

async function api(path, body) {
  if (STANDALONE) {
    const name = path.replace("/api/", "");
    const result = await new Promise((resolve, reject) => {
      const id = nextReq++;
      pendingReqs.set(id, { resolve, reject });
      engine().postMessage({ id, name, body: body || {} });
    });
    if (result && result.state && result.state.tallies) store("tallies", result.state.tallies);
    return result;
  }
  const opts = path === "/api/state" ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  };
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* ignore */ }
    throw new Error(msg);
  }
  return r.json();
}

// ---------------------------------------------------------------- card html
function cardHTML(c, { cls = "", style = "", attrs = "", extra = "" } = {}) {
  const s = suitOf(c), r = rankOf(c), sym = SUIT_SYM[s];
  const color = isRed(s) ? "red" : "black";
  const lbl = RANK_LBL[r];
  // Big rank + suit in the top-left corner; one large symbol (or a face panel)
  // in the bottom-right, so nothing ever overlaps the rank.
  const art = r >= 9 && r <= 11
    ? `<div class="face"><b>${lbl}</b><i>${sym}</i></div>`
    : `<div class="big-suit ${r === 12 ? "ace" : ""}">${sym}</div>`;
  return `<div class="card ${color} ${r === TEN ? "ten" : ""} ${cls}" data-id="${c}" style="${style}" ${attrs}>
    <div class="idx ${lbl.length > 1 ? "two" : ""}"><b>${lbl}</b><i>${sym}</i></div>${art}${extra}</div>`;
}
const backHTML = (style = "") => `<div class="card back" style="${style}"></div>`;

function sortHand(cards, trump) {
  const order = DISPLAY_SUIT_ORDER.slice();
  if (trump !== null && trump !== undefined) {
    order.splice(order.indexOf(trump), 1);
    order.unshift(trump);
  }
  return cards.slice().sort((a, b) =>
    order.indexOf(suitOf(a)) - order.indexOf(suitOf(b)) || rankOf(b) - rankOf(a));
}

// ---------------------------------------------------------------- rendering
function humanToAct() {
  return S && !busy && S.phase === "play" && S.turn === 0;
}

function renderHand(p, justAdded) {
  const el = $(`#hand-${p}`);
  const cards = sortHand(D.hands[p], D.trump);
  if (p === 0) {
    const legal = new Set(humanToAct() ? S.legal : []);
    const vals = hint && hint.kind === "card" ? hint.values : null;
    const n = cards.length, mid = (n - 1) / 2;
    el.innerHTML = cards.map((c, i) => {
      const off = i - mid;
      const narrow = innerWidth < 700;  // phones: flatter fan so the end cards stay on screen
      const style = `--rot:${(off * (narrow ? 0.9 : 1.8)).toFixed(2)}deg;--ty:${(off * off * (narrow ? 0.3 : 0.6)).toFixed(1)}px;z-index:${i + 1}`;
      let cls = "";
      if (humanToAct()) cls = legal.has(c) ? "playable" : "dim";
      if (humanToAct() && c === selected) cls += " selected";
      if (justAdded && justAdded.has(c)) cls += " dealt-in";
      let extra = "";
      if (vals && vals[c] !== undefined) {
        const best = hint.best === c;
        const tie = !best && (hint.tied || []).includes(c);
        if (best) cls += " best";
        extra = `<span class="ev ${best ? "best" : tie ? "tie" : ""}" title="${tie ? "About as good as the highlighted card" : ""}">${fmtEv(vals[c])}${tie ? " ≈" : ""}</span>`;
      }
      return cardHTML(c, { cls, style, extra });
    }).join("");
    return;
  }
  el.classList.toggle("revealed", reveal);
  if (!reveal) { el.innerHTML = cards.map(() => backHTML()).join(""); return; }
  if (p === 2) { el.innerHTML = cards.map(c => cardHTML(c)).join(""); return; }
  // Side seats: one overlapped row per suit keeps the revealed hand compact.
  const rows = [];
  for (const c of cards) {
    if (!rows.length || suitOf(rows[rows.length - 1][0]) !== suitOf(c)) rows.push([]);
    rows[rows.length - 1].push(c);
  }
  el.innerHTML = rows.map(r => `<div class="suitrow">${r.map(c => cardHTML(c)).join("")}</div>`).join("");
}

function fmtEv(v) {
  return (v >= 0 ? "+" : "") + v.toFixed(2);
}

function renderTrick() {
  const el = $("#trick");
  el.querySelectorAll(".slot").forEach(s => s.remove());
  for (const [p, c] of D.trick) el.insertAdjacentHTML("beforeend", slotHTML(p, c));
}
function slotHTML(p, c, cls = "") {
  const rot = ((c * 37) % 11) - 5;
  return `<div class="slot ${cls}" data-p="${p}">${cardHTML(c, { style: `--r:${rot}deg` })}</div>`;
}

function renderPlates() {
  for (let p = 0; p < 4; p++) {
    const el = $(`#plate-${p}`);
    if (!S || S.phase === "none") { el.innerHTML = plateInner(p, false); continue; }
    const active = thinking === p || (!thinking && S.phase === "play" && D.turn === p && !busy) ||
      (S.phase === "trump" && S.chooser === p && !busy);
    el.classList.toggle("active", !!active);
    el.innerHTML = plateInner(p, thinking === p);
  }
}
function plateInner(p, isThinking) {
  const team = p % 2;
  let chips = "";
  if (S && S.phase !== "none") {
    if (S.dealer === p) chips += `<span class="chip dealer" title="Dealer">D</span>`;
    if (S.chooser === p) chips += `<span class="chip caller" title="Called trump">TRUMP</span>`;
    if (isStreak() && D && D.streak === p && D.pot.length)
      chips += `<span class="chip streak" title="Won the last trick: winning the next one too collects the middle">1 MORE TO COLLECT</span>`;
  }
  const count = p !== 0 && D ? `<span class="count">${D.hands[p].length}</span>` : "";
  const dots = isThinking ? `<span class="dots"><i></i><i></i><i></i></span>` : "";
  const initial = p === 0 ? "Y" : NAMES[p][0];
  return `<span class="avatar t${team}">${initial}</span>${NAMES[p]}${p === 2 ? " <span class='count partner'>(partner)</span>" : ""} ${count}${chips}${dots}`;
}

function renderHUD() {
  const sym = $("#trump-sym");
  const t = D ? D.trump : null;
  if (t === null || t === undefined) {
    sym.textContent = "?"; sym.className = "trump-sym";
    $("#trump-name").textContent = "Not called";
    $("#trump-caller").textContent = S && S.phase === "trump" ? `${NAMES[S.chooser]} to call` : "";
  } else {
    sym.textContent = SUIT_SYM[t];
    sym.className = "trump-sym " + (isRed(t) ? "red" : "black");
    $("#trump-name").textContent = SUIT_NAME[t];
    $("#trump-caller").textContent = `Called by ${S.chooser === 0 ? "you" : NAMES[S.chooser]}`;
  }
  for (const team of [0, 1]) {
    const tens = D ? D.tens_cards[team] : [];
    $(`#tens-${team}`).innerHTML = tens.map(c => cardHTML(c)).join("") || `<span class="slot-empty"></span>`;
    $(`#tricks-${team}`).textContent = D ? D.tricks_won[team] : 0;
  }
  const tl = S ? S.tally : { win: 0, tie: 0, loss: 0 };
  const rules = isStreak() ? "Two in a row" : "Classic";
  $("#tally").innerHTML = `${rules}: <b>${tl.win}</b> W · <b>${tl.tie}</b> T · <b>${tl.loss}</b> L`;
  renderPot();
}

function renderPot() {
  const el = $("#pot");
  const pot = D && isStreak() ? D.pot : [];
  el.classList.toggle("hidden", !pot.length);
  if (!pot.length) return;
  const stack = Math.min(3, Math.ceil(pot.length / 4));
  el.querySelector(".pot-stack").innerHTML = Array.from({ length: stack }, (_, i) =>
    `<div class="card back" style="transform:translate(${-i * 3}px, ${-i * 3}px)"></div>`).join("");
  const tricks = pot.length / 4;
  el.querySelector(".pot-label").textContent = `Middle: ${tricks} trick${tricks === 1 ? "" : "s"}`;
  el.querySelector(".pot-tens").innerHTML = pot.filter(c => rankOf(c) === TEN).map(c => cardHTML(c)).join("");
}

function renderStatus() {
  const el = $("#status");
  el.classList.remove("you");
  if (!S || S.phase === "none") { el.textContent = ""; return; }
  if (thinking !== null) {
    el.textContent = S.phase === "trump" ? `${NAMES[thinking]} is choosing trump…` : `${NAMES[thinking]} is thinking…`;
    return;
  }
  if (busy) { el.textContent = ""; return; }
  if (S.phase === "trump" && S.chooser === 0) {
    el.textContent = "Call trump"; el.classList.add("you"); return;
  }
  if (S.phase === "play" && S.turn === 0) {
    el.classList.add("you");
    if (selected !== null) { el.textContent = "Tap the raised card again to play it"; return; }
    if (!S.trick.length) { el.textContent = "Your lead"; return; }
    const led = suitOf(S.trick[0][1]);
    const canFollow = S.hands[0].some(c => suitOf(c) === led);
    el.textContent = canFollow ? `Your turn: follow ${SUIT_SYM[led]}` : `Your turn: no ${SUIT_SYM[led]}, play any card`;
    return;
  }
  if (S.phase === "done") {
    el.textContent = S.outcome > 0 ? "Your team won the hand" : S.outcome < 0 ? "Your team lost the hand" : "Tied 2-2";
    return;
  }
  el.textContent = "";
}

function renderControls() {
  const humanDecision = S && !busy && ((S.phase === "play" && S.turn === 0) || (S.phase === "trump" && S.chooser === 0));
  $("#btn-hint").disabled = !(humanDecision && S.phase === "play");
  $("#btn-hint-m").disabled = $("#btn-hint").disabled;
  if (!humanToAct()) selected = null;
  $("#btn-last").disabled = !(S && S.last_trick);
  $("#btn-new").disabled = busy;
  $("#btn-reset").disabled = busy;
  $("#reveal").checked = reveal;
  if (S && S.think_time) $("#think").value = String(S.think_time);
  $("#variant").value = (S && S.variant) || "classic";
  $("#variant").disabled = busy;
}

function renderPicker() {
  const show = S && !busy && S.phase === "trump" && S.chooser === 0;
  $("#trump-picker").classList.toggle("hidden", !show);
  if (!show) return;
  const counts = [0, 0, 0, 0];
  for (const c of S.hands[0]) counts[suitOf(c)]++;
  const vals = hint && hint.kind === "trump" ? hint.values : null;
  $("#suit-buttons").innerHTML = [0, 1, 2, 3].map(s => {
    const best = vals && hint.best === s;
    const ev = vals ? `<span class="ev ${best ? "best" : ""}">${fmtEv(vals[s])}</span>` : "";
    return `<button class="suit-btn ${isRed(s) ? "red" : "black"} ${best ? "best" : ""}" data-suit="${s}">
      ${ev}<span class="sym">${SUIT_SYM[s]}</span><span class="nm">${SUIT_NAME[s]}</span>
      <span class="ct">${counts[s]} card${counts[s] === 1 ? "" : "s"}</span></button>`;
  }).join("");
}

function renderAll() {
  if (!D) return;
  for (let p = 0; p < 4; p++) renderHand(p);
  renderTrick();
  renderPlates();
  renderHUD();
  renderStatus();
  renderControls();
  renderPicker();
  renderModal();
}

// ---------------------------------------------------------------- modal
function renderModal() {
  const m = $("#modal");
  if (m.dataset.kind === "rules" && !m.classList.contains("hidden")) return;
  if (!S || S.phase === "none") { showWelcome(); return; }
  if (S.phase === "done" && !resultClosed && !busy) { showResult(); return; }
  if (m.dataset.kind !== "rules") m.classList.add("hidden");
}

function showWelcome() {
  const m = $("#modal");
  m.dataset.kind = "welcome";
  $("#modal-card").innerHTML = `
    <div class="suits-deco">♠<span class="r">♥</span>♣<span class="r">♦</span></div>
    <h1>Dehla Pakad</h1>
    <p>You and North against West and East. Capture three or more of the four 10s to win.</p>
    <div class="actions"><button class="btn primary" id="m-deal">Deal the cards</button>
    <button class="btn" id="m-rules">How to play</button></div>`;
  m.classList.remove("hidden");
  $("#m-deal").onclick = () => newHand();
  $("#m-rules").onclick = showRules;
}

function showResult() {
  const m = $("#modal");
  m.dataset.kind = "result";
  const o = S.outcome;
  const title = o > 0 ? "You win!" : o < 0 ? "You lose" : "It's a tie";
  const sub = o > 0 ? (S.tens_won[0] === 4 ? "Mendikot! Your team captured all four 10s." : "Your team captured three 10s.")
    : o < 0 ? (S.tens_won[1] === 4 ? "Mendikot: West & East took all four 10s." : "West & East captured three 10s.")
      : "Each team captured two 10s.";
  const row = (team, label) => `<div class="rrow"><div class="team-name"><span class="dot t${team}"></span>${label}</div>
    <div class="tens">${S.tens_cards[team].map(c => cardHTML(c)).join("") || "<span class='count'>none</span>"}</div>
    <div class="count">${S.tricks_won[team]} tricks</div></div>`;
  $("#modal-card").innerHTML = `
    <h1 class="${o > 0 ? "win" : o < 0 ? "loss" : ""}">${title}</h1>
    <p>${sub} Finished after ${S.tricks_played} trick${S.tricks_played === 1 ? "" : "s"}.</p>
    <div class="result-rows">${row(0, "You &amp; North")}${row(1, "West &amp; East")}</div>
    <p>${isStreak() ? "Two in a row" : "Classic"} session: ${S.tally.win} won · ${S.tally.tie} tied · ${S.tally.loss} lost</p>
    <div class="actions"><button class="btn primary" id="m-next">Deal next hand</button>
    <button class="btn" id="m-close">View table</button></div>`;
  m.classList.remove("hidden");
  $("#m-next").onclick = () => newHand();
  $("#m-close").onclick = () => { resultClosed = true; m.classList.add("hidden"); };
}

function showRules() {
  const m = $("#modal");
  const prev = m.dataset.kind;
  m.dataset.kind = "rules";
  $("#modal-card").innerHTML = `
    <h1>How to play</h1>
    <ul class="rules">
      <li>Four players in two teams. Your partner is <b>North</b>, across the table.</li>
      <li>Everyone gets 5 cards. The player on the dealer's left looks at them and <b>calls trump</b>, then the rest are dealt (13 each).</li>
      <li>The caller leads the first trick. You <b>must follow suit</b> if you can; otherwise play any card, including a trump.</li>
      <li>The highest trump wins the trick, or the highest card of the suit led if no trump was played. Aces are high. The winner leads next.</li>
      <li>The <b>10s</b> are what count. The team that captures <b>3 or 4</b> of them wins the hand. 2-2 is a tie.</li>
      <li><b>Two in a row</b> rules (switch under <i>Rules</i>): tricks aren't taken straight away. Their cards pile up in the middle until one player, not just their team, wins <b>two tricks in a row</b> and collects the whole pile, 10s included. Then the count starts over. The winner of the last trick takes whatever is left.</li>
      <li><b>Hint</b> runs the AI's Monte Carlo search for your seat and shows each card's expected score (+1 win, 0 tie, &minus;1 loss). Cards marked &asymp; are as good as the highlighted one; the highlight goes to the cheapest of them, keeping 10s, trumps and high cards for later.</li>
    </ul>
    <div class="actions"><button class="btn primary" id="m-ok">Got it</button></div>`;
  m.classList.remove("hidden");
  $("#m-ok").onclick = () => {
    m.dataset.kind = "";
    if (prev === "welcome" || prev === "result") renderModal(); else m.classList.add("hidden");
  };
}

// ---------------------------------------------------------------- animation helpers
// Animations pause while the tab is hidden; never let the game wait on them forever.
const finished = (anim, dur) => Promise.race([anim.finished.catch(() => {}), sleep(dur + 150)]);

async function flyFrom(el, from, dur = 420) {
  const to = el.getBoundingClientRect();
  const dx = (from.left + from.width / 2) - (to.left + to.width / 2);
  const dy = (from.top + from.height / 2) - (to.top + to.height / 2);
  const sc = Math.max(0.6, Math.min(1.25, from.width / to.width));
  await finished(el.animate([
    { transform: `translate(${dx}px, ${dy}px) scale(${sc})` },
    { transform: "translate(0, 0) scale(1)" },
  ], { duration: dur, easing: "cubic-bezier(.22,.8,.26,1)" }), dur);
}

function flyBack(fromRect, toEl, dur = 300) {
  const to = toEl.getBoundingClientRect();
  const w = parseFloat(getComputedStyle($("#deck .card")).width) || 60;
  const div = document.createElement("div");
  div.className = "card back flying";
  div.style.cssText = `--w:${w}px;left:${fromRect.left + fromRect.width / 2 - w / 2}px;top:${fromRect.top + fromRect.height / 2 - w * 0.7}px`;
  document.body.appendChild(div);
  const dx = (to.left + to.width / 2) - (fromRect.left + fromRect.width / 2);
  const dy = (to.top + to.height / 2) - (fromRect.top + fromRect.height / 2);
  return finished(div.animate([
    { transform: "translate(0,0) rotate(0deg)" },
    { transform: `translate(${dx}px, ${dy}px) rotate(${(Math.random() * 20 - 10).toFixed(1)}deg)` },
  ], { duration: dur, easing: "cubic-bezier(.3,.7,.3,1)" }), dur).then(() => div.remove());
}

function toast(text, kind = "") {
  const t = document.createElement("div");
  t.className = "toast " + kind;
  t.textContent = text;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), 2700);
}

// ---------------------------------------------------------------- event animations
async function animDeal(round, next) {
  const per = round === 1 ? 5 : 8;
  if (round === 1) D.hands = [[], [], [], []];
  const pending = next.hands.map((h, p) => {
    const rest = h.filter(c => !D.hands[p].includes(c));
    for (let i = rest.length - 1; i > 0; i--) {  // shuffle the order they arrive in
      const j = Math.floor(Math.random() * (i + 1));
      [rest[i], rest[j]] = [rest[j], rest[i]];
    }
    return rest;
  });
  renderAll();
  $("#deck").classList.remove("hidden");
  const deckRect = $("#deck").getBoundingClientRect();
  const flights = [];
  for (let k = 0; k < per * 4; k++) {
    const p = (next.dealer + 1 + k) % 4;
    const c = pending[p].shift();
    if (c === undefined) continue;
    flights.push(flyBack(deckRect, $(`#hand-${p}`), 320).then(() => {
      D.hands[p].push(c);
      renderHand(p, new Set([c]));
      renderPlates();
    }));
    await sleep(round === 1 ? 70 : 45);
  }
  await Promise.all(flights);
  $("#deck").classList.add("hidden");
  await sleep(150);
}

async function animTrump(ev) {
  D.trump = ev.suit;
  const a = $("#announce");
  const who = ev.player === 0 ? "You call" : `${NAMES[ev.player]} calls`;
  a.innerHTML = `<span class="big ${isRed(ev.suit) ? "red" : "black"}">${SUIT_SYM[ev.suit]}</span>
    <span class="txt">${who} ${SUIT_NAME[ev.suit]} as trump</span>`;
  a.classList.remove("hidden");
  a.style.animation = "none"; void a.offsetWidth; a.style.animation = "";
  renderHUD();
  $("#trump-sym").classList.add("pop");
  setTimeout(() => $("#trump-sym").classList.remove("pop"), 700);
  await sleep(1500);
  a.classList.add("hidden");
}

async function animPlay(ev) {
  const { player: p, card: c } = ev;
  const src = document.querySelector(`#hand-${p} [data-id="${c}"]`) ||
    document.querySelector(`#hand-${p} .card:nth-child(${Math.ceil(D.hands[p].length / 2)})`) ||
    $(`#hand-${p}`);
  const from = src.getBoundingClientRect();
  D.hands[p] = D.hands[p].filter(x => x !== c);
  D.trick.push([p, c]);
  D.turn = (p + 1) % 4;
  renderHand(p);
  const faceDown = p !== 0 && !reveal;
  $("#trick").insertAdjacentHTML("beforeend", slotHTML(p, c, faceDown ? "flip" : ""));
  renderPlates();
  const slot = $(`#trick .slot[data-p="${p}"]`);
  await flyFrom(slot, from, 380);
}

async function animTrick(ev) {
  await sleep(650);
  const slots = [...document.querySelectorAll("#trick .slot")];
  const win = slots.find(s => +s.dataset.p === ev.winner);
  if (win) win.classList.add("win");
  await sleep(750);
  // Classic: the trick goes to the winner. Two in a row: it either joins the
  // pile in the middle, or (second win in a row / last trick) the winner
  // sweeps up the trick and the whole pile.
  const collected = ev.collected !== false;
  const potEl = $("#pot");
  if (!collected) potEl.classList.remove("hidden");
  const target = (collected ? $(`#plate-${ev.winner}`) : potEl).getBoundingClientRect();
  const movers = slots.slice();
  if (collected && !potEl.classList.contains("hidden")) movers.push(potEl);
  await Promise.all(movers.map(s => {
    const r = s.getBoundingClientRect();
    const dx = (target.left + target.width / 2) - (r.left + r.width / 2);
    const dy = (target.top + target.height / 2) - (r.top + r.height / 2);
    return finished(s.animate([
      { transform: "none", opacity: 1 },
      { transform: `translate(${dx}px, ${dy}px) scale(.35)`, opacity: collected ? 0 : 0.2 },
    ], { duration: 460, easing: "cubic-bezier(.5,0,.75,0)", fill: collected || s !== potEl ? "forwards" : "none" }), 460);
  }));
  potEl.getAnimations().forEach(a => a.cancel());
  const team = ev.winner % 2;
  D.trick = [];
  if (collected) {
    D.tricks_won[team] += ev.collected_tricks ?? 1;
    D.tens_cards[team].push(...ev.tens);
  }
  D.pot = (ev.pot || []).slice();
  D.streak = ev.streak ?? null;
  D.turn = ev.winner;
  renderTrick();
  renderHUD();
  renderPlates();
  const n = ev.collected_tricks ?? 1;
  if (collected && ev.tens.length) {
    const verb = ev.winner === 0 ? "capture" : "captures";
    const what = n > 1 ? ` (${n} tricks from the middle)` : "";
    toast(`${NAMES[ev.winner]} ${verb} ${ev.tens.map(cardLabel).join(" ")}${what}`, team === 0 ? "good" : "bad");
    const row = $(`#team-${team}`);
    row.classList.remove("pulse"); void row.offsetWidth; row.classList.add("pulse");
  } else if (collected && n > 1) {
    toast(`${NAMES[ev.winner]} ${ev.winner === 0 ? "collect" : "collects"} ${n} tricks from the middle`);
  } else if (!collected) {
    const tens = D.pot.filter(c => rankOf(c) === TEN);
    if (tens.length) {
      const who = ev.winner === 0 ? "you need" : `${NAMES[ev.winner]} needs`;
      toast(`${tens.map(cardLabel).join(" ")} waiting in the middle: ${who} one more trick`);
    }
  }
}

async function applyResponse(resp) {
  const next = resp.state;
  for (const ev of resp.events) {
    if (ev.type === "deal") {
      if (ev.round === 1) { S = next; resultClosed = false; }
      await animDeal(ev.round, next);
    } else if (ev.type === "trump") await animTrump(ev);
    else if (ev.type === "play") await animPlay(ev);
    else if (ev.type === "trick") await animTrick(ev);
    else if (ev.type === "end") await sleep(500);
  }
  S = next;
  syncDisplay();
}

// ---------------------------------------------------------------- game flow
async function pump() {
  if (busy) return;
  busy = true;
  try {
    while (S && (S.phase === "play" || S.phase === "trump")) {
      const humanTurn = (S.phase === "trump" && S.chooser === 0) || (S.phase === "play" && S.turn === 0);
      if (humanTurn) break;
      thinking = S.phase === "trump" ? S.chooser : S.turn;
      renderPlates(); renderStatus(); renderControls();
      let resp;
      try { resp = await api("/api/ai_step"); }
      finally { thinking = null; }
      await applyResponse(resp);
    }
  } catch (e) {
    toast(e.message, "err");
  } finally {
    busy = false;
    renderAll();
  }
}

async function act(fn) {
  if (busy) return;
  busy = true;
  hint = null;
  renderAll();
  try {
    const resp = await fn();
    await applyResponse(resp);
  } catch (e) {
    toast(e.message, "err");
  }
  busy = false;
  renderAll();
  pump();
}

function newHand() {
  if (window.DEHLA_UPDATE_READY) {   // phone app: a new version arrived; load it now, between hands
    toast("Updating to the latest version...");
    setTimeout(() => location.reload(), 400);
    return;
  }
  $("#modal").classList.add("hidden");
  $("#modal").dataset.kind = "";
  act(() => api("/api/new"));
}

async function askHint() {
  if (busy) return;
  busy = true;
  const el = $("#status");
  el.classList.remove("you");
  el.textContent = "Running simulations for you…";
  renderControls();
  try {
    hint = await api("/api/hint");
  } catch (e) { toast(e.message, "err"); }
  busy = false;
  renderAll();
}

// ---------------------------------------------------------------- wiring
$("#hand-0").addEventListener("click", e => {
  e.stopPropagation();  // handled here; the hand re-renders, so don't let the table see it
  const card = e.target.closest(".card.playable");
  if (!card || !humanToAct()) return;
  const id = +card.dataset.id;
  if (TOUCH && selected !== id) {   // first tap raises, second tap plays
    selected = id;
    renderHand(0);
    renderStatus();
    return;
  }
  selected = null;
  act(() => api("/api/play", { card: id }));
});
// Tapping the table (not a card) lowers a raised card again.
$("#table").addEventListener("click", e => {
  if (selected !== null && !e.target.closest("#hand-0")) { selected = null; renderHand(0); renderStatus(); }
});
$("#suit-buttons").addEventListener("click", e => {
  const b = e.target.closest(".suit-btn");
  if (!b) return;
  act(() => api("/api/trump", { suit: +b.dataset.suit }));
});
$("#btn-trump-hint").onclick = askHint;
$("#btn-hint").onclick = askHint;
$("#btn-hint-m").onclick = askHint;
// Phones: the controls live behind a menu button; any choice closes it.
const menu = $("#controls");
$("#btn-menu").onclick = e => {
  e.stopPropagation();
  const open = !menu.classList.contains("open");
  menu.classList.toggle("open", open);
  $("#btn-menu").setAttribute("aria-expanded", String(open));
};
menu.addEventListener("click", e => {
  if (e.target.closest("button")) { menu.classList.remove("open"); $("#btn-menu").setAttribute("aria-expanded", "false"); }
});
document.addEventListener("click", e => {
  if (!e.target.closest("#controls") && !e.target.closest("#btn-menu")) menu.classList.remove("open");
});
$("#btn-new").onclick = () => newHand();
$("#btn-reset").onclick = () => {
  if (!confirm("Reset the game? This clears the session score and deals a fresh hand.")) return;
  $("#modal").classList.add("hidden");
  $("#modal").dataset.kind = "";
  act(() => api("/api/reset"));
};
$("#btn-rules").onclick = showRules;
$("#variant").onchange = e => {
  const v = e.target.value;
  const midHand = S && (S.phase === "play" || S.phase === "trump");
  if (midHand && !confirm("Switching rules deals a new hand. Continue?")) {
    e.target.value = S.variant;
    return;
  }
  $("#modal").classList.add("hidden");
  $("#modal").dataset.kind = "";
  if (STANDALONE) store("variant", v);
  act(() => api("/api/variant", { variant: v }));
};
$("#reveal").onchange = e => { reveal = e.target.checked; store("reveal", reveal); renderAll(); };
$("#think").onchange = async e => {
  try {
    const r = await api("/api/settings", { think_time: parseFloat(e.target.value) });
    S.think_time = r.state.think_time;
    if (STANDALONE) store("think", S.think_time);
  } catch (err) { toast(err.message, "err"); }
};
$("#btn-last").onclick = () => {
  const pop = $("#last-trick");
  if (!pop.classList.contains("hidden") || !S.last_trick) { pop.classList.add("hidden"); return; }
  const lt = S.last_trick;
  pop.innerHTML = `<h3>Last trick</h3><div class="row">${lt.cards.map(([p, c]) =>
    `<div>${cardHTML(c)}<div class="who ${p === lt.winner ? "win" : ""}">${NAMES[p]}${p === lt.winner ? " ★" : ""}</div></div>`).join("")}</div>`;
  pop.classList.remove("hidden");
};
document.addEventListener("click", e => {
  if (!e.target.closest("#last-trick") && !e.target.closest("#btn-last")) $("#last-trick").classList.add("hidden");
});
window.addEventListener("resize", () => renderAll());

(async function init() {
  try {
    S = (await api("/api/state")).state;
    if (STANDALONE) {
      // The phone app keeps its scores between visits; the engine starts fresh.
      const saved = store("tallies");
      if (saved) S = (await api("/api/tallies", saved)).state;
      const think = store("think");
      if (think) S = (await api("/api/settings", { think_time: think })).state;
      const variant = store("variant");
      if (variant) S = (await api("/api/prefer_variant", { variant })).state;
    }
  } catch (e) {
    S = { phase: "none", tally: { win: 0, tie: 0, loss: 0 } };
    toast(STANDALONE ? "Could not start the game engine: " + e.message : "Cannot reach the game server", "err");
  }
  syncDisplay();
  renderAll();
  pump();
})();
