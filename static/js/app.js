/* AirGuard AI — front-end orchestration (MapLibre GL + Chart.js) */
"use strict";

const $ = (s) => document.querySelector(s);
const api = (p) => fetch(p).then((r) => r.json());

const BANDS = [
  [50, "Good", "#00b894"], [100, "Satisfactory", "#a3c853"],
  [200, "Moderate", "#f4d03f"], [300, "Poor", "#f39c12"],
  [400, "Very Poor", "#e74c3c"], [9999, "Severe", "#7e0023"],
];
const catOf = (a) => BANDS.find(([u]) => a <= u) || BANDS[5];

const state = {
  meta: null, cities: [], cityById: {}, current: null, loaded: {},
  fcChart: null, trajOn: false, gridOn: false, trajAnim: null, endMarker: null,
};

/* ── Map (MapLibre GL, vector dark style, pitched) ────────── */
const map = new maplibregl.Map({
  container: "map",
  style: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
  center: [80.5, 22.8], zoom: 4.3, pitch: 42, bearing: -8,
  attributionControl: false, maxZoom: 12, minZoom: 3,
});
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");

const fc = (features) => ({ type: "FeatureCollection", features });

function whenStyleReady(fn) {
  if (map.isStyleLoaded()) { fn(); return; }
  let done = false;
  const run = () => { if (!done && map.isStyleLoaded()) { done = true; fn(); } };
  map.on("load", run);
  map.on("styledata", run);
}

/* ── Init ─────────────────────────────────────────────────── */
async function init() {
  let meta, cityData, fireData;
  try {
    [meta, cityData, fireData] = await Promise.all([
      api("/api/meta"), api("/api/cities"), api("/api/fires"),
    ]);
  } catch (e) { console.error("[init] fetch failed", e); return; }

  state.meta = meta;
  state.cities = cityData.cities;
  cityData.cities.forEach((c) => (state.cityById[c.id] = c));

  // Chrome (top bar, KPIs, provenance) renders immediately — never gated on
  // the basemap, so the UI is responsive even if vector tiles are slow.
  renderSources(meta, cityData, fireData);
  renderKpis(cityData, fireData);
  $("#loading").classList.add("hidden");

  // Map data layers attach as soon as the vector style is ready.
  whenStyleReady(() => {
    addCityLayers(cityData.cities);
    addFireLayers(fireData.fires);
    addWorstMarker(cityData.worst);
  });
}

function renderSources(meta, cityData, fireData) {
  const pill = (label, live, detail) =>
    `<div class="src-pill"><span class="src-dot ${live ? "live" : "seed"}"></span>
     <b>${label}</b> ${live ? (detail || "live") : "seeded"}</div>`;
  $("#sources").innerHTML =
    pill("LLM", meta.has_llm, meta.provider.replace(/\(.*\)/, "").trim()) +
    pill("AQI · OpenAQ", cityData.live) +
    pill("Fires · NASA FIRMS", fireData.live) +
    pill("Forecast · Open-Meteo", true);
}

function renderKpis(data, fireData) {
  $("#worstCity").innerHTML =
    `${data.worst.name}<span class="worst-aqi" style="color:${data.worst.color}">${data.worst.aqi}</span>`;
  const avg = $("#natAvg");
  avg.classList.toggle("bad", data.national_avg > 200);
  countUp(avg, data.national_avg);
  countUp($("#fireCount"), fireData.fires.length);
}

