"use strict";
/* "Not for us" (downvote + reasons). Like saved homes: keyed by listing URL, kept in localStorage and merged with the server copy
   (/api/downvotes/sync, persisted privately server-side). A downvoted home disappears from every list except Page 'Not for us'. */
const VOTE_KEY = "pd_down_v1";
const THUMB = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17 14V3.5M17 14l-3.4 6.3a1.7 1.7 0 0 1-3.2-.9V16H5.9a2 2 0 0 1-2-2.3l1.2-7A2 2 0 0 1 7.1 5H17m0 9h2.5A1.5 1.5 0 0 0 21 12.5v-7A1.5 1.5 0 0 0 19.5 4H17"/></svg>';
const REASONS = [
  ["openplan", "No open-plan kitchen/dining/living space like our ideal home"], ["lounge", "No separate lounge"],
  ["kitchen_small", "Kitchen/family space too small (no room for sofa+TV)"], ["layout", "Layout unlike our ideal home"], ["utility", "No utility"],
  ["wc", "No downstairs WC / en-suite"], ["beds", "Too few bedrooms / too small"], ["garden", "Garden too small"], ["overlooked", "Overlooked"],
  ["parking", "No front parking / driveway"], ["condition", "Needs work / dated"], ["area", "Area / street not for us"], ["far", "Too far / bad location"],
  ["price", "Price / poor value"], ["style", "Style / looks (e.g. new-build estate, exterior)"], ["noise", "Busy road / noise"], ["other", "Other"],
];
const REASON_LABEL = Object.fromEntries(REASONS);
const Vote = {
  m: (() => { try { return JSON.parse(localStorage.getItem(VOTE_KEY)) || {}; } catch (_) { return {}; } })(),
  ready: null,
  keep() { try { localStorage.setItem(VOTE_KEY, JSON.stringify(this.m)); } catch (_) {} },
  key: c => Fav.key(c),
  has(c) { const e = this.m[this.key(c)]; return !!(e && e.down); },
  list() { return Object.values(this.m).filter(e => e.down).sort((a, b) => (b.updated || 0) - (a.updated || 0)); },
  count() { return this.list().length; },
  set(c, down, reasons, note) {
    const k = this.key(c), old = this.m[k] || {};
    this.m[k] = {url: k, down, reasons: down ? (reasons || []) : (old.reasons || []), note: down ? (note || "") : (old.note || ""), updated: Date.now(), snap: Fav.snap(c), lat: old.lat, lon: old.lon, sk: old.sk};
    this.keep(); return this.sync(true);
  },
  async sync() {
    try {
      const r = await api("/downvotes/sync", {json: {items: this.m}});
      for (const [u, e] of Object.entries(r.items || {})) { const l = this.m[u]; if (!l || (e.updated || 0) > (l.updated || 0)) this.m[u] = e; else if (l && e.lat != null && l.lat == null) { l.lat = e.lat; l.lon = e.lon; l.sk = e.sk; } }
      this.keep(); renderNav(parse());
    } catch (_) {}
  },
};

function downHtml(c, cls = "") {
  const k = Vote.key(c); CARDS[k] = c;
  return `<button type="button" class="downbtn ${cls}" data-down="${esc(k)}" aria-label="Not for us" title="Not for us – tell it why">${THUMB}</button>`;
}
function downBtnDetail(c) {
  const k = Vote.key(c); CARDS[k] = c;
  const on = Vote.has(c) || c.down;
  return `<button class="btn downdetail ${on ? "on" : ""}" id="dwn" data-down="${esc(k)}"><span class="hi">${THUMB}</span><span class="lbl">${on ? "Undo not for us" : "Not for us"}</span></button>`;
}
document.addEventListener("click", e => {
  const b = e.target.closest(".downbtn,.downdetail"); if (!b) return;
  e.preventDefault(); e.stopPropagation();
  const c = CARDS[b.dataset.down]; if (!c) return;
  if (b.classList.contains("downdetail") && (Vote.has(c) || c.down)) { Vote.set(c, false).then(() => { toast("Restored", 1500); renderDetail(c.id); }); return; }
  openVote(c);
}, true);

