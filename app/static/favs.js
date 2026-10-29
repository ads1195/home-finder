"use strict";
/* Saved homes ("favourites"). Entries are keyed by listing URL (database ids change when the server restarts) and
   carry a small snapshot, so a saved home still shows after it drops out of the current fetch. The server keeps a
   merged copy via /api/favourites/sync (used for the modest 'similar to your saved homes' ranking nudge). */
const FAV_KEY = "pd_saved_v1";
const CARDS = {};                       // favourite key -> card object, for the heart buttons
const HEART = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>';
const Fav = {
  m: (() => { try { return JSON.parse(localStorage.getItem(FAV_KEY)) || {}; } catch (_) { return {}; } })(),
  ready: null, _t: null,
  keep() { try { localStorage.setItem(FAV_KEY, JSON.stringify(this.m)); } catch (_) {} },
  key: c => (typeof c === "string" ? c : (c.url || "id:" + c.id)).split("#")[0],
  has(c) { const e = this.m[this.key(c)]; return !!(e && e.saved); },
  list() { return Object.values(this.m).filter(e => e.saved).sort((a, b) => (b.updated || 0) - (a.updated || 0)); },
  count() { return this.list().length; },
  snap(c) {
    return {id: c.id, title: c.title, address: c.address, price: c.price, image: c.image_remote || c.image, score: c.score, bedrooms: c.bedrooms,
            bathrooms: c.bathrooms, property_type: c.property_type, band: c.band, agent: c.agent, source: (c.sources || [])[0] || ""};
  },
  toggle(c) {
    const k = this.key(c), old = this.m[k], on = !(old && old.saved);
    this.m[k] = {url: k, saved: on, updated: Date.now(), note: old ? old.note || "" : "", snap: this.snap(c), feat: c.feat || (old && old.feat) || null};
    this.keep(); this.sync(); return on;
  },
  note(k, text) { const e = this.m[k]; if (!e) return; e.note = text.slice(0, 600); e.updated = Date.now(); this.keep(); this.sync(); },
  refresh(c) { const e = this.m[this.key(c)]; if (e && e.saved) { e.snap = this.snap(c); e.feat = c.feat || e.feat; this.keep(); } },
  sync(now) {
    clearTimeout(this._t);
    const go = async () => {
      try {
        const r = await api("/favourites/sync", {json: {items: this.m}});
        for (const [u, e] of Object.entries(r.items || {})) { const l = this.m[u]; if (!l || (e.updated || 0) > (l.updated || 0)) this.m[u] = e; }
        this.keep(); renderNav(parse());
      } catch (_) {}
    };
    if (now) return (this.ready = go());
    this._t = setTimeout(go, 400); return null;
  },
};