function countUp(el, to) {
  const start = performance.now(), dur = 900;
  const step = (t) => {
    const k = Math.min(1, (t - start) / dur);
    el.textContent = Math.round(to * (1 - Math.pow(1 - k, 3)));
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/* ── City layers ──────────────────────────────────────────── */
function addCityLayers(cities) {
  const features = cities.map((c) => ({
    type: "Feature", geometry: { type: "Point", coordinates: [c.lon, c.lat] },
    properties: { id: c.id, name: c.name, aqi: c.aqi, color: c.color, category: c.category },
  }));
  map.addSource("cities", { type: "geojson", data: fc(features) });

  map.addLayer({
    id: "city-heat", type: "heatmap", source: "cities", maxzoom: 9,
    paint: {
      "heatmap-weight": ["interpolate", ["linear"], ["get", "aqi"], 0, 0, 400, 1],
      "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 4, 0.7, 9, 2.4],
      "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 4, 34, 9, 90],
      "heatmap-opacity": 0.45,
      "heatmap-color": ["interpolate", ["linear"], ["heatmap-density"],
        0, "rgba(0,0,0,0)", 0.2, "#00b894", 0.4, "#a3c853",
        0.6, "#f4d03f", 0.8, "#f39c12", 1, "#e74c3c"],
    },
  });
  map.addLayer({
    id: "city-glow", type: "circle", source: "cities",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["get", "aqi"], 30, 16, 500, 50],
      "circle-color": ["get", "color"], "circle-blur": 1, "circle-opacity": 0.28,
    },
  });
  map.addLayer({
    id: "city-core", type: "circle", source: "cities",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["get", "aqi"], 30, 6, 500, 26],
      "circle-color": ["get", "color"], "circle-opacity": 0.95,
      "circle-stroke-width": 1.4, "circle-stroke-color": "rgba(255,255,255,.6)",
    },
  });
  map.addLayer({
    id: "city-label", type: "symbol", source: "cities",
    layout: { "text-field": ["get", "name"], "text-size": 11,
      "text-font": ["Open Sans Semibold"], "text-offset": [0, 1.4],
      "text-anchor": "top", "text-optional": true },
    paint: { "text-color": "#dbe4f0", "text-halo-color": "#05080f", "text-halo-width": 1.4 },
  });

  const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 16 });
  map.on("mousemove", "city-core", (e) => {
    map.getCanvas().style.cursor = "pointer";
    const p = e.features[0].properties;
    popup.setLngLat(e.features[0].geometry.coordinates)
      .setHTML(`<b>${p.name}</b> · AQI <span class="pop-aqi" style="color:${p.color}">${p.aqi}</span> · ${p.category}`)
      .addTo(map);
  });
  map.on("mouseleave", "city-core", () => { map.getCanvas().style.cursor = ""; popup.remove(); });
  map.on("click", "city-core", (e) => openCity(state.cityById[e.features[0].properties.id]));
}

function addFireLayers(fires) {
  const features = fires.map((f) => ({
    type: "Feature", geometry: { type: "Point", coordinates: [f.lon, f.lat] },
    properties: { frp: f.frp },
  }));
  map.addSource("fires", { type: "geojson", data: fc(features) });
  map.addLayer({
    id: "fires-glow", type: "circle", source: "fires",
    paint: { "circle-radius": 7, "circle-color": "#ff8c3a", "circle-blur": 1, "circle-opacity": 0.4 },
  }, "city-glow");
  map.addLayer({
    id: "fires-core", type: "circle", source: "fires",
    paint: { "circle-radius": 2.6, "circle-color": "#ffb454", "circle-opacity": 0.95 },
  }, "city-glow");
}

function addWorstMarker(worst) {
  const el = document.createElement("div");
  el.className = "worst-marker";
  el.title = `${worst.name} — worst AQI now`;
  el.onclick = () => openCity(state.cityById[worst.id]);
  new maplibregl.Marker({ element: el }).setLngLat([worst.lon, worst.lat]).addTo(map);
}

/* ── Panel ────────────────────────────────────────────────── */
function openCity(c) {
  if (!c) return;
  state.current = c; state.loaded = {};
  clearTraj();
  map.flyTo({ center: [c.lon, c.lat], zoom: 6.4, pitch: 52, duration: 1300, essential: true });
  $("#panel").classList.remove("hidden");
  $("#legend").classList.add("shift");
  $("#pCity").textContent = c.name;
  $("#pSub").innerHTML =
    `AQI <b class="mono" style="color:${c.color}">${c.aqi}</b> · ${c.category}
     · PM2.5 ${c.pm25} µg/m³ ${c.live ? "" : "<span style='color:var(--warn)'>· seeded</span>"}`;
  drawGauge(c.aqi, c.color);
  setTab("analysis");
}
function closeCity() {
  $("#panel").classList.add("hidden");
  $("#legend").classList.remove("shift");
  clearTraj();
}

