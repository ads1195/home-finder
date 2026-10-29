"use strict";
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const gbp = n => n == null ? "—" : "£" + Number(n).toLocaleString("en-GB");
const fdt = t => t ? new Date(t * 1000).toLocaleString("en-GB", {timeZone: "Europe/London", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"}) : "—";
const ago = t => { if (!t) return "never"; const m = Math.round((Date.now() / 1000 - t) / 60); return m < 1 ? "just now" : m < 90 ? m + " min ago" : m < 2880 ? Math.round(m / 60) + " h ago" : Math.round(m / 1440) + " d ago"; };
const MODE = {offline: false};
let S = null, lastTab = "matches", CFG = null;

async function api(path, opt = {}) {
  const o = {credentials: "same-origin", ...opt};
  if (o.json !== undefined) { o.method = o.method || "POST"; o.headers = {"Content-Type": "application/json"}; o.body = JSON.stringify(o.json); }
  let r;
  try { r = await fetch("/api" + path, o); MODE.offline = false; } catch (e) { MODE.offline = true; $("#offline").hidden = false; throw e; }
  if (r.status === 401) { location.reload(); throw new Error("auth"); }
  if (r.status === 503) { MODE.offline = true; $("#offline").hidden = false; } else { $("#offline").hidden = true; }
  if (!r.ok) { let t = await r.text(); try { t = JSON.parse(t).detail || t; } catch (_) {} throw new Error(t); }
  return r.json();
}
function toast(msg, ms = 3500) { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => t.hidden = true, ms); }

/* ------------------------------------------------------------ nav */
const NAV = [
  {id: "matches", icon: "⭐", label: "Top matches", main: 1}, {id: "just_over", icon: "💷", label: "Over budget", main: 1},
  {id: "all", icon: "🏘️", label: "All", main: 1}, {id: "feed", icon: "🔔", label: "Feed", main: 1},
  {id: "review", icon: "🔎", label: "Needs review"}, {id: "saved", icon: "♥", label: "Saved"},
  {id: "references", icon: "🖼️", label: "Reference homes"}, {id: "sources", icon: "📡", label: "Sources"}, {id: "settings", icon: "⚙️", label: "Settings"},
];
const TITLES = {matches: "Top matches", just_over: "Just over budget", all: "All fetched", review: "Needs review", saved: "Saved homes", feed: "Notifications", references: "Reference homes", sources: "Source status", settings: "Settings"};
function counts() { return (S && S.counts) || {}; }
function renderNav(route) {
  const c = counts();
  const badge = id => id === "feed" && c.unread ? `<b class="badge">${c.unread}</b>` : id === "saved" && Fav.count() ? `<b class="badge soft">${Fav.count()}</b>` : id === "matches" && c.matches ? `<b class="badge amber" style="background:var(--brand)">${c.matches}</b>` : id === "just_over" && c.just_over ? `<b class="badge amber">${c.just_over}</b>` : "";
  const cur = route.tab === "listing" ? lastTab : route.tab;
  const link = n => `<a href="#/${n.id}" class="${n.id === cur ? "on" : ""}"><span class="i">${n.icon}</span><span>${n.label}</span>${badge(n.id)}</a>`;
  const vis = NAV.filter(n => c.show_all || !["all", "review"].includes(n.id));
  const isMain = n => c.show_all ? n.main : (n.main || n.id === "saved");
  $("#sidenav").innerHTML = vis.map(link).join("");
  $("#bottomnav").innerHTML = vis.filter(isMain).map(link).join("") + `<a href="#" id="morebtn" class="${!vis.find(n => n.id === cur && isMain(n)) ? "on" : ""}"><span class="i">☰</span><span>More</span></a>`;
  $("#morebtn").onclick = e => { e.preventDefault(); openSheet(); };
  $("#sidefoot").textContent = S ? (S.poll.running ? "polling…" : "last poll " + ago((S.poll.last || {}).finished)) : "";
}
function openSheet() {
  const sh = $("#sheet"); sh.hidden = false;
  sh.innerHTML = `<div class="s">${NAV.filter(n => (counts().show_all || !["all", "review"].includes(n.id)) && !(counts().show_all ? n.main : (n.main || n.id === "saved"))).map(n => `<a href="#/${n.id}"><span class="i">${n.icon}</span>${n.label}</a>`).join("")}<a href="#" data-x="logout">🚪 Sign out</a></div>`;
  sh.onclick = e => { if (e.target.closest("[data-x=logout]")) { e.preventDefault(); api("/logout", {method: "POST"}).then(() => location.reload()); } sh.hidden = true; };
}

/* ------------------------------------------------------------ routing */
function parse() {
  const h = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (!h.length) return {tab: "matches"};
  if (h[0] === "listing") return {tab: "listing", id: +h[1]};
  return {tab: h[0], id: h[1] ? +h[1] : null};
}
async function route() {
  const r = parse();
  if (parse().tab === "favourites") { location.replace("#/saved"); return; }
  const listTabs = ["matches", "just_over", "all", "review", "saved"];
  let view = "list";
  if (r.tab === "listing" || (listTabs.includes(r.tab) && r.id)) view = "detail";
  else if (!listTabs.includes(r.tab)) view = "page";
  if (listTabs.includes(r.tab)) lastTab = r.tab;
  const lt = r.tab === "listing" ? lastTab : r.tab;
  document.body.className = "view-" + view;
  $("#title").textContent = view === "detail" ? "Listing" : (TITLES[r.tab] || "Property Dashboard");
  $("#backbtn").hidden = view !== "detail";
  renderNav(r);
  if (view === "page") {
    ({feed: pageFeed, references: pageRefs, sources: pageSources, settings: pageSettings}[r.tab] || (() => $("#page").innerHTML = "<div class='empty'>Not found</div>"))();
    $("#page").scrollTop = 0;
    return;
  }
  if (!$("#listview").dataset.tab || $("#listview").dataset.tab !== lt || view === "list") await renderList(lt, r.id || null);
  else $$(".card").forEach(c => c.classList.toggle("sel", +c.dataset.id === (r.id || -1)));
  if (r.id) { await renderDetail(r.id); }
  else if (view === "list") { $("#detailview").innerHTML = '<div class="empty">Select a listing to see photos, floorplan, checklist and drive times.</div>'; }
}
$("#backbtn").onclick = () => { location.hash = "#/" + lastTab; };
window.addEventListener("hashchange", route);

/* ------------------------------------------------------------ list */
let listState = {q: "", sort: "score"};
async function renderList(tab, selId) {
  const lv = $("#listview"); lv.dataset.tab = tab;
  let d;
  try { d = await api(`/listings?tab=${tab}&q=${encodeURIComponent(listState.q)}&sort=${listState.sort}`); }
  catch (e) { lv.innerHTML = `<div class="empty">Could not load listings (${esc(e.message)}).</div>`; return; }
  if (d.counts) { S = S || {}; S.counts = d.counts; renderNav(parse()); }
  const tabs = [["matches", "⭐ Top matches"], ["just_over", "💷 Just over budget"], ...(d.counts.show_all ? [["all", "All fetched"], ["review", "Needs review"]] : []), ["saved", "♥ Saved"]];
  let savedRows = null; if (tab === "saved") { savedRows = await savedItems(); d.items = savedRows; }
  const sortopts = [["score", "Best score"], ["new", "Newest"], ["price", "Price ↑"]];
  const c = d.counts;
  lv.innerHTML = `<div class="tabs">${tabs.map(([id, l]) => `<a href="#/${id}" class="${id === tab ? "on" : ""} ${id === "just_over" ? "over" : ""}">${l}${id === "saved" ? ` <small>(${Fav.count()})</small>` : (c[id] != null ? ` <small>(${c[id]})</small>` : "")}</a>`).join("")}</div>
   <div class="tools"><input id="q" type="search" placeholder="Search address / text" value="${esc(listState.q)}"><select id="sort">${sortopts.map(([v, l]) => `<option value="${v}" ${v === listState.sort ? "selected" : ""}>${l}</option>`).join("")}</select></div>
   ${d.meta ? `<div class="banner">Showing the <b>${d.meta.shown}</b> best of ${d.meta.passing} listings that pass every hard filter (score ≥ ${d.meta.min_score}, max ${d.meta.max_count}). Others stay stored but hidden. <a href="#/settings">Change cut-off</a></div>` : ""}
   ${tab === "just_over" ? '<div class="banner">💷 <b>Just over budget</b> — strong matches priced above your normal ceiling (within the stretch limit). All other hard criteria are met.</div>' : ""}
   ${tab === "review" ? '<div class="banner">These listings fail nothing explicitly but some hard data (price, bedrooms, location/drive time) is still unknown. Check the original.</div>' : ""}
   <div class="cards">${(savedRows ? savedRows.map(savedCardHtml) : d.items.map(c => cardHtml(c))).join("") || `<div class="empty" style="grid-column:1/-1">${MODE.offline ? "Nothing cached yet." : (tab === "saved" ? "Nothing saved yet. Tap the ♥ on any home to keep it here." : "No listings in this view yet. Run a poll (⟳) or relax criteria in Settings.")}</div>`}</div>`;
  $("#q").onchange = e => { listState.q = e.target.value; renderList(tab, selId); };
  $("#sort").onchange = e => { listState.sort = e.target.value; renderList(tab, selId); };
  $$(".card", lv).forEach(el => { el.onclick = e => { if (e.target.closest(".heart,.note,a")) return; if (el.dataset.ext) return window.open(el.dataset.ext, "_blank", "noopener"); location.hash = `#/${tab}/${el.dataset.id}`; }; if (+el.dataset.id === selId) el.classList.add("sel"); });
}
const IC = {confirmed: "✓", contradicted: "✗", not_mentioned: "?"};
function cardHtml(c, o = {}) {
  const drv = Object.entries(c.drive_full || {}).map(([k, v]) => `${esc(k.split(" - ")[0])} ${v.min}m${v.est ? "*" : ""}`).join(" · ");
  const ck = Object.entries(c.checklist).filter(([k, v]) => v.mode !== "off").map(([k, v]) => `<span class="c ${v.state}" title="${esc(v.label)}: ${v.state.replace("_", " ")}">${IC[v.state]} ${esc(shortLabel(k))}</span>`).join("");
  const bg = c.image ? `style="background-image:url('${esc(c.image)}')"` : "";
  return `<article class="card" data-id="${c.id}" data-key="${esc(Fav.key(c))}"><div class="ph" ${bg}><div class="tags">${c.fav_boost >= 0.5 ? `<span class="pill sim" title="Ranked slightly higher because it resembles homes you saved">♥ like your saved +${c.fav_boost}</span>` : ""}${c.is_new ? '<span class="pill new">NEW</span>' : ""}${c.band === "just_over" ? '<span class="pill over">JUST OVER BUDGET</span>' : ""}</div>${heartHtml(c)}${c.score != null ? `<span class="scorebub score">${Math.round(c.score)}</span>` : ""}</div>
  <div class="body"><div><span class="price">${gbp(c.price)}</span> <span class="scoreinline scorebub">${c.score != null ? Math.round(c.score) : ""}</span></div>
  <div class="addr">${esc(c.address || c.title)}</div>
  <div class="meta"><span>🛏 ${c.bedrooms ?? "?"}</span>${c.bathrooms ? `<span>🛁 ${c.bathrooms}</span>` : ""}<span>${esc(c.property_type || "")}</span></div>
  <div class="meta" style="color:var(--mut)">🚗 ${drv || "—"}</div>
  <div class="ck">${ck}</div>
  <div class="meta" style="color:var(--mut);font-size:.75rem">${(c.sources || []).map(esc).join(" + ")} · ${esc(c.agent || "")}</div>${o.saved ? noteHtml(Fav.key(c), o.note) : ""}</div></article>`;
}
const SHORT = {kitchen_family: "Kitchen-diner+family", wc_or_ensuite: "WC/en-suite", utility: "Utility", parking: "Off-street parking", not_overlooked: "Private", garden_size: "Garden"};
const shortLabel = k => SHORT[k] || k;

/* ------------------------------------------------------------ detail */
async function renderDetail(id) {
  const dv = $("#detailview");
  let d;
  try { d = await api("/listings/" + id); } catch (e) { dv.innerHTML = `<div class="empty">Listing not available (${esc(e.message)}).</div>`; return; }
  const l = d.listing, ev = d.evaluation, an = d.analysis || {};
  $("#title").textContent = l.address ? l.address.split(",")[0] : "Listing";
  const photos = d.photos_local.length ? d.photos_local : l.images;
  const fps = d.floorplans_local.length ? d.floorplans_local : l.floorplans;
  const drv = ev ? Object.entries(ev.drive) : [];
  const band = ev && ev.band;
  const sec = (title, inner, open = false, id = "") => `<details class="sec" ${open ? "open" : ""} ${id ? `data-id="${id}"` : ""}><summary>${title}</summary><div class="in">${inner}</div></details>`;
  const station = ev && ev.station ? `<p>🚉 <b>${esc(ev.station.name)}</b> ${ev.station.km} km away <span class="hint">(straight line)</span><br><span class="hint">${esc(ev.station.info)} — indicative only, check National Rail.</span></p>` : "";
  dv.innerHTML = `<div class="dwrap">
   ${photos.length ? `<div class="gallery"><div class="gal-track" id="gt">${photos.map((p, i) => `<img src="${esc(p)}" loading="${i < 2 ? "eager" : "lazy"}" alt="Photo ${i + 1}" data-i="${i}">`).join("")}</div>
     <button class="gal-nav l" aria-label="Previous">‹</button><button class="gal-nav r" aria-label="Next">›</button><span class="gal-count" id="gc">1 / ${photos.length}</span></div>` : '<div class="empty">No photos</div>'}
   <div class="dhead">
     <div style="display:flex;gap:8px;align-items:baseline;flex-wrap:wrap"><span class="price">${gbp(l.price)}</span>
       ${band === "just_over" ? '<span class="pill over">JUST OVER BUDGET</span>' : ""}${ev && ev.passes && band === "in_budget" ? '<span class="pill ok">MATCH</span>' : ""}${ev && !ev.passes ? '<span class="pill bad">does not meet hard criteria</span>' : ""}
       ${ev ? `<span class="scorebub" style="margin-left:auto">score ${Math.round(ev.score)}</span>` : ""}</div>
     <div class="addr" style="font-size:1rem">${esc(l.address)}</div>
     <div class="meta" style="margin-top:6px"><span>🛏 ${l.bedrooms ?? "?"} bed</span><span>🛁 ${l.bathrooms ?? "?"} bath</span><span>${esc(l.property_type || "")}</span><span>${esc(l.tenure || "")}</span><span>${esc(l.agent || "")}</span></div>
     <div class="meta" style="margin-top:6px;gap:6px">${drv.map(([k, v]) => `<span class="pill ${v.drive_min <= (d.anchors.find(a => a.name === k) || {max_drive_min: 25}).max_drive_min ? "ok" : "bad"}">🚗 ${esc(k.split(" - ")[1] ? k.split(" - ")[1].replace(/ \(PRIMARY\)/, "") : k)}: ${Math.round(v.drive_min)} min · ${v.straight_km.toFixed(1)} km${v.method === "estimate" ? " (est.)" : ""}</span>`).join("") || '<span class="hint">drive times pending</span>'}</div>
     ${ev && ev.reasons.length ? `<div class="banner" style="margin-top:8px"><b>Why not a match:</b><ul class="plain">${ev.reasons.map(r => `<li>${esc(r)}</li>`).join("")}</ul></div>` : ""}
     <div class="btnrow">${d.sightings.map(s => `<a class="btn pri" href="${esc(s.url)}" target="_blank" rel="noopener">Open on ${esc(s.source)} ↗</a>`).join("")}
       <button class="btn savebtn ${Fav.has(d.card) ? "on" : ""}" id="fav" data-heart="${esc(Fav.key(d.card))}"><span class="hi">${HEART}</span><span class="lbl">${Fav.has(d.card) ? "Saved" : "Save"}</span></button><button class="btn danger" id="hide">${l.hidden ? "Unhide" : "Hide"}</button></div>
     <div id="favnote" ${Fav.has(d.card) ? "" : "hidden"}>${Fav.has(d.card) ? noteHtml(Fav.key(d.card), (Fav.m[Fav.key(d.card)] || {}).note) : ""}</div>
     <p class="hint">First seen ${fdt(l.first_seen)} (Europe/London) · listed on ${d.sightings.length} source(s): ${d.sightings.map(s => esc(s.source)).join(", ")}</p>
   </div>
   <div class="twocol">
   ${ev ? sec("✅ Your checklist", checklistHtml(ev), true, "checklist") : ""}
   ${ev ? sec("🏠 vs your current home", vsHtml(ev, d.current_home, an), true, "vs") : ""}
   </div>
   ${fps.length ? sec(`📐 Floorplan${fps.length > 1 ? "s (" + fps.length + ")" : ""}`, `<div id="fpv"></div><p class="hint">Pinch or double-tap to zoom, drag to pan (mouse: wheel + drag).</p>`, true, "fp") : ""}
   ${sec("🔬 Layout analysis (floorplan OCR / photos)", layoutHtml(an, ev), false, "layout")}
   ${sec("🗺️ Map & location", `<div class="map" id="map"></div>${station}${drv.length ? `<table class="t"><tr><th>Anchor</th><th>Drive</th><th>Straight</th></tr>${drv.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${Math.round(v.drive_min)} min${v.method === "estimate" ? " (est.)" : ""}</td><td>${v.straight_km.toFixed(1)} km</td></tr>`).join("")}</table><p class="hint">Drive times: OSRM public demo server (typical daytime, no live traffic); “est.” = straight-line × 1.35 at ~42 km/h.</p>` : ""}`, true, "map")}
   ${sec("📝 Description", `<div style="white-space:pre-wrap">${esc(l.description || "—")}</div>`, false, "desc")}
   ${l.features.length ? sec("🔑 Key features", `<ul class="plain">${l.features.map(f => `<li>${esc(f)}</li>`).join("")}</ul>`, false, "feat") : ""}
   ${ev ? sec("🧮 Hard criteria & score breakdown", hardHtml(ev), false, "hard") : ""}
   <div class="btnrow"><button class="btn" id="reanalyse">↻ Re-run analysis</button></div>
  </div>`;
  // gallery
  const gt = $("#gt");
  if (gt) {
    gt.onscroll = () => { const i = Math.round(gt.scrollLeft / gt.clientWidth); $("#gc").textContent = `${i + 1} / ${photos.length}`; };
    $(".gal-nav.l").onclick = () => gt.scrollBy({left: -gt.clientWidth, behavior: "smooth"});
    $(".gal-nav.r").onclick = () => gt.scrollBy({left: gt.clientWidth, behavior: "smooth"});
    $$("img", gt).forEach(im => im.onclick = () => lightbox(photos, +im.dataset.i));
  }
  if (fps.length) zoomViewer($("#fpv"), fps);
  CARDS[Fav.key(d.card)] = d.card; Fav.refresh(d.card);
  $("#hide").onclick = async () => { await api(`/listings/${id}/hide`, {method: "POST"}); toast("Updated"); location.hash = "#/" + lastTab; };
  $("#reanalyse").onclick = async e => { e.target.textContent = "Analysing…"; await api(`/listings/${id}/reanalyse`, {method: "POST"}); renderDetail(id); };
  mapInit(l, d.anchors);
  dv.scrollTop = 0; $("#detailpane").scrollTop = 0;
}
function checklistHtml(ev) {
  const ic = {confirmed: "✅", contradicted: "❌", not_mentioned: "❔"};
  return `<div class="chk">${Object.entries(ev.checklist).filter(([k, v]) => v.mode !== "off").map(([k, v]) => `<div class="row ${v.state}"><div class="ic">${ic[v.state]}</div><div><div class="lab">${esc(v.label)} <span class="pill ${v.mode === "must" ? "warn" : ""}" style="font-size:.65rem">${v.mode === "must" ? "must" : "nice"}</span></div>
   <div class="hint">${v.state === "confirmed" ? "Confirmed" : v.state === "contradicted" ? "Contradicted" : "Not mentioned"}${v.source ? " · via " + esc(v.source) : ""}${v.likely_absent_on_floorplan ? " · not seen on floorplan" : ""}</div>
   ${v.evidence ? `<div class="ev">“${esc(v.evidence)}”</div>` : ""}</div></div>`).join("")}</div>
   <p class="hint">Must-haves exclude a listing only when explicitly contradicted; “not mentioned” keeps it in but flags it.</p>`;
}
function vsHtml(ev, ch, an) {
  const v = ev.vs_current, fp = (an && an.floorplan) || {};
  const row = (lbl, nw, cur, better) => `<tr><td>${lbl}</td><td>${nw ?? "—"} ${better === true ? "🟢" : better === false ? "🔴" : ""}</td><td>${cur ?? "—"}</td></tr>`;
  const b = v.bedrooms;
  return `<table class="t"><tr><th></th><th>This listing</th><th>Current home</th></tr>
   ${row("Bedrooms", b[0], b[1], b[0] != null ? b[0] > b[1] : null)}
   ${row("Bathrooms", v.bathrooms[0], v.bathrooms[1] + (ch.ensuites ? " (incl. en-suite)" : ""), null)}
   ${row("Reception rooms (floorplan)", v.receptions[0], v.receptions[1], v.receptions[0] != null ? v.receptions[0] > v.receptions[1] : null)}
   ${row("Floor area (sq ft)", v.sqft[0] ? v.sqft[0].toLocaleString() + (v.sqft[1] ? ` (${v.sqft[0] >= v.sqft[1] ? "+" : ""}${Math.round((v.sqft[0] / v.sqft[1] - 1) * 100)}%)` : "") + (v.sqft_basis && v.sqft_basis.startsWith("est") ? " ≈ labelled rooms only" : "") : "unknown", v.sqft[1] ? v.sqft[1].toLocaleString() + " (85 m²)" : "not yet entered", v.sqft[0] && v.sqft[1] ? v.sqft[0] > v.sqft[1] : null)}
   ${row("Off-street parking", v.garage_or_drive[0].replace("_", " "), "garage + driveway", null)}
   ${row("Utility room", v.utility[0].replace("_", " "), "none", null)}
   ${row("WC / en-suite", v.downstairs_wc_or_ensuite[0].replace("_", " "), "downstairs WC + en-suite", null)}
   </table><p class="hint">Baseline = your current home. 🟢 bigger than now. Listings ≥ ~1,200 sq ft get a size bonus.</p>`;
}
function layoutHtml(an, ev) {
  const fp = an.floorplan, out = [];
  if (!an || !Object.keys(an).length) return '<p class="hint">Not analysed yet — only candidates that pass the hard filters are analysed in depth.</p>';
  if (fp && fp.usable) {
    out.push(`<p><b>Floorplan OCR</b> (${fp.sheets} sheet(s), ${fp.n_labels} room labels found${fp.stated ? `, stated area ${esc(fp.stated.text)}` : ""}${fp.area_labelled_sqft ? `, labelled rooms ≈ ${fp.area_labelled_sqft} sq ft` : ""}).</p>`);
    out.push(`<table class="t"><tr><th>Room</th><th>Floor</th><th>Size</th></tr>${fp.rooms.map(r => `<tr><td>${esc(r.label)}</td><td>${esc(r.floor || "")}</td><td>${esc(r.dim_text || "")}${r.area_m2 ? ` ≈ ${(r.area_m2 * 10.76).toFixed(0)} sq ft` : ""}</td></tr>`).join("")}</table>`);
    if (fp.adjacency_evidence && fp.adjacency_evidence.length) out.push(`<p><b>Evidence:</b></p><ul class="plain">${fp.adjacency_evidence.map(e => `<li>${esc(e)}</li>`).join("")}</ul>`);
    const f = fp.flags; out.push(`<p class="hint">Flags: kitchen+family ${f.kitchen_family ? "✓" : "–"} · ground WC ${f.ground_wc ? "✓" : "–"} · en-suite ${f.ensuite ? "✓" : "–"} · utility ${f.utility ? "✓" : "–"} · studies/offices ${f.studies || 0} · receptions ${f.receptions || 0}</p>`);
  } else out.push(`<p class="hint">${an.floorplan_files && an.floorplan_files.length ? "Floorplan found but OCR could not read enough room labels." : "No floorplan image available to analyse."}</p>`);
  if (an.n_photos != null) out.push(`<p class="hint">${an.n_photos} photos downloaded &amp; embedded (${esc(an.embed_backend || "")}).</p>`);
  const sim = ev && ev.similarity;
  if (sim) out.push(`<p><b>Similarity to your reference homes:</b> ${Math.round(sim.score * 100)}% <span class="hint">(${sim.photo_cos_mean != null ? "photo cosine " + sim.photo_cos_mean : ""} ${sim.layout_match != null ? " layout match " + sim.layout_match : ""}; ${sim.n_ref_photos} ref photos, ${sim.n_ref_layouts} ref layouts)</span></p>`);
  else out.push('<p class="hint">No reference homes yet → no similarity score (add some under Reference homes).</p>');
  const v = an.vision;
  if (v && v.answers) out.push(`<p><b>Vision model:</b> ${esc(v.model || "")}</p><pre style="white-space:pre-wrap;font-size:.8rem">${esc(JSON.stringify(v.answers, null, 1))}</pre>`);
  else out.push(`<p class="hint">Vision model: ${esc((v && (v.reason || v.error)) || "off")}</p>`);
  return out.join("");
}
function hardHtml(ev) {
  return `<ul class="plain hard">${ev.hard.map(h => `<li class="${h.status === "pass" ? "p" : h.status === "unknown" ? "u" : "f"}">${h.status === "pass" ? "✓" : h.status === "unknown" ? "?" : "✗"} <b>${esc(h.key.replace("anchor:", "drive: "))}</b> — ${esc(h.detail)}</li>`).join("")}</ul>
  <p><b>Score parts</b> (weighted): ${Object.entries(ev.score_parts).map(([k, v]) => `${k} ${v}`).join(" · ")}</p>${ev.workspace_hits.length ? `<p>Workspace signals: ${ev.workspace_hits.map(esc).join(", ")}</p>` : ""}`;
}
let _map;
function mapInit(l, anchors) {
  const el = $("#map"); if (!el || typeof L === "undefined") return;
  if (l.lat == null) { el.innerHTML = '<div class="empty">No coordinates for this listing</div>'; return; }
  if (_map) { try { _map.remove(); } catch (_) {} }
  L.Icon.Default.imagePath = "/static/vendor/images/";
  const m = _map = L.map(el, {scrollWheelZoom: false}).setView([l.lat, l.lon], 13);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {maxZoom: 18, attribution: "© OpenStreetMap"}).addTo(m);
  L.marker([l.lat, l.lon]).addTo(m).bindPopup("This listing");
  const pts = [[l.lat, l.lon]];
  anchors.forEach(a => { if (a.lat != null) { L.circleMarker([a.lat, a.lon], {radius: 9, color: "#b45309", fillOpacity: .8}).addTo(m).bindPopup(esc(a.name)); pts.push([a.lat, a.lon]); } });
  m.fitBounds(pts, {padding: [30, 30], maxZoom: 14});
  setTimeout(() => m.invalidateSize(), 300);
  $$("details.sec[data-id=map]").forEach(d => d.addEventListener("toggle", () => setTimeout(() => m.invalidateSize(), 100)));
}

/* pinch-zoom viewer (pointer events: 2-finger pinch, drag, double-tap, wheel) */
function zoomViewer(host, urls, startIdx = 0) {
  let idx = startIdx;
  host.innerHTML = `<div class="zoomwrap"><div class="zoomctl"><button data-z="+" aria-label="Zoom in">+</button><button data-z="-" aria-label="Zoom out">−</button><button data-z="0" aria-label="Reset">⤢</button></div><img draggable="false"></div>${urls.length > 1 ? `<div class="btnrow"><button class="btn" data-n="-1">‹ Prev</button><span class="hint" id="zc" style="align-self:center"></span><button class="btn" data-n="1">Next ›</button></div>` : ""}`;
  const wrap = $(".zoomwrap", host), img = $("img", wrap);
  let sc = 1, tx = 0, ty = 0, fit = 1;
  const pts = new Map(); let last = null, lastTap = 0;
  const apply = () => img.style.transform = `translate(${tx}px,${ty}px) scale(${sc})`;
  const clamp = () => { const W = wrap.clientWidth, H = wrap.clientHeight, w = img.naturalWidth * sc, h = img.naturalHeight * sc;
    tx = w <= W ? (W - w) / 2 : Math.min(0, Math.max(W - w, tx)); ty = h <= H ? (H - h) / 2 : Math.min(0, Math.max(H - h, ty)); };
  const reset = () => { const W = wrap.clientWidth, H = wrap.clientHeight; fit = Math.min(W / img.naturalWidth, H / img.naturalHeight); sc = fit; clamp(); apply(); };
  const zoomAt = (f, cx, cy) => { const ns = Math.min(fit * 8, Math.max(fit, sc * f)); tx = cx - (cx - tx) * ns / sc; ty = cy - (cy - ty) * ns / sc; sc = ns; clamp(); apply(); };
  const load = () => { img.onload = reset; img.src = urls[idx]; const zc = $("#zc", host); if (zc) zc.textContent = `${idx + 1} / ${urls.length}`; };
  const rect = () => wrap.getBoundingClientRect();
  wrap.addEventListener("pointerdown", e => { if (e.target.closest(".zoomctl")) return; wrap.setPointerCapture(e.pointerId); pts.set(e.pointerId, {x: e.clientX, y: e.clientY}); last = null;
    if (pts.size === 1) { const now = Date.now(); if (now - lastTap < 300) { const r = rect(); sc > fit * 1.05 ? reset() : zoomAt(2.5 / (sc / fit), e.clientX - r.left, e.clientY - r.top); } lastTap = now; } });
  wrap.addEventListener("pointermove", e => { if (!pts.has(e.pointerId)) return; const p = pts.get(e.pointerId), r = rect();
    if (pts.size === 2) { const o = [...pts.entries()].find(([id]) => id !== e.pointerId)[1]; const d0 = Math.hypot(p.x - o.x, p.y - o.y); pts.set(e.pointerId, {x: e.clientX, y: e.clientY}); const d1 = Math.hypot(e.clientX - o.x, e.clientY - o.y);
      if (d0 > 0) zoomAt(d1 / d0, (e.clientX + o.x) / 2 - r.left, (e.clientY + o.y) / 2 - r.top); }
    else { tx += e.clientX - p.x; ty += e.clientY - p.y; pts.set(e.pointerId, {x: e.clientX, y: e.clientY}); clamp(); apply(); } });
  const up = e => { pts.delete(e.pointerId); };
  wrap.addEventListener("pointerup", up); wrap.addEventListener("pointercancel", up);
  wrap.addEventListener("wheel", e => { e.preventDefault(); const r = rect(); zoomAt(e.deltaY < 0 ? 1.2 : 1 / 1.2, e.clientX - r.left, e.clientY - r.top); }, {passive: false});
  $$(".zoomctl button", wrap).forEach(b => b.onclick = () => { const r = rect(), cx = r.width / 2, cy = r.height / 2; b.dataset.z === "+" ? zoomAt(1.5, cx, cy) : b.dataset.z === "-" ? zoomAt(1 / 1.5, cx, cy) : reset(); });
  $$("[data-n]", host).forEach(b => b.onclick = () => { idx = (idx + +b.dataset.n + urls.length) % urls.length; load(); });
  window.addEventListener("resize", () => img.naturalWidth && reset());
  load();
  return {reset};
}
function lightbox(urls, i) {
  const lb = $("#lightbox"); lb.hidden = false;
  lb.innerHTML = `<div class="lbbar"><span style="align-self:center">Pinch to zoom · swipe via ‹ ›</span><button aria-label="Close">✕</button></div><div id="lbh" style="flex:1;display:flex;flex-direction:column;min-height:0"></div>`;
  $("button", lb).onclick = () => { lb.hidden = true; lb.innerHTML = ""; };
  zoomViewer($("#lbh"), urls, i);
}

/* ------------------------------------------------------------ pages */
async function pageFeed() {
  const p = $("#page"); p.innerHTML = '<div class="empty">Loading…</div>';
  const items = await api("/notifications");
  p.innerHTML = `<div class="btnrow" style="margin:0 0 10px"><button class="btn" id="mr">Mark all read</button><button class="btn" id="pushbtn">Enable push on this device</button></div><div class="hint" id="pushmsg"></div>
   <div class="feed" style="margin-top:10px">${items.map(n => `<a class="item" href="#/listing/${n.listing_id}" style="${n.read ? "" : "border-left:4px solid var(--brand)"}"><b>${esc(n.message.title)}</b><div class="hint">${esc(n.message.body)}</div><div class="hint">${fdt(n.created)}${n.baseline ? " · first run (baseline)" : ""}</div></a>`).join("") || '<div class="empty">No notifications yet. They appear when a new listing passes every hard filter.</div>'}</div>`;
  $("#mr").onclick = async () => { await api("/notifications/read", {method: "POST"}); refreshState(); pageFeed(); };
  $("#pushbtn").onclick = enablePush; pushStatus();
  await api("/notifications/read", {method: "POST"}).catch(() => {}); refreshState();
}
async function pushStatus() {
  const m = $("#pushmsg"); if (!m) return;
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) m.textContent = ios && !standalone ? "iPhone/iPad: add this app to your Home Screen first (Share → Add to Home Screen), then open it from there to enable push (iOS 16.4+)." : "Push isn't supported in this browser" + (location.protocol === "http:" && location.hostname !== "localhost" ? " (push needs HTTPS – use Tailscale serve / Cloudflare Tunnel)." : ".");
  else if (location.protocol === "http:" && !["localhost", "127.0.0.1"].includes(location.hostname)) m.textContent = "Browsers only allow push over HTTPS. Open the dashboard through your Tailscale/Cloudflare HTTPS address to enable it.";
  else { const s = await (await navigator.serviceWorker.ready).pushManager.getSubscription(); m.textContent = s ? "✓ Push is enabled on this device." : "Push not enabled on this device."; }
}
async function enablePush() {
  try {
    const perm = await Notification.requestPermission(); if (perm !== "granted") return toast("Notification permission denied");
    const reg = await navigator.serviceWorker.ready, {key} = await api("/push/key");
    const b = key.replace(/-/g, "+").replace(/_/g, "/"), raw = atob(b + "=".repeat((4 - b.length % 4) % 4)), arr = Uint8Array.from(raw, c => c.charCodeAt(0));
    const sub = await reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: arr});
    await api("/push/subscribe", {json: sub.toJSON()}); toast("Push enabled"); pushStatus();
  } catch (e) { toast("Push failed: " + e.message, 6000); }
}

const STATE_LABEL = {ok: "working", blocked: "blocked", error: "error", disabled: "disabled", unsupported: "not yet supported", never_run: "not run yet"};
async function pageSources() {
  const p = $("#page"); await refreshState();
  p.innerHTML = `<div class="box"><h3>Source status</h3><p class="hint">Last poll: ${ago((S.poll.last || {}).finished)} · interval ${S.poll.interval} min · next ${S.poll.next_run ? fdt(S.poll.next_run) : "—"} (Europe/London)</p>
   ${S.sources.map(s => `<div class="src"><div class="h"><span class="dot ${s.state}"></span><b>${esc(s.name)}</b><span class="pill">${esc(s.type)}</span><span class="pill ${s.state === "ok" ? "ok" : s.state === "blocked" ? "bad" : s.state === "error" ? "warn" : ""}">${STATE_LABEL[s.state] || s.state}</span>${s.count != null ? `<span class="hint">${s.count} listings</span>` : ""}</div>
     <div class="hint">${esc(s.url || "")}</div>${s.message && s.state !== "ok" ? `<div style="color:var(--bad);font-size:.88rem">${esc(s.message)}${s.http_status ? " (HTTP " + s.http_status + ")" : ""}</div>` : s.message ? `<div class="hint">${esc(s.message)}</div>` : ""}
     ${(s.warnings || []).map(w => `<div class="hint">⚠ ${esc(w)}</div>`).join("")}<div class="hint">${s.last_run ? "last run " + fdt(s.last_run) : ""}</div></div>`).join("")}
   <div class="btnrow"><button class="btn pri" id="pn">⟳ Poll now</button></div><div id="plog" class="hint" style="white-space:pre-wrap;margin-top:8px">${(S.poll.progress || []).map(esc).join("\n")}</div></div>
   <div class="box"><h3>Engine</h3><ul class="plain"><li>Floorplan OCR (RapidOCR/ONNX, offline): ${S.ocr ? "✓ available" : "✗ not installed"}</li><li>Photo embeddings: ${esc(S.embed.backend)}${S.embed.error ? " – " + esc(S.embed.error) : ""}</li><li>Vision model: ${S.vision ? "✓ enabled" : "off (no API key) — Settings → Vision model"}</li></ul></div>`;
  $("#pn").onclick = async () => { const r = await api("/poll", {method: "POST"}); toast(r.started ? "Polling started…" : "Already running"); setTimeout(pageSources, 4000); };
}

async function pageRefs() {
  const p = $("#page"); const d = await api("/refs"); const ch = d.current_home;
  const rooms = (ch.rooms || []);
  const roomTbl = fl => `<table class="t"><tr><th>${fl === "ground" ? "Ground floor" : "First floor"}</th><th>Size</th><th></th></tr>${rooms.filter(r => r.floor === fl).map(r => `<tr><td>${esc(r.name)}</td><td>${esc(r.dim || "")}${r.ft ? `<br><small>${esc(r.ft)}</small>` : ""}</td><td><small>${esc(r.note || "")}</small></td></tr>`).join("")}</table>`;
  p.innerHTML = `<div class="box"><h3>🏡 Current home — ${esc(ch.address || "baseline")}</h3><p class="hint">${esc(ch.description)}</p>
    ${ch.floorplan ? `<div class="two"><div><img src="/media/refs/${esc(ch.floorplan)}" alt="Current home floorplan" style="width:100%;border-radius:8px;border:1px solid var(--line,#ddd)" class="zoomable"><p class="hint">Gross internal area <b>${ch.sqft} sq ft / ${ch.m2} m²</b> (ground ${(ch.floor_areas_sqft || {}).ground}, first ${(ch.floor_areas_sqft || {}).first}). Floorplan source: see current-home config${ch.source_url ? ` · <a href="${esc(ch.source_url)}" target="_blank" rel="noopener">Rightmove record ↗</a>` : ""}.</p></div><div>${roomTbl("ground")}${roomTbl("first")}</div></div>
    <div class="gal" style="display:flex;gap:6px;overflow-x:auto">${(ch.photos || []).map(f => `<img src="/media/refs/${esc(f)}" loading="lazy" style="height:90px;border-radius:6px">`).join("")}</div>` : ""}
    <div class="frm"><label class="f">Floor area (sq ft) — edit when known<input id="ch_sqft" type="number" value="${ch.sqft ?? ""}"></label><label class="f">Reception rooms<input id="ch_rec" type="number" value="${ch.receptions}"></label></div>
    <div class="btnrow"><button class="btn" id="ch_save">Save baseline</button></div></div>
   <div class="box"><h3>Add reference homes</h3><p class="hint">Photos or floorplans of homes you like (or listing URLs). New listings are scored by similarity to these (photo embeddings: ${esc(d.embed.backend)}; layout flags from floorplan OCR).</p>
    <div class="frm"><label class="f full">Upload image(s)<input type="file" id="rf" accept="image/*" multiple></label><label class="f">Label (optional)<input id="rl"></label>
    <label class="f">Listing URL (Rightmove / OnTheMarket / Purplebricks / agent)<input id="ru" type="url" placeholder="https://…"></label></div>
    <div class="btnrow"><button class="btn pri" id="rup">Upload</button><button class="btn pri" id="rurl">Add URL</button><button class="btn" id="rscan">Rescan folder</button></div>
    <p class="hint">Watched folder on the host: <code>${esc(d.folder)}</code> — drop images or <code>.url/.txt</code> files containing a listing link; they're picked up on every poll or via “Rescan”.</p></div>
   <div class="box"><h3>References (${d.items.filter(r => r.kind !== "current_home").length})</h3><div class="refgrid">${d.items.filter(r => r.kind !== "current_home").map(r => `<div class="r"><div class="im" style="background-image:url('${esc(r.thumb || "")}')"></div><div class="tx"><b>${esc(r.label)}</b><div class="hint">${esc(r.kind)}${r.features.layout ? " · " + (r.features.layout.n_labels || 0) + " room labels" : ""}${r.features.n_photos ? " · " + r.features.n_photos + " photos" : ""}</div>${r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">source ↗</a>` : ""}<br><a href="#" data-del="${r.id}">remove</a></div></div>`).join("") || '<div class="empty" style="grid-column:1/-1">None yet. More reference images from you will go here.</div>'}</div></div>`;
  $("#ch_save").onclick = async () => { await api("/refs/current-home", {method: "PUT", json: {sqft: $("#ch_sqft").value ? +$("#ch_sqft").value : null, receptions: +$("#ch_rec").value}}); toast("Saved"); };
  $("#rup").onclick = async () => { const fs = $("#rf").files; if (!fs.length) return toast("Choose a file"); for (const f of fs) { const fd = new FormData(); fd.append("file", f); fd.append("label", $("#rl").value); await fetch("/api/refs/upload", {method: "POST", body: fd}); } toast("Uploaded"); pageRefs(); };
  $("#rurl").onclick = async e => { const u = $("#ru").value; if (!u) return; e.target.textContent = "Reading…"; try { await api("/refs/url", {json: {url: u, label: $("#rl").value}}); toast("Added"); } catch (er) { toast("Failed: " + er.message, 6000); } pageRefs(); };
  $("#rscan").onclick = async () => { const r = await api("/refs/rescan", {method: "POST"}); toast(`${r.added} new`); pageRefs(); };
  $$("[data-del]").forEach(a => a.onclick = async e => { e.preventDefault(); await api("/refs/" + a.dataset.del, {method: "DELETE"}); pageRefs(); });
}

