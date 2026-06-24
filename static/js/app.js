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

const grapStageOf = (aqi) => aqi > 450 ? 4 : aqi > 400 ? 3 : aqi > 300 ? 2 : aqi > 200 ? 1 : 0;

const state = {
  meta: null, cities: [], cityById: {}, current: null, loaded: {},
  fcChart: null, trajOn: false, gridOn: false, trajAnim: null, endMarker: null,
  grapOrderText: "",
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
  updateGrapButton(cityData.cities);
  whenStyleReady(() => {
    addCityLayers(cityData.cities);
    addFireLayers(fireData.fires);
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
    // `band` is the exact AQI-zone colour from the legend (Good→Severe), so the
    // heat blobs are coloured by the city's real AQI category — not by density.
    properties: { id: c.id, name: c.name, aqi: c.aqi, color: c.color,
      category: c.category, band: catOf(c.aqi)[2] },
  }));
  map.addSource("cities", { type: "geojson", data: fc(features) });

  // Heat is rendered as soft glow blobs coloured by each city's AQI ZONE
  // (catOf → legend colour). circle-blur:1 = full radial falloff, so there is
  // no hard circle edge — they melt into the dark map and blend on overlap.
  // Wide, faint outer aura.
  map.addLayer({
    id: "city-heat", type: "circle", source: "cities",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"],
        3, 26,   5, 40,   7, 62,   9, 100,   11, 160,   14, 300],
      "circle-color": ["get", "band"],
      "circle-blur": 1, "circle-opacity": 0.30,
    },
  });
  // Tighter, brighter inner hotspot — gives the blob a hotter core.
  map.addLayer({
    id: "city-heat-core", type: "circle", source: "cities",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"],
        3, 14,   5, 22,   7, 34,   9, 56,   11, 90,   14, 170],
      "circle-color": ["get", "band"],
      "circle-blur": 1, "circle-opacity": 0.42,
    },
  });
  // Invisible hit-target per city — no visible circle, but still clickable and
  // hoverable so the heatmap + labels are the only things you see.
  map.addLayer({
    id: "city-core", type: "circle", source: "cities",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 12, 7, 18, 11, 26],
      "circle-color": "#ffffff", "circle-opacity": 0,
      "circle-stroke-width": 0,
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
  }, "city-core");
  map.addLayer({
    id: "fires-core", type: "circle", source: "fires",
    paint: { "circle-radius": 2.6, "circle-color": "#ffb454", "circle-opacity": 0.95 },
  }, "city-core");
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
  ({ analysis: loadAnalysis, forecast: loadForecast, health: loadHealth,
     enforcement: loadEnforcement, citizen: loadCitizen, grap: loadGrap,
     inventory: loadInventory }[name])();
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
  const priorTag = d.prior_source === "learned"
    ? `<span class="tag tag--learned" title="City-specific learned prior — click to view inventory" onclick="openMoatDashboard()" style="cursor:pointer">✦ City-learned prior</span>`
    : `<span class="tag">Published prior</span>`;
  $("#attrProvenance").innerHTML =
    provTags([d.llm_used ? `AI · ${d.provider}` : "AI · templated fallback",
              "live wind + satellite fires"]) + priorTag;
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