function drawGauge(aqi, color) {
  const pct = Math.min(1, aqi / 500), R = 50, C = 2 * Math.PI * R;
  $("#gauge").innerHTML = `
    <circle cx="60" cy="60" r="${R}" fill="none" stroke="rgba(255,255,255,.08)" stroke-width="9"/>
    <circle cx="60" cy="60" r="${R}" fill="none" stroke="${color}" stroke-width="9"
      stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${C}"
      transform="rotate(-90 60 60)" style="transition:stroke-dashoffset 1.1s cubic-bezier(.16,.84,.28,1);filter:drop-shadow(0 0 6px ${color})" id="gaugeArc"/>
    <text x="60" y="60" text-anchor="middle" class="gauge-val" fill="${color}">${aqi}</text>
    <text x="60" y="77" text-anchor="middle" class="gauge-lbl">AQI</text>`;
  requestAnimationFrame(() => { const a = $("#gaugeArc"); if (a) a.style.strokeDashoffset = C * (1 - pct); });
}

function setTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".pane").forEach((p) => p.classList.toggle("active", p.dataset.pane === name));
  if (state.loaded[name]) return;
  state.loaded[name] = true;
  ({ analysis: loadAnalysis, forecast: loadForecast,
     enforcement: loadEnforcement, citizen: loadCitizen }[name])();
}

async function loadAnalysis() {
  const id = state.current.id;
  $("#bars").innerHTML = ""; $("#explain").textContent = "Analysing source signals…";
  const d = await api(`/api/city/${id}/analysis`);
  if (state.current.id !== id) return;
  $("#attrConf").textContent = `${d.confidence}% confidence`;
  $("#windRow").innerHTML =
    chip("Wind", `${d.wind.direction} · ${d.wind.speed_kmh} km/h`) +
    chip("Upwind fires", d.upwind_fire_count) +
    chip("Season", d.season.replace("_", "-"));
  $("#bars").innerHTML = d.bars.map((b) => `
    <div class="bar-item">
      <div class="bar-top"><span>${b.label}</span><b style="color:${b.color}">${b.pct}%</b></div>
      <div class="bar-track"><div class="bar-fill" data-w="${b.pct}" style="background:${b.color};color:${b.color}"></div></div>
    </div>`).join("");
  requestAnimationFrame(() => document.querySelectorAll("#bars .bar-fill")
    .forEach((el) => { el.style.width = el.dataset.w + "%"; }));
  $("#explain").textContent = d.explanation;
  $("#attrProvenance").innerHTML = provTags([
    d.llm_used ? `AI · ${d.provider}` : "AI · templated fallback",
    "anchored to seasonal apportionment", "live wind + satellite fires"]);
}

async function loadForecast() {
  const id = state.current.id;
  $("#fcWarn").textContent = "Loading 72-hour forecast…";
  const d = await api(`/api/city/${id}/forecast`);
  if (state.current.id !== id) return;
  const f = d.forecast, sk = d.skill;
  $("#skillStat").innerHTML = `
    <div class="lbl">FORECAST SKILL · RMSE vs persistence (${Math.round(sk.n_hours / 24)} days)</div>
    <span class="big">−${sk.improvement_pct}%</span>
    <div class="sub">AirGuard RMSE <b>${sk.rmse_model}</b> vs persistence <b>${sk.rmse_persistence}</b>
      ${sk.live ? "· live data" : "· representative"}</div>`;
  const step = 2, labels = [], pts = [];
  for (let i = 0; i < f.aqi.length; i += step) {
    labels.push(f.time[i].slice(5, 16).replace("T", " ")); pts.push(f.aqi[i]);
  }
  const peak = Math.max(...f.aqi.slice(0, 48)), pIdx = f.aqi.slice(0, 48).indexOf(peak);
  const pTime = f.time[pIdx] ? f.time[pIdx].slice(5, 16).replace("T", " ") : "";
  const pCat = catOf(peak);
  $("#fcWarn").innerHTML = `<b>${pCat[1]} air ahead.</b> Forecast peaks near <b>${peak} AQI</b> around ${pTime}. ` +
    (peak > 300 ? "Cross-300 threshold likely — issue closure/health advisory now, hours ahead of the spike."
      : peak > 200 ? "Pre-position ward-level advisories for schools, hospitals and outdoor workers."
      : "Air quality manageable across the window; routine monitoring advised.");
  renderChart(labels, pts);
}