/* ---- settings (generic data-path binding) ---- */
const getp = (o, p) => p.split(".").reduce((a, k) => a == null ? a : a[k], o);
const setp = (o, p, v) => { const ks = p.split("."); const l = ks.pop(); const t = ks.reduce((a, k) => a[k], o); t[l] = v; };
function inp(label, path, type = "text", extra = "") {
  const v = getp(CFG, path);
  if (type === "bool") return `<label class="chkb"><input type="checkbox" data-p="${path}" data-t="bool" ${v ? "checked" : ""}> ${label}</label>`;
  if (type === "list") return `<label class="f full">${label}<textarea data-p="${path}" data-t="list">${esc((v || []).join(", "))}</textarea></label>`;
  if (type === "json") return `<label class="f full">${label}<textarea data-p="${path}" data-t="json" rows="6" style="font-family:monospace;font-size:.8rem">${esc(JSON.stringify(v, null, 1))}</textarea></label>`;
  if (type === "num") return `<label class="f">${label}<input type="number" step="any" data-p="${path}" data-t="num" value="${v ?? ""}" ${extra}></label>`;
  if (type.startsWith("sel:")) return `<label class="f">${label}<select data-p="${path}" data-t="str">${type.slice(4).split("|").map(o => `<option ${o === v ? "selected" : ""}>${o}</option>`).join("")}</select></label>`;
  return `<label class="f">${label}<input type="${type === "password" ? "password" : "text"}" data-p="${path}" data-t="str" value="${esc(v ?? "")}" ${extra}></label>`;
}
function collect() {
  $$("[data-p]").forEach(el => { const t = el.dataset.t; let v;
    if (t === "bool") v = el.checked; else if (t === "num") v = el.value === "" ? null : +el.value;
    else if (t === "list") v = el.value.split(/[,\n]/).map(s => s.trim()).filter(Boolean);
    else if (t === "json") { try { v = JSON.parse(el.value); } catch (e) { throw new Error(el.dataset.p + ": invalid JSON"); } } else v = el.value;
    setp(CFG, el.dataset.p, v); });
}
async function pageSettings() {
  const p = $("#page"); CFG = await api("/config"); await refreshState();
  const c = CFG.criteria;
  p.innerHTML = `<div class="box"><b>Defaults are PLACEHOLDERS</b> where marked below. Changes re-score everything immediately. <span class="hint">Everything is stored in SQLite.</span>
    ${(S.placeholders || []).length ? `<ul class="plain hint">${S.placeholders.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}</div>
   <details class="sec" open><summary>💷 Budget & property</summary><div class="in"><div class="frm">
     ${inp("Min price (£)", "criteria.price_min", "num")}${inp("Max price (£)", "criteria.price_max", "num")}${inp("Stretch ceiling (£) – 'Just over budget' up to here", "criteria.price_stretch_max", "num")}
     ${inp("Min bedrooms (HARD)", "criteria.bedrooms_min", "num")}${inp("Min bathrooms (0 = ignore)", "criteria.bathrooms_min", "num")}${inp("Tenure", "criteria.tenure", "sel:any|freehold|leasehold")}
     ${inp("Property types allowed (comma-separated; empty = any)", "criteria.property_types", "list")}${inp("Property types excluded", "criteria.exclude_property_types", "list")}
     ${inp("Must-have keywords", "criteria.must_keywords", "list")}${inp("Exclude keywords", "criteria.exclude_keywords", "list")}
     ${inp("Notify for ‘just over budget’ matches", "criteria.notify_just_over_budget", "bool")}</div></div></details>
   <details class="sec" open><summary>⭐ Top matches (curated view & notifications)</summary><div class="in"><p class="hint">Only listings that pass every hard filter, have no contradicted must-have <b>and</b> reach the score cut-off appear in the default views and trigger notifications. Everything else is stored but hidden.</p><div class="frm">
     ${inp("Top matches: min score (0–100)", "criteria.top_matches.min_score", "num")}${inp("Top matches: max shown", "criteria.top_matches.max_count", "num")}
     ${inp("Just over budget: min score", "criteria.top_matches.just_over_min_score", "num")}${inp("Just over budget: max shown", "criteria.top_matches.just_over_max_count", "num")}
     ${inp("Admin: show ‘All fetched’ & ‘Needs review’ tabs", "ui.show_all_fetched", "bool")}</div></div></details>
   <details class="sec" open><summary>📍 Anchors (hard: max drive to EACH)</summary><div class="in">${c.anchors.map((a, i) => `<div class="box"><div class="frm">
      ${inp("Name", `criteria.anchors.${i}.name`)}${inp("Address / street", `criteria.anchors.${i}.address`)}
      ${inp("Latitude", `criteria.anchors.${i}.lat`, "num")}${inp("Longitude", `criteria.anchors.${i}.lon`, "num")}
      ${inp("Max drive (min)", `criteria.anchors.${i}.max_drive_min`, "num")}${inp("Max straight-line (km)", `criteria.anchors.${i}.max_straight_km`, "num")}${inp("Weight (ranking)", `criteria.anchors.${i}.weight`, "num")}</div>
      <div class="btnrow"><button class="btn" data-geo="${i}">📍 Geocode address</button><button class="btn danger" data-delanchor="${i}">Remove</button></div></div>`).join("")}
     <button class="btn" id="addanchor">+ Add anchor</button><p class="hint">Coordinates are the street midpoint — type an exact house address (with postcode) and press Geocode for precision (Nominatim/postcodes.io, cached).</p></div></details>
   <details class="sec"><summary>🗺️ Search area</summary><div class="in"><div class="frm">${inp("Centre lat", "criteria.search_area.lat", "num")}${inp("Centre lon", "criteria.search_area.lon", "num")}${inp("Radius (miles)", "criteria.search_area.radius_miles", "num")}${inp("Search locations (Rightmove/OnTheMarket/Purplebricks)", "criteria.search_locations", "list")}</div></div></details>
   <details class="sec" open><summary>✅ Checklist (must / nice / off)</summary><div class="in"><div class="frm">${Object.entries(c.checklist).map(([k, v]) => `${inp(esc(v.label), `criteria.checklist.${k}.mode`, "sel:must|nice|off")}${inp("Weight", `criteria.checklist.${k}.weight`, "num")}`).join("")}
     ${inp("Floorplan can mark ‘contradicted’ (if no utility/kitchen-family room seen)", "criteria.floorplan_can_contradict", "bool")}</div></div></details>
   <details class="sec"><summary>📊 Scoring</summary><div class="in"><div class="frm">${Object.keys(c.score_weights).map(k => inp(k, `criteria.score_weights.${k}`, "num")).join("")}${inp("Workspace boosts (regex → points)", "criteria.workspace_boosts", "json")}</div><p class="hint">Train station weight is deliberately tiny (nice-to-have only).</p></div></details>
   <details class="sec"><summary>📡 Sources</summary><div class="in">${CFG.sources.map((s, i) => `<div class="src"><div class="h"><label class="chkb"><input type="checkbox" data-p="sources.${i}.enabled" data-t="bool" ${s.enabled ? "checked" : ""}> <b>${esc(s.name)}</b></label><span class="pill">${esc(s.type)}</span><button class="btn danger" data-delsrc="${esc(s.id)}" style="min-height:36px">remove</button></div><div class="hint">${esc(s.url || "")}</div></div>`).join("")}
     <div class="box"><b>Add an estate agent</b><div class="frm"><label class="f full">Agent website / for-sale search URL<input id="nsurl" placeholder="https://www.example-agents.co.uk"></label><label class="f">Name<input id="nsname"></label></div><button class="btn pri" id="addsrc">Detect platform &amp; add</button></div></div></details>
   <details class="sec"><summary>⏱ Polling</summary><div class="in"><div class="frm">${inp("Poll automatically", "poll.enabled", "bool")}${inp("Interval (minutes, 30–60 recommended)", "poll.interval_minutes", "num")}${inp("Delay between requests per host (s)", "poll.request_delay_s", "num")}${inp("Max result pages per search", "poll.max_pages", "num")}${inp("Max detail pages per cycle", "poll.max_detail_fetches_per_cycle", "num")}</div></div></details>
   <details class="sec"><summary>🔔 Notifications (all OFF by default)</summary><div class="in"><div class="frm">
     ${inp("Desktop popup on the host machine", "notifiers.desktop.enabled", "bool")}<div></div>
     ${inp("Webhook", "notifiers.webhook.enabled", "bool")}${inp("Webhook URL", "notifiers.webhook.url")}${inp("Format", "notifiers.webhook.format", "sel:json|slack|discord|ntfy")}<div></div>
     ${inp("Email (SMTP)", "notifiers.email.enabled", "bool")}${inp("SMTP host", "notifiers.email.smtp_host")}${inp("SMTP port", "notifiers.email.smtp_port", "num")}${inp("Username", "notifiers.email.username")}${inp("Password env var name", "notifiers.email.password_env")}${inp("From", "notifiers.email.from_addr")}${inp("To", "notifiers.email.to_addr")}
     ${inp("Also send external notifications for the first (baseline) batch", "notifiers.notify_baseline", "bool")}</div>
     <div class="btnrow"><button class="btn" id="ntest">Send test notification</button><button class="btn" id="npush">Enable web push on this device</button></div><p class="hint" id="pushmsg"></p>
     <p class="hint">In-app badge/feed is always on. See README for enabling each notifier. Web push needs HTTPS (Tailscale serve / Cloudflare Tunnel); iPhone/iPad need Add to Home Screen + iOS 16.4+.</p></div></details>
   <details class="sec"><summary>👁 Vision model (optional, needs API key)</summary><div class="in"><div class="frm">${inp("Enabled", "vision.enabled", "bool")}${inp("Provider", "vision.provider", "sel:openai|anthropic")}${inp("Base URL (OpenAI-compatible)", "vision.base_url")}${inp("Model", "vision.model")}${inp("API key (or set env PROPDASH_VISION_API_KEY)", "vision.api_key", "password")}${inp("Max images per listing", "vision.max_images", "num")}</div></div></details>
   <div class="btnrow" style="position:sticky;bottom:calc(var(--bot) + var(--sab) + 8px)"><button class="btn pri" id="save">💾 Save settings</button><button class="btn danger" id="reset">Reset to defaults</button></div>`;
  $("#save").onclick = async () => { try { collect(); await api("/config", {method: "PUT", json: CFG}); toast("Saved – re-scoring listings…"); } catch (e) { toast(e.message, 6000); } };
  $("#reset").onclick = async () => { if (confirm("Reset ALL settings to defaults?")) { await api("/config/reset", {method: "POST"}); pageSettings(); } };
  $$("[data-geo]").forEach(b => b.onclick = async () => { collect(); const a = CFG.criteria.anchors[+b.dataset.geo]; try { const g = await api("/geocode", {json: {address: a.address}}); a.lat = g.lat; a.lon = g.lon; toast(`Found: ${g.lat.toFixed(5)}, ${g.lon.toFixed(5)} (${g.source})`); } catch (e) { toast("Not found: " + e.message); } await api("/config", {method: "PUT", json: CFG}); pageSettings(); });
  $$("[data-delanchor]").forEach(b => b.onclick = async () => { collect(); CFG.criteria.anchors.splice(+b.dataset.delanchor, 1); await api("/config", {method: "PUT", json: CFG}); pageSettings(); });
  $("#addanchor").onclick = async () => { collect(); CFG.criteria.anchors.push({name: "New anchor", address: "", lat: null, lon: null, max_drive_min: 25, max_straight_km: 20, weight: 1}); await api("/config", {method: "PUT", json: CFG}); pageSettings(); };
  $$("[data-delsrc]").forEach(b => b.onclick = async () => { if (confirm("Remove this source?")) { await api("/sources/" + b.dataset.delsrc, {method: "DELETE"}); pageSettings(); } });
  $("#addsrc").onclick = async e => { const u = $("#nsurl").value; if (!u) return; collect(); await api("/config", {method: "PUT", json: CFG}); e.target.textContent = "Detecting…"; const r = await api("/sources", {json: {url: u, name: $("#nsname").value}}); toast(`Added as “${r.type}” adapter. ${r.note}`, 6000); pageSettings(); };
  $("#ntest").onclick = async () => { collect(); await api("/config", {method: "PUT", json: CFG}); const r = await api("/notify/test", {method: "POST"}); toast(JSON.stringify(r.results.length ? r.results : "No external notifier enabled") + " push:" + r.web_push_sent, 7000); };
  $("#npush").onclick = enablePush; pushStatus();
}

/* ------------------------------------------------------------ state, init */
async function refreshState() {
  try { S = await api("/state"); } catch (e) { return; }
  const c = $("#pollchip"); c.className = "chip" + (S.poll.running ? " run" : "");
  c.textContent = S.poll.running ? "polling…" : "updated " + ago((S.poll.last || {}).finished);
  renderNav(parse());
}
$("#refresh").onclick = async () => { const r = await api("/poll", {method: "POST"}); toast(r.started ? "Checking all sources…" : "Already running"); refreshState(); };
setInterval(() => { if (!document.hidden) { const was = S && S.poll.running; refreshState().then(() => { if (was && !S.poll.running) route(); }); } }, 15000);
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
Fav.sync(true);
refreshState().then(route);