/* ── Health Impact ────────────────────────────────────────── */
async function loadHealth() {
  const id = state.current.id;
  const pane = document.querySelector('[data-pane="health"]');
  pane.innerHTML = `<p class="grap-loading">Computing health burden…</p>`;
  const d = await api(`/api/city/${id}/health`);
  if (state.current.id !== id) return;

  const LEVEL_COLOR = {
    low: "#00b894", moderate: "#f4d03f",
    high: "#f39c12", very_high: "#e74c3c", emergency: "#7e0023",
  };
  const LEVEL_LABEL = {
    low: "Low Health Risk", moderate: "Moderate Health Risk",
    high: "High Health Risk", very_high: "Very High Health Risk",
    emergency: "Health Emergency",
  };
  const lc = LEVEL_COLOR[d.alert_level] || "#f39c12";
  const ll = LEVEL_LABEL[d.alert_level] || "Health Risk";

  const fmtPop = (n) => n >= 1e7 ? (n/1e7).toFixed(1)+"Cr"
    : n >= 1e5 ? (n/1e5).toFixed(1)+"L"
    : n.toLocaleString("en-IN");

  const safeLabel = d.safe_hours_outdoors >= 1
    ? `${d.safe_hours_outdoors} hrs`
    : `${Math.round(d.safe_hours_outdoors * 60)} min`;

  const ar = d.at_risk;
  const maxPop = Math.max(ar.children_under_14, ar.elderly_60_plus,
    ar.respiratory_cardiac, ar.outdoor_workers);

  const riskRow = (label, val, color) => {
    const pct = Math.round(val / maxPop * 100);
    return `<div class="hi-risk-row">
      <div class="hi-risk-meta">
        <span class="hi-risk-label">${label}</span>
        <span class="hi-risk-val">${fmtPop(val)}</span>
      </div>
      <div class="hi-risk-track">
        <div class="hi-risk-fill" data-w="${pct}" style="background:${color}"></div>
      </div>
    </div>`;
  };

  pane.innerHTML = `
    <div class="hi-alert" style="border-color:${lc};color:${lc}">${ll}</div>

    <div class="hi-grid">
      <div class="hi-stat">
        <div class="hi-stat-val">${d.excess_admissions.toLocaleString("en-IN")}</div>
        <div class="hi-stat-label">Excess hospital admissions today</div>
        <div class="hi-stat-sub">respiratory &amp; cardiovascular</div>
      </div>
      <div class="hi-stat">
        <div class="hi-stat-val">${safeLabel}</div>
        <div class="hi-stat-label">Safe outdoor exposure today</div>
        <div class="hi-stat-sub">beyond this — health risk accrues</div>
      </div>
      <div class="hi-stat">
        <div class="hi-stat-val">${d.exposure_multiple}×</div>
        <div class="hi-stat-label">WHO PM2.5 limit exceeded</div>
        <div class="hi-stat-sub">${d.pm25} µg/m³ vs 15 µg/m³ guideline</div>
      </div>
      <div class="hi-stat">
        <div class="hi-stat-val">${fmtPop(ar.outdoor_workers)}</div>
        <div class="hi-stat-label">Outdoor workers exposed</div>
        <div class="hi-stat-sub">no respiratory protection assumed</div>
      </div>
    </div>

    <div class="hi-section-title">AT-RISK POPULATION</div>
    <div class="hi-risk-list">
      ${riskRow("Children under 14", ar.children_under_14, "#5aa2ff")}
      ${riskRow("Elderly 60+", ar.elderly_60_plus, "#ffa94d")}
      ${riskRow("Respiratory / cardiac patients", ar.respiratory_cardiac, "#e74c3c")}
      ${riskRow("Outdoor workers", ar.outdoor_workers, "#f4d03f")}
    </div>

    <div class="hi-section-title">CLINICAL ASSESSMENT</div>
    <div class="hi-summary">${d.summary}</div>

    <div class="provenance">${provTags([
      d.llm_used ? `AI · ${d.provider}` : "templated fallback",
      ...d.sources])}</div>`;

  requestAnimationFrame(() =>
    pane.querySelectorAll(".hi-risk-fill").forEach((el) => {
      el.style.width = el.dataset.w + "%";
    })
  );
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
  const before = map.getLayer("city-core") ? "city-core" : undefined;
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

function updateGrapButton(cities) {
  const count = cities.filter((c) => grapStageOf(c.aqi) > 0).length;
  const btn = $("#grapBtn");
  if (!btn) return;
  if (count > 0) {
    btn.innerHTML = `GRAP <span class="grap-count">${count}</span>`;
    btn.classList.add("grap-active");
  }
}

/* ── GRAP city panel tab ──────────────────────────────────── */
async function loadGrap() {
  const id = state.current.id;
  const pane = document.querySelector('[data-pane="grap"]');
  pane.innerHTML = `<p class="grap-loading">Generating GRAP order…</p>`;
  const d = await api(`/api/city/${id}/grap`);
  if (state.current.id !== id) return;

  if (!d.triggered) {
    pane.innerHTML = `
      <div class="grap-ok">
        <div class="grap-ok-icon">✓</div>
        <div class="grap-ok-title">Below GRAP Threshold</div>
        <div class="grap-ok-msg">${d.message}</div>
      </div>
      <div class="provenance">${provTags(["GRAP CAQM framework", "no emergency measures required"])}</div>`;
    return;
  }

  const restrHtml = d.order.restrictions
    .map((r, i) => `<li><span class="restr-num">${i + 1}</span>${r}</li>`).join("");
  const checkHtml = d.checklist.map((item, i) => `
    <label class="check-item prio-${item.priority}">
      <input type="checkbox" id="chk${i}">
      <span class="check-box"></span>
      <span class="check-text">${item.item}</span>
      <span class="check-badge prio-${item.priority}">${item.priority}</span>
    </label>`).join("");

  pane.innerHTML = `
    <div class="grap-badge" style="border-color:${d.color};box-shadow:0 0 22px -4px ${d.color}40">
      <div class="grap-badge-stage" style="color:${d.color}">${d.stage_name}</div>
      <div class="grap-badge-label">${d.label} Air Quality</div>
      <div class="grap-badge-aqi">AQI <span class="mono" style="color:${d.color}">${d.aqi}</span></div>
    </div>

    <div class="grap-doc">
      <div class="grap-doc-stripe" style="background:${d.color}"></div>
      <div class="grap-doc-inner">
        <div class="grap-doc-ref">${d.order.ref_no}</div>
        <div class="grap-doc-title">${d.order.title}</div>
        <div class="grap-doc-date">${d.order.date}</div>
        <div class="grap-doc-body"><p>${fmtOrderBody(d.order.body)}</p></div>
        <div class="grap-restr-head">RESTRICTIONS INVOKED</div>
        <ol class="grap-restr-list">${restrHtml}</ol>
        <div class="grap-sig">
          <div class="grap-sig-line"></div>
          <div class="grap-sig-name">${d.order.signatory}</div>
        </div>
      </div>
    </div>

    <div class="grap-checklist-block">
      <div class="grap-cl-head">ENFORCEMENT CHECKLIST</div>
      <div class="grap-checklist">${checkHtml}</div>
    </div>

    <button class="btn ghost wide" onclick="copyGrapOrder()">Copy Order Text</button>
    <div class="provenance" style="margin-top:12px">${provTags([
      d.llm_used ? `AI · ${d.provider}` : "AI · templated fallback",
      "GRAP CAQM framework", "source-attribution linked"])}</div>`;

  state.grapOrderText = [
    d.order.title,
    `Ref: ${d.order.ref_no}   Date: ${d.order.date}`,
    "",
    d.order.body,
    "",
    "RESTRICTIONS INVOKED:",
    ...d.order.restrictions.map((r, i) => `${i + 1}. ${r}`),
    "",
    d.order.signatory,
  ].join("\n");
}

window.copyGrapOrder = function () {
  if (!state.grapOrderText) return;
  navigator.clipboard.writeText(state.grapOrderText).then(() => {
    const btn = document.querySelector('[data-pane="grap"] .btn.ghost.wide');
    if (btn) { btn.textContent = "✓ Copied"; setTimeout(() => { btn.textContent = "Copy Order Text"; }, 1600); }
  });
};

/* ── Inventory / Data Moat ────────────────────────────────── */
async function loadInventory() {
  const pane = document.querySelector('[data-pane="inventory"]');
  if (!state.current) return;
  pane.innerHTML = `<p class="grap-loading">Loading emission inventory…</p>`;
  const d = await api(`/api/inventory/${state.current.id}`);

  const moatColor = d.moat_score >= 60 ? "#2fe6cf" : d.moat_score >= 25 ? "#f7b731" : "#8899aa";
  const priorLabel = d.using_learned_prior
    ? `<span class="inv-badge inv-badge--live">City-learned prior active</span>`
    : `<span class="inv-badge">Using published prior</span>`;

  // Moat gauge arc
  const pct = d.moat_score / 100;
  const r = 28, cx = 36, cy = 36, stroke = 5;
  const circ = 2 * Math.PI * r;
  const dash = circ * pct;

  let html = `
    <div class="inv-header">
      <div class="inv-gauge-wrap">
        <svg viewBox="0 0 72 72" class="inv-gauge-svg">
          <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="rgba(255,255,255,.07)" stroke-width="${stroke}"/>
          <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${moatColor}" stroke-width="${stroke}"
            stroke-dasharray="${dash.toFixed(1)} ${circ.toFixed(1)}"
            stroke-linecap="round" transform="rotate(-90 ${cx} ${cy})"/>
          <text x="${cx}" y="${cy+5}" text-anchor="middle" fill="${moatColor}"
            font-family="JetBrains Mono,monospace" font-size="13" font-weight="800">${d.moat_score}</text>
        </svg>
        <div class="inv-gauge-label">MOAT<br>SCORE</div>
      </div>
      <div class="inv-stats">
        <div class="inv-stat"><b class="mono" style="color:${moatColor}">${d.total_events.toLocaleString()}</b><span>attribution events</span></div>
        <div class="inv-stat"><b class="mono">${d.days_active}</b><span>days collecting</span></div>
        <div class="inv-stat">${priorLabel}</div>
      </div>
    </div>`;

  if (d.total_events === 0) {
    html += `<div class="inv-empty">
      <p>No events recorded for <b>${state.current.name}</b> yet.</p>
      <p class="inv-empty-sub">Every attribution you run is stored here. After ${8} events in a season, AirGuard switches from the published prior to a city-specific learned prior — sharpening accuracy with each analysis.</p>
    </div>`;
  } else {
    // Season breakdown
    if (d.season_summary.length) {
      html += `<h3 class="inv-section-title">Season Inventory</h3><div class="inv-seasons">`;
      for (const s of d.season_summary) {
        const imp = s.improvement_pct != null
          ? `<span class="inv-improvement">↓ ${s.improvement_pct}% RMSE vs base</span>` : "";
        const learnedBadge = s.learned
          ? `<span class="inv-badge inv-badge--live">Learned</span>`
          : `<span class="inv-badge">${s.events}/${8} events</span>`;
        html += `<div class="inv-season-row">
          <div class="inv-season-left">
            <span class="inv-season-name">${s.season}</span>
            ${learnedBadge}
          </div>
          <div class="inv-season-right">
            <span class="mono">${s.events}</span> events ${imp}
          </div>
        </div>`;
      }
      html += `</div>`;
    }

    // Prior drift
    if (d.drift.length) {
      html += `<h3 class="inv-section-title">Learned Prior Drift <span class="hint">vs published anchor</span></h3>
        <div class="inv-drift-list">`;
      for (const dr of d.drift) {
        const sign = dr.delta > 0 ? "+" : "";
        const dColor = dr.delta > 0 ? "#ff6b35" : "#5aa2ff";
        html += `<div class="inv-drift-row">
          <span class="inv-drift-cat">${dr.category.replace("_", " ")}</span>
          <span class="inv-drift-season">${dr.season}</span>
          <div class="inv-drift-bar-wrap">
            <span class="mono" style="color:${dColor};font-size:11px">${sign}${dr.delta}pp</span>
            <div class="inv-drift-bar" style="--pct:${Math.min(Math.abs(dr.delta)*3,100)}%;--col:${dColor}"></div>
          </div>
          <span class="inv-drift-vals">${dr.base}→<b>${dr.learned}</b>%</span>
        </div>`;
      }
      html += `</div>`;
    }

    // RMSE trend sparkline
    if (d.rmse_trend.length >= 3) {
      html += `<h3 class="inv-section-title">Accuracy Trend <span class="hint">RMSE — lower is better</span></h3>
        <div class="inv-rmse-wrap"><canvas id="rmseChart" height="70"></canvas></div>`;
    }
  }

  pane.innerHTML = html;

  // Draw RMSE sparkline
  if (d.rmse_trend.length >= 3) {
    const ctx = document.getElementById("rmseChart");
    if (ctx && window.Chart) {
      const labels = d.rmse_trend.map((_, i) => i + 1);
      new Chart(ctx, {
        type: "line",
        data: {
          labels,
          datasets: [
            { label: "vs Base Prior", data: d.rmse_trend.map(r => r.rmse_vs_base),
              borderColor: "#8899aa", borderWidth: 1.5, pointRadius: 0, tension: 0.4, fill: false },
            { label: "vs Learned Prior", data: d.rmse_trend.map(r => r.rmse_vs_learned),
              borderColor: "#2fe6cf", borderWidth: 2, pointRadius: 0, tension: 0.4, fill: false },
          ],
        },
        options: {
          plugins: { legend: { labels: { color: "#8899aa", font: { size: 10 }, boxWidth: 14 } } },
          scales: {
            x: { display: false },
            y: { ticks: { color: "#586277", font: { size: 9 } }, grid: { color: "rgba(255,255,255,.05)" } },
          },
          animation: false,
        },
      });
    }
  }
}

async function openMoatDashboard() {
  $("#moatModal").classList.remove("hidden");
  $("#moatContent").innerHTML = `<p class="grap-loading">Querying emission inventory…</p>`;
  const d = await api("/api/inventory");
  $("#moatModalTitle").textContent = `${d.total_events.toLocaleString()} Events · ${d.cities_tracked} Cities`;
  $("#moatMetaRow").textContent = d.moat_summary;

  if (!d.cities.length) {
    $("#moatContent").innerHTML = `<div class="grap-ok">
      <div class="grap-ok-icon">📊</div>
      <div class="grap-ok-title">Inventory Empty</div>
      <div class="grap-ok-msg">Open any city analysis to start recording attribution events.</div>
    </div>`;
    return;
  }

  // Moat bar chart across cities
  let html = `<div class="moat-city-list">`;
  for (const c of d.cities) {
    const barColor = c.moat_score >= 60 ? "#2fe6cf" : c.moat_score >= 25 ? "#f7b731" : "#5aa2ff";
    const learnedPills = c.learned_seasons.map(s =>
      `<span class="inv-badge inv-badge--live" style="font-size:9px">${s}</span>`).join("");
    html += `<div class="moat-city-row">
      <div class="moat-city-left">
        <span class="moat-city-name">${c.name}</span>
        ${learnedPills}
      </div>
      <div class="moat-city-bar-wrap">
        <div class="moat-city-bar" style="width:${c.moat_score}%;background:${barColor}"></div>
        <span class="moat-city-score mono" style="color:${barColor}">${c.events}</span>
      </div>
    </div>`;
  }
  html += `</div>
    <p class="inv-empty-sub" style="margin-top:16px;border-top:1px solid rgba(255,255,255,.06);padding-top:12px">
      <b>How the moat works:</b> Each attribution event refines city-specific seasonal priors.
      After ${8} events per season, AirGuard switches from the published anchor to the learned prior —
      yielding measurably lower RMSE. A competitor starting today has zero events; your inventory
      is non-replicable without re-running the same observations over the same period.
    </p>`;
  $("#moatContent").innerHTML = html;
  $("#moatProvenance").innerHTML = provTags(["Attribution events · all cities", "Rolling learned priors", "RMSE vs published anchor"]);
}

function closeMoatModal() { $("#moatModal").classList.add("hidden"); }

/* ── Stubble burn prediction ──────────────────────────────── */
const BURN_COLORS = { CRITICAL: "#ff2d55", HIGH: "#ff6b35", MEDIUM: "#f7b731", LOW: "#a8e063" };

async function openStubblePrediction() {
  $("#stubbleModal").classList.remove("hidden");
  const el = $("#stubbleContent");
  el.innerHTML = `<p class="grap-loading">Running prediction engine — fusing thermal anomalies, harvest calendar &amp; wind forecast…</p>`;
  const d = await api("/api/stubble/predictions");

  $("#stubbleModalTitle").textContent =
    `${d.critical_count + d.high_count} High-Risk Districts · 48–72 h Window`;

  const seasonBadge = d.season.active
    ? `<span class="tag" style="color:#ff6b35">🌾 ${d.season.crop} burn season active</span>`
    : `<span class="tag">Off-season (background risk)</span>`;

  $("#stubbleMetaRow").innerHTML =
    `${seasonBadge} <span class="tag">${d.total} districts analysed</span>` +
    `<span class="tag">${d.wind_live ? "Live" : "Seeded"} wind forecast</span>` +
    `<span class="tag">Generated ${d.generated_at}</span>`;

  if (!d.events.length) {
    el.innerHTML = `<div class="grap-ok"><div class="grap-ok-icon">✓</div>
      <div class="grap-ok-title">No High-Risk Events</div>
      <div class="grap-ok-msg">All districts below prediction threshold at this time.</div></div>`;
  } else {
    el.innerHTML = `<div class="stubble-list">${d.events.map((e, i) => `
      <div class="sbe sbe--${e.risk_level.toLowerCase()}" onclick="toggleBurnAlert(${i})">
        <div class="sbe-header">
          <span class="sbe-level" style="color:${BURN_COLORS[e.risk_level]}">${e.risk_level}</span>
          <span class="sbe-name">${e.district}, ${e.state}</span>
          <span class="sbe-score mono" style="color:${BURN_COLORS[e.risk_level]}">${e.risk_score}</span>
        </div>
        <div class="sbe-meta">
          <span>🌾 ${e.crop}</span>
          <span>💨 ${e.transport.mean_speed_kmh} km/h wind</span>
          <span>📅 ${e.predicted_window}</span>
          ${e.transport.toward.length ? `<span>→ ${e.transport.toward.map(c=>c.city).join(', ')}</span>` : ''}
        </div>
        <div class="sbe-bars">
          <div class="sbe-bar-row"><span>Season</span><div class="sbe-bar-track"><div class="sbe-bar-fill" style="width:${Math.round(e.season_active ? 100 : 25)}%;background:${BURN_COLORS[e.risk_level]}"></div></div></div>
          <div class="sbe-bar-row"><span>Hotspot</span><div class="sbe-bar-track"><div class="sbe-bar-fill" style="width:${Math.round(e.hotspot_density * 100)}%;background:${BURN_COLORS[e.risk_level]}"></div></div></div>
          <div class="sbe-bar-row"><span>Wind</span><div class="sbe-bar-track"><div class="sbe-bar-fill" style="width:${Math.round(e.transport.risk * 100)}%;background:${BURN_COLORS[e.risk_level]}"></div></div></div>
        </div>
        ${e.collector_alert ? `
        <div class="sbe-alert hidden" id="sbe-alert-${i}">
          <div class="sbe-alert-eyebrow">DISTRICT COLLECTOR ALERT — ${e.district.toUpperCase()}</div>
          <pre class="sbe-alert-text">${e.collector_alert}</pre>
          <button class="btn ghost" onclick="event.stopPropagation();copyBurnAlert(${i})">Copy Alert</button>
        </div>` : ''}
      </div>`).join('')}
    </div>`;

    // Update map with burn risk zones
    addBurnRiskLayer(d.events);
  }

  $("#stubbleProvenance").innerHTML = provTags([
    "NASA FIRMS thermal anomalies", "Open-Meteo 72h wind", "Harvest calendar anchors",
    "16 districts · Punjab / Haryana / UP"]);
}

window.toggleBurnAlert = function(i) {
  const el = document.getElementById(`sbe-alert-${i}`);
  if (el) el.classList.toggle("hidden");
};

window.copyBurnAlert = function(i) {
  const el = document.getElementById(`sbe-alert-${i}`);
  const text = el?.querySelector(".sbe-alert-text")?.textContent || "";
  navigator.clipboard.writeText(text).then(() => {
    const btn = el?.querySelector(".btn");
    if (btn) { btn.textContent = "✓ Copied"; setTimeout(() => btn.textContent = "Copy Alert", 1500); }
  });
};

// Track HTML markers so we can remove/replace them on refresh
const _burnMarkers = [];

function addBurnRiskLayer(events) {
  // Remove old markers
  _burnMarkers.forEach((m) => m.remove());
  _burnMarkers.length = 0;

  events.forEach((e) => {
    const color = BURN_COLORS[e.risk_level] || "#f7b731";
    const size = e.risk_level === "CRITICAL" ? 44 : e.risk_level === "HIGH" ? 38 : 32;
    const pulseSize = size + 18;

    const el = document.createElement("div");
    el.className = "burn-marker";
    el.style.cssText = `position:relative;width:${size}px;height:${size}px;cursor:pointer`;
    el.innerHTML = `
      <div class="burn-pulse" style="width:${pulseSize}px;height:${pulseSize}px;
        top:${-(pulseSize - size) / 2}px;left:${-(pulseSize - size) / 2}px;
        border-color:${color};animation-duration:${e.risk_level === "CRITICAL" ? "1.1s" : "1.6s"}"></div>
      <div class="burn-icon" style="width:${size}px;height:${size}px;border-color:${color};box-shadow:0 0 12px ${color}88">
        <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"
          style="width:${Math.round(size * 0.52)}px;height:${Math.round(size * 0.52)}px">
          <path d="M12 2C12 2 7 7.5 7 12.5C7 15.5 9.2 18 12 18C14.8 18 17 15.5 17 12.5C17 10.5 15.5 8.5 14 7C14 9 13 10 12 10C11 10 10 9 10 7.5C10 5.5 12 2 12 2Z"
            fill="${color}" opacity="0.9"/>
          <path d="M12 13C12 13 10 11.5 10 10C10 10 9 12 9 13.5C9 15.5 10.3 17 12 17C13.7 17 15 15.5 15 13.5C15 12.5 14.5 11.5 14 11C14 12 13 13 12 13Z"
            fill="white" opacity="0.7"/>
        </svg>
      </div>
      <div class="burn-label" style="color:${color}">${e.district}</div>`;

    el.addEventListener("click", () => {
      new maplibregl.Popup({ closeButton: false, className: "burn-popup", offset: [0, -size / 2] })
        .setLngLat([e.lon, e.lat])
        .setHTML(`
          <div style="font-family:'Sora',sans-serif;font-size:10px;font-weight:800;letter-spacing:.6px;color:${color};margin-bottom:4px">${e.risk_level} RISK</div>
          <div style="font-weight:700;font-size:13px;margin-bottom:6px">${e.district}, ${e.state}</div>
          <div style="font-size:11px;color:#8899aa;line-height:1.6">
            Score <b style="color:${color}">${e.risk_score}</b>/100 &nbsp;·&nbsp; ${e.crop}<br>
            📅 ${e.predicted_window}<br>
            💨 ${e.transport.mean_speed_kmh} km/h
            ${e.transport.toward.length ? `→ ${e.transport.toward.map(c => c.city).join(", ")}` : ""}
          </div>`)
        .addTo(map);
    });

    const marker = new maplibregl.Marker({ element: el, anchor: "center" })
      .setLngLat([e.lon, e.lat])
      .addTo(map);
    _burnMarkers.push(marker);
  });
}

function closeStubbleModal() { $("#stubbleModal").classList.add("hidden"); }

/* ── GRAP national modal ──────────────────────────────────── */
async function openGrapNational() {
  $("#grapModal").classList.remove("hidden");
  const el = $("#grapNationalContent");
  el.innerHTML = `<p class="grap-loading">Loading GRAP status…</p>`;
  const d = await api("/api/grap/national");
  $("#grapModalTitle").textContent =
    d.count > 0 ? `${d.count} ${d.count === 1 ? "City" : "Cities"} in Active GRAP` : "National GRAP Status";
  if (d.count === 0) {
    el.innerHTML = `
      <div class="grap-ok" style="margin-top:4px">
        <div class="grap-ok-icon">✓</div>
        <div class="grap-ok-title">All Clear</div>
        <div class="grap-ok-msg">No monitored cities currently crossing GRAP thresholds.</div>
      </div>`;
  } else {
    el.innerHTML = `
      <div class="brief-meta">${d.count} of ${d.total_cities} monitored cities in active GRAP. Click a city to open its order.</div>
      <div class="grap-national-list">
        ${d.triggered.map((c) => `
          <div class="gnc" onclick="openCityFromGrap('${c.id}')" style="--sc:${c.stage_color}">
            <div class="gnc-left">
              <span class="gnc-stage" style="color:${c.stage_color}">${c.stage_name}</span>
              <span class="gnc-name">${c.name}</span>
              <span class="gnc-lbl">${c.label}</span>
            </div>
            <span class="gnc-aqi mono" style="color:${c.color}">${c.aqi}</span>
          </div>`).join("")}
      </div>`;
  }
  $("#grapNationalProvenance").innerHTML = provTags([
    `${d.total_cities} cities monitored`, "GRAP CAQM thresholds"]);
}

window.openCityFromGrap = function (cityId) {
  $("#grapModal").classList.add("hidden");
  const city = state.cityById[cityId];
  if (!city) return;
  openCity(city);
  setTimeout(() => setTab("grap"), 200);
};

function closeGrapModal() { $("#grapModal").classList.add("hidden"); }

/* ── helpers ──────────────────────────────────────────────── */
const chip = (l, v) => `<div class="chip">${l} <b>${v}</b></div>`;
const provTags = (arr) => arr.map((t) => `<span class="tag">${t}</span>`).join("");

function fmtOrderBody(text) {
  return text
    .replace(/\*\*(.*?)\*\*/g, "<b>$1</b>")   // **bold** → <b>
    .replace(/\*(.*?)\*/g, "<em>$1</em>")       // *italic* → <em>
    .replace(/^#{1,3}\s+/gm, "")               // strip # headings
    .replace(/\n\n+/g, "</p><p>");              // double newline → paragraph
}

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
$("#grapBtn").onclick = openGrapNational;
$("#grapModalClose").onclick = closeGrapModal;
$("#grapModal").onclick = (e) => { if (e.target.id === "grapModal") closeGrapModal(); };
$("#stubbleBtn").onclick = openStubblePrediction;
$("#stubbleModalClose").onclick = closeStubbleModal;
$("#stubbleModal").onclick = (e) => { if (e.target.id === "stubbleModal") closeStubbleModal(); };
$("#moatBtn").onclick = openMoatDashboard;
$("#moatModalClose").onclick = closeMoatModal;
$("#moatModal").onclick = (e) => { if (e.target.id === "moatModal") closeMoatModal(); };
$("#copyAlert").onclick = () => navigator.clipboard.writeText($("#alertBox").textContent).then(() => {
  $("#copyAlert").textContent = "✓ Copied"; setTimeout(() => $("#copyAlert").textContent = "Copy WhatsApp message", 1500); });
document.querySelectorAll(".tab").forEach((t) => t.onclick = () => setTab(t.dataset.tab));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") {
  $("#modal").classList.add("hidden"); $("#valModal").classList.add("hidden");
  $("#grapModal").classList.add("hidden"); $("#stubbleModal").classList.add("hidden");
  $("#moatModal").classList.add("hidden"); closeCity(); } });

window.map = map;  // exposed for debugging
init();