function renderChart(labels, data) {
  const ctx = $("#fcChart").getContext("2d");
  const g = ctx.createLinearGradient(0, 0, 0, 180);
  g.addColorStop(0, "rgba(47,230,207,.4)"); g.addColorStop(1, "rgba(47,230,207,0)");
  if (state.fcChart) state.fcChart.destroy();
  state.fcChart = new Chart(ctx, {
    type: "line",
    data: { labels, datasets: [{ data, borderColor: "#2fe6cf", borderWidth: 2.2,
      backgroundColor: g, fill: true, pointRadius: 0, tension: 0.35 }] },
    options: { plugins: { legend: { display: false } }, animation: { duration: 800 },
      scales: {
        x: { ticks: { color: "#7e8aa3", maxTicksLimit: 6, font: { size: 9 } }, grid: { color: "rgba(255,255,255,.04)" } },
        y: { ticks: { color: "#7e8aa3", font: { size: 10 } }, grid: { color: "rgba(255,255,255,.04)" } } } },
  });
}

async function loadEnforcement() {
  const id = state.current.id;
  $("#enfList").innerHTML = ""; $("#enfSummary").textContent = "Generating enforcement plan…";
  const d = await api(`/api/city/${id}/enforcement`);
  if (state.current.id !== id) return;
  $("#enfSummary").textContent = d.summary;
  $("#enfList").innerHTML = d.actions.map((a) => `
    <li><span class="enf-zone">${a.zone}</span> <span class="enf-insp">${a.inspectors} inspectors</span>
      <div class="enf-target">${a.target}</div><div class="enf-why">${a.rationale}</div></li>`).join("");
  $("#enfProvenance").innerHTML = provTags([
    d.llm_used ? `AI · ${d.provider}` : "AI · templated fallback", "derived from source attribution"]);
}

const LANGS = [["hi", "हिन्दी"], ["en", "English"], ["ta", "தமிழ்"],
  ["kn", "ಕನ್ನಡ"], ["mr", "मराठी"], ["te", "తెలుగు"]];
function loadCitizen() {
  $("#langRow").innerHTML = LANGS.map(([c, n], i) =>
    `<button class="lang-btn ${i === 0 ? "active" : ""}" data-lang="${c}">${n}</button>`).join("");
  document.querySelectorAll(".lang-btn").forEach((b) => b.onclick = () => {
    document.querySelectorAll(".lang-btn").forEach((x) => x.classList.remove("active"));
    b.classList.add("active"); fetchAlert(b.dataset.lang);
  });
  fetchAlert("hi");
}
async function fetchAlert(lang) {
  const id = state.current.id;
  $("#alertBox").textContent = "Generating advisory…";
  const d = await api(`/api/city/${id}/citizen?lang=${lang}`);
  if (state.current.id !== id) return;
  $("#alertBox").textContent = d.text;
  $("#citProvenance").innerHTML = provTags([
    d.llm_used ? `AI · ${d.provider}` : "AI · templated fallback", `${d.language} · WhatsApp-ready`]);
}

/* ── Wind back-trajectory (animated ant-trail) ────────────── */
const DASHES = [[0, 4, 3], [0.5, 4, 2.5], [1, 4, 2], [1.5, 4, 1.5], [2, 4, 1], [2.5, 4, 0.5],
  [3, 4, 0], [0, 0.5, 3, 3.5], [0, 1, 3, 3], [0, 1.5, 3, 2.5], [0, 2, 3, 2], [0, 2.5, 3, 1.5], [0, 3, 3, 1], [0, 3.5, 3, 0.5]];