let _voteCard = null;
function closeVote() { const s = $("#votesheet"); if (s) s.remove(); _voteCard = null; }
function openVote(c) {
  closeVote(); _voteCard = c;
  const sus = new Set(c.suspect || []);
  const sh = document.createElement("div"); sh.id = "votesheet";
  sh.innerHTML = `<div class="vs" role="dialog" aria-label="Not for us">
    <div class="vgrab"></div>
    <div class="vhead"><b>Not for us?</b><span class="vaddr">${esc(c.address || c.title || "")}</span></div>
    <p class="vhint">Tap any reasons (optional) – they help us find better matches. <span class="vdot"></span> Dotted = what we already suspect.</p>
    <div class="vpills">${REASONS.map(([id, l]) => `<button type="button" class="rp ${sus.has(id) ? "sug" : ""}" data-r="${id}" aria-pressed="false">${esc(l)}</button>`).join("")}</div>
    <textarea id="vnote" maxlength="200" rows="1" placeholder="Anything else? (optional, short)"></textarea>
    <div class="vact"><button type="button" class="btn pri" id="vhide">Not for us</button><button type="button" class="vcancel" id="vcancel">Cancel</button></div>
  </div>`;
  document.body.appendChild(sh);
  sh.onclick = e => { if (e.target === sh) closeVote(); };
  $$(".rp", sh).forEach(b => b.onclick = () => { const on = b.classList.toggle("on"); b.setAttribute("aria-pressed", on); const n = $$(".rp.on", sh).length; $("#vhide").innerHTML = "Not for us" + (n ? ` <span class="vcount">· ${n} reason${n > 1 ? "s" : ""}</span>` : ""); });
  $("#vcancel").onclick = closeVote;
  $("#vhide").onclick = () => submitVote(c, $$(".rp.on", sh).map(b => b.dataset.r), $("#vnote").value.trim());
}
async function submitVote(c, reasons, note) {
  closeVote();
  const k = Vote.key(c);
  const el = $$(".card").find(x => x.dataset.key === k);
  if (el) { el.classList.add("leaving"); setTimeout(() => el.remove(), 320); }
  CARDS[k] = c; const saving = Vote.set(c, true, reasons, note);
  renderNav(parse());
  const dv = $("#detailview"); if (dv && dv.querySelector("#dwn") && dv.querySelector("#dwn").dataset.down === k) dv.innerHTML = '<div class="empty">Hidden – not for us.</div>';
  const inDetail = document.body.className === "view-detail";
  if (inDetail) { await saving; location.hash = "#/" + lastTab; }
  else if (!$$(".card").some(x => x !== el)) setTimeout(() => { const lv = $("#listview"); if (lv && lv.dataset.tab) renderList(lv.dataset.tab, null); }, 350);
  toastUndo("👎 Hidden – not for us", async () => { await Vote.set(c, false); if (parse().tab === "notforus") pageNotForUs(); else route(); toast("Restored", 1500); });
}
function toastUndo(msg, fn) {
  const t = $("#toast"); t.innerHTML = `${esc(msg)} <button type="button" class="tundo">Undo</button>`; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => t.hidden = true, 7000);
  $(".tundo", t).onclick = e => { e.stopPropagation(); t.hidden = true; fn(); };
}