function heartHtml(c, cls = "") {
  const k = Fav.key(c); CARDS[k] = c; const on = Fav.has(c);
  return `<button type="button" class="heart ${on ? "on" : ""} ${cls}" data-heart="${esc(k)}" aria-pressed="${on}" aria-label="${on ? "Remove from saved" : "Save this home"}" title="${on ? "Saved – tap to remove" : "Save this home"}">${HEART}</button>`;
}
function paintHearts() {
  $$(".heart").forEach(b => { const on = !!(Fav.m[b.dataset.heart] && Fav.m[b.dataset.heart].saved); b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); b.setAttribute("aria-label", on ? "Remove from saved" : "Save this home"); });
  $$(".savebtn").forEach(b => { const on = Fav.has(CARDS[b.dataset.heart] || b.dataset.heart); b.classList.toggle("on", on); b.querySelector(".lbl").textContent = on ? "Saved" : "Save"; });
}
function noteHtml(k, text) {
  return `<div class="note" data-note="${esc(k)}"><textarea maxlength="600" rows="2" placeholder="Add a note – e.g. loved the kitchen, check road noise…" aria-label="Note for this home">${esc(text || "")}</textarea></div>`;
}
function toggleFav(c) {
  const k = Fav.key(c), on = Fav.toggle(c); paintHearts();
  $$(".heart").filter(b => b.dataset.heart === k).forEach(b => { b.classList.remove("pop"); void b.offsetWidth; if (on) b.classList.add("pop"); });
  toast(on ? "♥ Saved" : "Removed from Saved", 1600); renderNav(parse());
  const nb = $("#favnote"); if (nb) { nb.hidden = !on; if (on) nb.innerHTML = noteHtml(k, (Fav.m[k] || {}).note); }
  const lv = $("#listview");
  if (!on && lv && lv.dataset.tab === "saved") {
    const el = $$(".card", lv).find(x => x.dataset.key === k);
    if (el) { el.classList.add("leaving"); setTimeout(() => { el.remove(); if (!$(".card", lv)) renderList("saved", null); }, 320); }
  }
  return on;
}
document.addEventListener("click", e => {
  const b = e.target.closest(".heart,.savebtn"); if (!b) return;
  e.preventDefault(); e.stopPropagation();
  const c = CARDS[b.dataset.heart]; if (c) toggleFav(c);
}, true);
document.addEventListener("input", e => {
  const t = e.target.closest(".note textarea"); if (!t) return;
  const k = t.closest(".note").dataset.note; clearTimeout(t._t); t._t = setTimeout(() => Fav.note(k, t.value), 500);
});

/* Saved tab: merge each saved home with its live card (matched by URL); otherwise show the snapshot. */
async function savedItems() {
  try { await Fav.ready; } catch (_) {}
  let live = {};
  try { live = (await api("/favourites/live")).items || {}; } catch (_) {}
  const q = (listState.q || "").toLowerCase();
  let rows = Fav.list().map(e => ({e, c: live[e.url] || null}));
  rows.forEach(r => { if (r.c) Fav.refresh(r.c); });
  if (q) rows = rows.filter(({e, c}) => ((c ? c.address + " " + c.title : (e.snap || {}).address + " " + (e.snap || {}).title) + " " + (e.note || "")).toLowerCase().includes(q));
  const sc = ({e, c}) => (c ? c.score : (e.snap || {}).score) || 0, pr = ({e, c}) => (c ? c.price : (e.snap || {}).price) || 1e12;
  if (listState.sort === "score") rows.sort((a, b) => sc(b) - sc(a)); else if (listState.sort === "price") rows.sort((a, b) => pr(a) - pr(b));
  return rows;
}
function savedCardHtml({e, c}) {
  if (c) return cardHtml(c, {saved: true, note: e.note});
  const s = e.snap || {}, k = e.url, card = {id: s.id, url: k, title: s.title, address: s.address, image_remote: s.image, feat: e.feat, sources: [s.source], price: s.price, score: s.score, bedrooms: s.bedrooms};
  CARDS[k] = card;
  const bg = s.image ? `style="background-image:url('${esc(s.image)}')"` : "";
  return `<article class="card stale" data-key="${esc(k)}" data-ext="${esc(k)}"><div class="ph" ${bg}><div class="tags"><span class="pill warn">Not in current fetch</span></div>${heartHtml(card)}${s.score != null ? `<span class="scorebub score">${Math.round(s.score)}</span>` : ""}</div>
  <div class="body"><div><span class="price">${gbp(s.price)}</span></div><div class="addr">${esc(s.address || s.title || k)}</div>
  <div class="meta">${s.bedrooms ?? "?"} bed${s.property_type ? ' <span class="dot2">·</span> ' + esc(s.property_type) : ""}</div>
  <div class="drv">Saved snapshot - may have sold or been withdrawn</div>
  <div class="btnrow" style="margin:0"><a class="btn pri" href="${esc(k)}" target="_blank" rel="noopener">Open original ↗</a></div>${noteHtml(k, e.note)}</div></article>`;
}