async function toggleTraj() {
  if (state.trajOn) { clearTraj(); return; }
  const id = state.current.id;
  const d = await api(`/api/city/${id}/backtrajectory`);
  if (state.current.id !== id) return;
  const coords = d.points.map((p) => [p[1], p[0]]);  // [lat,lon] -> [lon,lat]
  const line = fc([{ type: "Feature", geometry: { type: "LineString", coordinates: coords } }]);
  map.addSource("traj", { type: "geojson", data: line });
  map.addLayer({ id: "traj-glow", type: "line", source: "traj",
    paint: { "line-color": "#2fe6cf", "line-width": 7, "line-blur": 4, "line-opacity": 0.35 } });
  map.addLayer({ id: "traj-line", type: "line", source: "traj",
    paint: { "line-color": "#2fe6cf", "line-width": 2.6, "line-dasharray": [0, 4, 3] } });
  const end = coords[coords.length - 1];
  const el = document.createElement("div");
  el.className = "worst-marker"; el.style.background = "rgba(47,230,207,.95)";
  el.style.boxShadow = "0 0 12px rgba(47,230,207,.9)";
  state.endMarker = new maplibregl.Marker({ element: el }).setLngLat(end)
    .setPopup(new maplibregl.Popup({ offset: 14, closeButton: false })
      .setHTML(`Air arriving from <b>${d.from_direction}</b> · ${d.reach_km} km upwind`)).addTo(map);
  state.endMarker.togglePopup();
  let i = 0, last = 0;
  const anim = (t) => {
    if (t - last > 70) { i = (i + 1) % DASHES.length; map.setPaintProperty("traj-line", "line-dasharray", DASHES[i]); last = t; }
    state.trajAnim = requestAnimationFrame(anim);
  };
  state.trajAnim = requestAnimationFrame(anim);
  state.trajOn = true;
  $("#trajBtn").classList.add("on"); $("#trajBtn").textContent = "Hide wind back-trajectory";
}
function clearTraj() {
  if (state.trajAnim) cancelAnimationFrame(state.trajAnim);
  if (state.endMarker) { state.endMarker.remove(); state.endMarker = null; }
  ["traj-line", "traj-glow"].forEach((l) => { if (map.getLayer(l)) map.removeLayer(l); });
  if (map.getSource("traj")) map.removeSource("traj");
  state.trajOn = false;
  const b = $("#trajBtn");
  if (b) { b.classList.remove("on"); b.textContent = "Show wind back-trajectory"; }
}

/* ── National brief ───────────────────────────────────────── */
async function openBrief() {
  $("#modal").classList.remove("hidden");
  $("#briefText").textContent = "Generating national morning brief…";
  $("#briefTop").innerHTML = ""; $("#briefMeta").textContent = "";
  const d = await api("/api/national-brief");
  $("#briefMeta").innerHTML = `National average AQI <b>${d.national_avg}</b> · ${new Date().toDateString()}`;
  $("#briefText").textContent = d.text;
  $("#briefTop").innerHTML = d.top.map((c) =>
    `<div class="row"><span class="swatch" style="background:${c.color}">${c.aqi}</span>
     <b>${c.name}</b> <span style="color:var(--muted)">${c.category}</span></div>`).join("");
  $("#briefProvenance").innerHTML = provTags([
    d.llm_used ? `AI · ${d.provider}` : "AI · templated fallback", "top 5 of 20 cities"]);
}

/* ── Validation ───────────────────────────────────────────── */
async function openValidation() {
  $("#valModal").classList.remove("hidden");
  $("#valBars").innerHTML = "Computing…"; $("#valHead").textContent = ""; $("#valNote").innerHTML = "";
  const d = await api("/api/validation");
  const verdict = d.mean_abs_dev <= 4 ? "strong agreement" : d.mean_abs_dev <= 8 ? "close agreement" : "notable divergence";
  $("#valHead").innerHTML =
    `AirGuard's Delhi winter source split vs <b>${d.source}</b> — mean deviation
     <b style="color:var(--accent)">${d.mean_abs_dev} pts</b> (${verdict}).`;
  $("#valBars").innerHTML = d.categories.map((c) => {
    const a = d.airguard[c.key], r = d.reference[c.key], dev = d.deviation[c.key];
    return `<div class="valrow"><div class="valrow-top"><span>${c.label}</span><span class="valdev">Δ ${dev} pts</span></div>
      <div class="valpair">
        <div class="valbar"><span class="vlbl">AirGuard</span><div class="vtrack"><div class="vfill" data-w="${a}" style="background:${c.color};color:${c.color}"></div></div><b>${a}%</b></div>
        <div class="valbar"><span class="vlbl">Study</span><div class="vtrack"><div class="vfill ref" data-w="${r}"></div></div><b>${r}%</b></div>
      </div></div>`;
  }).join("");
  requestAnimationFrame(() => document.querySelectorAll("#valBars .vfill")
    .forEach((el) => { el.style.width = el.dataset.w + "%"; }));
  $("#valNote").innerHTML = provTags([
    "anchored to published apportionment", "deterministic — no LLM variance", "swap in your cited study's exact figures"]);
}