/* 'Not for us' page (More menu): the downvoted homes with their reasons + Undo, then 'What we've learned'. */
async function pageNotForUs() {
  const p = $("#page");
  try { await Vote.sync(); } catch (_) {}
  let live = {}, learned = null;
  try { live = (await api("/downvotes/live")).items || {}; } catch (_) {}
  try { learned = await api("/learned"); } catch (_) {}
  const rows = Vote.list();
  const card = e => {
    const c = live[e.url], s = e.snap || {}, k = e.url;
    const cc = c || {id: s.id, url: k, title: s.title, address: s.address, image_remote: s.image, image: s.image, price: s.price, score: s.score, bedrooms: s.bedrooms, property_type: s.property_type, sources: [s.source]};
    CARDS[k] = cc;
    const bg = cc.image ? `style="background-image:url('${esc(cc.image)}')"` : "";
    const why = (e.reasons || []).map(r => `<span class="pill">${esc(REASON_LABEL[r] || r)}</span>`).join("") || '<span class="hint">no reason given</span>';
    return `<article class="card nfu" data-key="${esc(k)}" ${c ? `data-id="${c.id}"` : `data-ext="${esc(k)}"`}><div class="ph" ${bg}>${c ? "" : '<div class="tags"><span class="pill warn">Not in current fetch</span></div>'}</div>
      <div class="body"><div><span class="price">${gbp(cc.price)}</span></div><div class="addr">${esc(cc.address || cc.title || k)}</div>
      <div class="meta">${cc.bedrooms ?? "?"} bed${cc.property_type ? ' <span class="dot2">·</span> ' + esc(cc.property_type) : ""}</div>
      <div class="whys">${why}</div>${e.note ? `<div class="hint" style="margin-top:6px">“${esc(e.note)}”</div>` : ""}
      <div class="btnrow" style="margin:10px 0 0"><button class="btn" data-undo="${esc(k)}">Undo – bring it back</button></div></div></article>`;
  };
  const eff = r => r.effects.map(x => `${esc(x.name)} ${x.default}→<b>${x.now}</b>`).join(", ");
  const lr = learned ? learned.rows.filter(r => r.count) : [];
  p.innerHTML = `<div class="banner">Homes you marked <b>Not for us</b> are hidden everywhere else. The reasons you give nudge the scoring (never more than 2×) so similar homes drop down the list. Tap <b>Undo</b> to bring one back.</div>
    <div class="cards">${rows.map(card).join("") || '<div class="empty" style="grid-column:1/-1">Nothing here yet. Tap 👎 on a home that isn\'t right.</div>'}</div>
    <details class="sec" ${lr.length ? "open" : ""} style="margin-top:16px"><summary>What we've learned</summary><div class="in">
      ${lr.length ? `<table class="t"><tr><th>Reason</th><th>Votes</th><th>Effect on scoring</th></tr>${lr.map(r => `<tr><td>${esc(r.label)}</td><td>${r.count}</td><td>${r.mult > 1 ? "×" + r.mult + " " : ""}<small class="hint">${eff(r)}${r.extra ? (r.effects.length ? "; " : "") + esc(r.extra) : ""}</small></td></tr>`).join("")}</table>
      <p class="hint">Each vote nudges the related weight up a little (1 vote ≈ +11%, 5 ≈ +38%, never more than 2×). Homes near or on the same street as 'area' votes lose up to 4 points; similar-looking photos for 'style' up to 3. Your ♥ saved homes work the same way in the other direction.</p>
      <div class="btnrow"><button class="btn danger" id="lreset">Reset what's been learned</button></div>` : '<p class="hint">Nothing learned yet. Use 👎 on homes that aren\'t right and say why – the scoring adapts, and you can see exactly how here.</p>'}
    </div></details>`;
  $$("[data-undo]", p).forEach(b => b.onclick = async e => { e.stopPropagation(); const k = b.dataset.undo; const c = CARDS[k]; const art = b.closest(".card"); art.classList.add("leaving"); await Vote.set(c, false); setTimeout(pageNotForUs, 300); toast("Restored", 1500); });
  $$(".card[data-id]", p).forEach(el => el.onclick = e => { if (e.target.closest("button,a")) return; location.hash = `#/listing/${el.dataset.id}`; });
  $$(".card[data-ext]", p).forEach(el => el.onclick = e => { if (e.target.closest("button,a")) return; window.open(el.dataset.ext, "_blank", "noopener"); });
  const rs = $("#lreset"); if (rs) rs.onclick = async () => { if (!confirm("Reset what's been learned? The hidden homes stay hidden; only the scoring nudges are cleared.")) return; await api("/learned/reset", {method: "POST"}); toast("Learning reset", 1600); pageNotForUs(); };
}