/* ── Delhi 1 km grid ──────────────────────────────────────── */
async function toggleGrid() {
  if (state.gridOn) {
    ["grid-cells", "grid-stations"].forEach((l) => { if (map.getLayer(l)) map.removeLayer(l); });
    ["grid", "gridst"].forEach((s) => { if (map.getSource(s)) map.removeSource(s); });
    state.gridOn = false; $("#gridBtn").classList.remove("on"); return;
  }
  const d = await api("/api/grid/delhi");
  map.addSource("grid", { type: "geojson", data: fc(d.grid.map((c) => ({
    type: "Feature", geometry: { type: "Point", coordinates: [c.lon, c.lat] }, properties: { color: c.color } }))) });
  const before = map.getLayer("city-glow") ? "city-glow" : undefined;
  map.addLayer({ id: "grid-cells", type: "circle", source: "grid",
    paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 5, 11, 12],
      "circle-color": ["get", "color"], "circle-opacity": 0.5, "circle-blur": 0.4 } }, before);
  map.addSource("gridst", { type: "geojson", data: fc(d.stations.map((s) => ({
    type: "Feature", geometry: { type: "Point", coordinates: [s.lon, s.lat] },
    properties: { name: s.name, aqi: s.aqi, color: catOf(s.aqi)[2] } }))) });
  map.addLayer({ id: "grid-stations", type: "circle", source: "gridst",
    paint: { "circle-radius": 5, "circle-color": ["get", "color"],
      "circle-stroke-width": 1.5, "circle-stroke-color": "#fff" } });
  const gp = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12 });
  map.on("mousemove", "grid-stations", (e) => { map.getCanvas().style.cursor = "pointer";
    const p = e.features[0].properties;
    gp.setLngLat(e.features[0].geometry.coordinates).setHTML(`<b>${p.name}</b> · AQI ${p.aqi}`).addTo(map); });
  map.on("mouseleave", "grid-stations", () => { map.getCanvas().style.cursor = ""; gp.remove(); });
  state.gridOn = true; $("#gridBtn").classList.add("on");
  map.flyTo({ center: [77.12, 28.62], zoom: 9.3, pitch: 50, duration: 1300, essential: true });
}

/* ── helpers ──────────────────────────────────────────────── */
const chip = (l, v) => `<div class="chip">${l} <b>${v}</b></div>`;
const provTags = (arr) => arr.map((t) => `<span class="tag">${t}</span>`).join("");

/* ── wiring ───────────────────────────────────────────────── */
$("#panelClose").onclick = closeCity;
$("#modalClose").onclick = () => $("#modal").classList.add("hidden");
$("#modal").onclick = (e) => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); };
$("#briefBtn").onclick = openBrief;
$("#valBtn").onclick = openValidation;
$("#valClose").onclick = () => $("#valModal").classList.add("hidden");
$("#valModal").onclick = (e) => { if (e.target.id === "valModal") $("#valModal").classList.add("hidden"); };
$("#gridBtn").onclick = toggleGrid;
$("#trajBtn").onclick = toggleTraj;
$("#copyAlert").onclick = () => navigator.clipboard.writeText($("#alertBox").textContent).then(() => {
  $("#copyAlert").textContent = "✓ Copied"; setTimeout(() => $("#copyAlert").textContent = "Copy WhatsApp message", 1500); });
document.querySelectorAll(".tab").forEach((t) => t.onclick = () => setTab(t.dataset.tab));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") {
  $("#modal").classList.add("hidden"); $("#valModal").classList.add("hidden"); closeCity(); } });

window.map = map;  // exposed for debugging
init();
