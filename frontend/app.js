/* CHHAON — frontend. Vanilla JS, no build step.
 * The story/film scroll logic is unchanged. The route tool now calls the real
 * deployed Chhaon API (see api.js) instead of returning mock routes. */
const CONFIG = {
  film: { basePath: "../frame/", clips: ["clip1","clip2","clip3","clip4","clip5","clip6"], framesPerClip: 120, pad: 4, lerp: 0.12, maxDPR: 1.5, preloadFirst: 20 },
  captions: [
    { from: 0.14, to: 0.28, label: "The land", text: "This is what the land gave us." },
    { from: 0.30, to: 0.42, label: "Deforestation", text: "First, the trees were cut." },
    { from: 0.44, to: 0.56, label: "Urbanization", text: "Then, concrete replaced soil." },
    { from: 0.58, to: 0.70, label: "Vehicle emissions", text: "More vehicles. More exhaust." },
    { from: 0.72, to: 0.84, label: "Waste burning and cooling", text: "Waste was burned. Air conditioners pushed the heat outside." },
    { from: 0.86, to: 1.00, label: "The result", text: "Hotter. Harder to breathe." },
  ],
  colors: { cool: "#3fb8a4", heat: "#ff3b1f", accent: "#ff6b2c", amber: "#e0a100", green: "#3fa46a", grey: "#9aa0a6" },
  phases: [
    { id: "film", name: "The land" }, { id: "problem", name: "The problem" },
    { id: "what", name: "Chhaon" }, { id: "how", name: "How it works" },
    { id: "tool", name: "The tool" }, { id: "help", name: "Who it helps" },
  ],
  map: {
    // Amazon Location Service — MapLibre GL loads the "Style dynamic maps"
    // (v2) descriptor from ap-south-1. The key is a RESTRICTED PUBLIC client
    // key (NOT an AWS credential): scoped to geo-maps:*/geo-places:* on the
    // ap-south-1 provider. Embedding it in a web page is the documented AWS
    // pattern. Rotate/delete with:
    //   aws location delete-key --key-name chhaon-web --region ap-south-1 --force-delete
    styleUrl: "https://maps.geo.ap-south-1.amazonaws.com/v2/styles/Standard/descriptor?key=v1.public.eyJqdGkiOiJmMGQ4MmM2NC05ZDNmLTRlMzEtYjkyYS1jOTMyYjVlNTljZTAifTNF3fwdkkxYtgyb-Ez8sYr5qYjFrXr_nuCngBU1g6osEt7w7wGJFz59IGtjcSvR63d_pVRlR0S0tKuCkbLuhZ-4BSqRmA7NZ9Iud8GLJcULmsgfs9CcG_X4Y6hLRYTAZtRX9NLCNsn8qB5vlHASWDjgDCCXS2F9Jvwy4Q7J16ji3LoYtJGzjZ9cy7S9IlU1U8IB86tK0K7s9Xlmmz7h_0rZNM2hfRVp6Y5XclELrKndhiU2oJKj3_69QQ2PmK66Oe_AxjTcgSA8kL0aQa9C1ElowTul4SAu1v58LfcR0yDZ2hPZK3bIMtolqM--VkFBDFh3ysV-PlWJ8QvbxH30tz4.Njg1MGZlZTUtYTI2ZS00MDdlLWJjNDktMDNmZDlkNzVmMjQ0",
    // Used only if the Amazon style fails to load, so the map is never blank.
    fallbackStyleUrl: "https://demotiles.maplibre.org/style.json",
    center: [77.2090, 28.6139], zoom: 13,
  },
  // Development ONLY. When true (or with ?mock=1 in the URL) the tool renders
  // built-in sample routes instead of calling the API. It is never enabled by
  // default and is NEVER used as a fallback when a real request fails.
  dev: { useMockRoutes: false },
};
/* ACT 1 film — scroll logic preserved: progress->index, cover-fit, lerp, preload 20 + idle rest, DPR cap, reverse on scroll-up, reduced-motion skips smoothing. */
(function film() {
  const F = CONFIG.film;
  const section = document.getElementById("film"), canvas = document.getElementById("filmCanvas");
  const ctx = canvas.getContext("2d");
  const labelEl = document.getElementById("filmLabel"), lineEl = document.getElementById("filmLine");
  const textBox = document.getElementById("filmText"), hint = document.getElementById("scrollHint");
  const hero = document.getElementById("filmHero");
  const loadBar = document.getElementById("loadBar"), prog = document.getElementById("filmProgress");
  if (!section || !canvas) return;
  const rm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const urls = [];
  for (const c of F.clips) for (let i = 1; i <= F.framesPerClip; i++) urls.push(F.basePath + c + "_f_" + String(i).padStart(F.pad, "0") + ".webp");
  const N = urls.length, cache = new Array(N).fill(null);
  let loaded = 0, current = -1, target = 0, smooth = 0, ticking = false;
  function sizeCanvas() {
    const dpr = Math.min(window.devicePixelRatio || 1, F.maxDPR);
    canvas.width = Math.round((canvas.clientWidth || innerWidth) * dpr);
    canvas.height = Math.round((canvas.clientHeight || innerHeight) * dpr);
    draw(current);
  }
  function loadOne(i) {
    return new Promise((res) => {
      if (cache[i]) return res();
      const im = new Image();
      im.onload = () => { cache[i] = im; loaded++; loadBar.style.width = (loaded / N * 100).toFixed(1) + "%"; res(); };
      im.onerror = () => res();
      im.src = urls[i];
    });
  }
  (async () => {
    const jobs = [];
    for (let i = 0; i < Math.min(F.preloadFirst, N); i++) jobs.push(loadOne(i));
    await Promise.all(jobs);
    draw(0); current = 0;
    const rest = []; for (let i = F.preloadFirst; i < N; i++) rest.push(i);
    const pump = ("requestIdleCallback" in window) ? requestIdleCallback : (fn) => setTimeout(fn, 0);
    (function next() { if (!rest.length) return; Promise.all(rest.splice(0, 4).map(loadOne)).then(() => pump(next)); })();
  })();
  function paint(im) {
    const cw = canvas.width, ch = canvas.height;
    const s = Math.max(cw / im.naturalWidth, ch / im.naturalHeight);
    const dw = im.naturalWidth * s, dh = im.naturalHeight * s;
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, cw, ch);
    ctx.drawImage(im, (cw - dw) / 2, (ch - dh) / 2, dw, dh);
  }
  function draw(i) {
    if (i < 0 || i >= N) return;
    if (cache[i]) { paint(cache[i]); current = i; return; }
    let j = i; while (j >= 0 && !cache[j]) j--;
    if (j >= 0) { paint(cache[j]); current = j; }
  }
  function mix(a, b, t) {
    const pa = [parseInt(a.slice(1,3),16),parseInt(a.slice(3,5),16),parseInt(a.slice(5,7),16)];
    const pb = [parseInt(b.slice(1,3),16),parseInt(b.slice(3,5),16),parseInt(b.slice(5,7),16)];
    return "rgb(" + pa.map((v,k)=>Math.round(v+(pb[k]-v)*t)).join(",") + ")";
  }
  function progress() {
    const range = section.offsetHeight - innerHeight;
    return range > 0 ? Math.min(Math.max(scrollY - section.offsetTop, 0), range) / range : 0;
  }
  function captions(p) {
    // Hero (Section 1: Introduction, 0-13%): visible at load, fades as film story begins.
    // Driven by the SAME scroll progress as the canvas frames — no extra listener.
    if (hero) {
      const t = Math.min(Math.max(p / 0.13, 0), 1);   // 0 at top -> 1 by 13%
      hero.style.opacity = String(Math.max(0, 1 - t));
      hero.classList.toggle("hide", p > 0.12);
    }
    const hit = CONFIG.captions.find((c) => p >= c.from && p <= c.to);
    if (hit) {
      if (labelEl.textContent !== hit.label) { labelEl.textContent = hit.label; lineEl.textContent = hit.text; }
      textBox.classList.add("show");
    } else textBox.classList.remove("show");
    hint.style.opacity = p < 0.14 ? String(0.7 * (1 - p / 0.14)) : "0";
    prog.style.width = (p * 100).toFixed(1) + "%";
    const ac = mix(CONFIG.colors.cool, CONFIG.colors.heat, p);
    document.documentElement.style.setProperty("--accent", p < 0.5 ? mix(CONFIG.colors.cool, CONFIG.colors.accent, p * 2) : mix(CONFIG.colors.accent, CONFIG.colors.heat, (p - 0.5) * 2));
    prog.style.background = ac; loadBar.style.background = ac;
  }
  function loop() {
    ticking = false;
    target = progress();
    smooth = rm ? target : smooth + (target - smooth) * F.lerp;
    if (Math.abs(target - smooth) < 0.0005) smooth = target;
    const idx = Math.min(N - 1, Math.max(0, Math.round(smooth * (N - 1))));
    if (idx !== current) { if (cache[idx]) draw(idx); else loadOne(idx).then(() => draw(idx)); }
    captions(smooth);
    markPhase();
    if (!rm && smooth !== target) start();
  }
  function start() { if (!ticking) { ticking = true; requestAnimationFrame(loop); } }
  addEventListener("scroll", () => { start(); markPhase(); }, { passive: true });
  addEventListener("resize", () => { sizeCanvas(); start(); });
  sizeCanvas(); loop();
})();
function markPhase(){
  const dots=document.querySelectorAll('#phaseDots button');
  if(!dots.length)return;
  let best=0,bd=1e9;
  CONFIG.phases.forEach((p,i)=>{const el=document.getElementById(p.id);if(!el)return;const d=Math.abs(el.getBoundingClientRect().top);if(d<bd){bd=d;best=i;}});
  dots.forEach((b,i)=>b.classList.toggle('on',i===best));
}
(function phases(){
  const ol=document.getElementById('phaseDots');
  CONFIG.phases.forEach((p,i)=>{const li=document.createElement('li');const b=document.createElement('button');b.type='button';b.setAttribute('aria-label','Go to '+p.name);b.addEventListener('click',()=>document.getElementById(p.id).scrollIntoView({behavior:'smooth'}));li.appendChild(b);ol.appendChild(li);});
  markPhase();
})();
(function reveals(){
  const els=document.querySelectorAll('[data-reveal]');
  if(!('IntersectionObserver' in window)){els.forEach(e=>e.classList.add('in'));return;}
  const io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){e.target.classList.add('in');io.unobserve(e.target);}}),{threshold:0.35});
  els.forEach(e=>io.observe(e));
})();
document.getElementById('toTool').addEventListener('click',()=>document.getElementById('tool').scrollIntoView({behavior:'smooth'}));

/* ---------------------------------------------------------------------------
 * ACT — the route tool. Reads user input, validates it, calls the production
 * Chhaon API (/meta and /route), and renders the real response. All displayed
 * metrics come from the API; nothing is fabricated on the client.
 * ------------------------------------------------------------------------- */
(function tool(){
  const form = document.getElementById('routeForm');
  if (!form) return;

  const statusEl = document.getElementById('formStatus');
  const resultsEl = document.getElementById('results');
  const resultsMetaEl = document.getElementById('resultsMeta');
  const adviceBox = document.getElementById('adviceBox');
  const adviceText = document.getElementById('adviceText');
  const adviceSourceEl = document.getElementById('adviceSource');
  const pilotNote = document.getElementById('pilotNote');
  const disclaimerEl = document.getElementById('toolDisclaimer');
  const findBtn = document.getElementById('findBtn');
  const bEn = document.getElementById('langEn');
  const bHi = document.getElementById('langHi');
  const inStart = document.getElementById('inStart');
  const inDest = document.getElementById('inDest');
  const inTime = document.getElementById('inTime');
  const inWho = document.getElementById('inWho');

  const DEFAULT_DISCLAIMER =
    'Estimated values for a pilot area, based on map data and simple models. Not measured conditions.';
  if (disclaimerEl) disclaimerEl.textContent = DEFAULT_DISCLAIMER;

  // Populate the location suggestion list from the shared place list.
  const placeList = document.getElementById('placeList');
  // `CHHAON` is declared with const in api.js; like all top-level const bindings
  // it lives in the shared global lexical scope (not on window), so reference
  // it by name and guard with typeof.
  if (placeList && typeof CHHAON !== 'undefined') {
    for (const p of CHHAON.PILOT_PLACES) {
      const o = document.createElement('option');
      o.value = p.name;
      placeList.appendChild(o);
    }
  }

  const state = { meta: null, routes: null, advice: null, lang: 'en', selected: null, req: null };
  let inFlight = null;
  let markers = [];

  /* --- map ----------------------------------------------------------------- */
  let map = null, mapReady = false, styleFellBack = false;

  // Decide the basemap up front by checking the Amazon Location style URL.
  // Doing this BEFORE MapLibre starts avoids false fallbacks: an ordinary tile
  // hiccup used to look like a style failure and blanked the Amazon basemap.
  async function pickMapStyle() {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 8000);
      const r = await fetch(CONFIG.map.styleUrl, { method: 'GET', signal: ctrl.signal });
      clearTimeout(t);
      if (r.ok) return CONFIG.map.styleUrl;
    } catch (_) { /* fall through to the fallback style */ }
    return CONFIG.map.fallbackStyleUrl;
  }

  (async function initMap() {
    const mapEl = document.getElementById('map');
    if (typeof maplibregl === 'undefined' || !mapEl) {
      if (mapEl) {
        mapEl.classList.add('map-unavailable');
        mapEl.textContent = 'Map unavailable — routes are still calculated and listed below.';
      }
      return;
    }
    const style = await pickMapStyle();
    styleFellBack = style !== CONFIG.map.styleUrl;
    if (styleFellBack) console.warn('[chhaon] Amazon Location style unavailable; using the fallback basemap.');
    map = new maplibregl.Map({ container: 'map', style: style, center: CONFIG.map.center, zoom: CONFIG.map.zoom });
    map.addControl(new maplibregl.NavigationControl(), 'top-right');
    map.on('load', () => { mapReady = true; });
    // Second safety net: only a genuine style-load failure falls back. Errors
    // carrying a tile/source (data) are ignored so the basemap stays put.
    map.on('error', (e) => {
      if (mapReady || styleFellBack) return;
      if (e && (e.tile || e.sourceId)) return;
      styleFellBack = true;
      console.warn('[chhaon] map style failed to load; falling back to demotiles:', (e && e.error && e.error.message) || String(e));
      try { map.setStyle(CONFIG.map.fallbackStyleUrl); } catch (_) { /* ignore */ }
    });
  })();

  function routeColor(type) {
    if (type === 'fastest') return CONFIG.colors.grey;
    if (type === 'balanced') return CONFIG.colors.amber;
    return CONFIG.colors.green;
  }

  function clearMapRoutes() {
    if (!map || !mapReady) return;
    for (const t of CHHAON.ROUTE_TYPES) {
      if (map.getLayer(t)) map.removeLayer(t);
      if (map.getSource(t)) map.removeSource(t);
    }
    for (const mk of markers) mk.remove();
    markers = [];
  }

  function addMarker(lngLat, cls, title) {
    if (!map) return null;
    const el = document.createElement('div');
    el.className = 'mk ' + cls;
    el.title = title;
    return new maplibregl.Marker({ element: el }).setLngLat(lngLat).addTo(map);
  }

  function drawRoutes(routes, start, end) {
    if (!map || !mapReady) return;
    clearMapRoutes();
    const bounds = new maplibregl.LngLatBounds();
    let any = false;
    for (const r of routes) {
      if (!r.geometry) continue;               // never invent geometry
      map.addSource(r.type, { type: 'geojson', data: { type: 'Feature', properties: {}, geometry: r.geometry } });
      map.addLayer({ id: r.type, type: 'line', source: r.type,
        paint: { 'line-color': routeColor(r.type), 'line-width': 4, 'line-opacity': 0.85 } });
      for (const c of r.geometry.coordinates) bounds.extend(c);
      any = true;
    }
    if (start) { markers.push(addMarker([start.lon, start.lat], 'start', 'Start')); bounds.extend([start.lon, start.lat]); any = true; }
    if (end) { markers.push(addMarker([end.lon, end.lat], 'end', 'Destination')); bounds.extend([end.lon, end.lat]); any = true; }
    if (any) map.fitBounds(bounds, { padding: 50, maxZoom: 16 });
    applySelection();
  }

  function applySelection() {
    if (!map || !mapReady) return;
    const sel = state.selected;
    for (const t of CHHAON.ROUTE_TYPES) {
      if (!map.getLayer(t)) continue;
      map.setPaintProperty(t, 'line-width', sel === t ? 7 : 4);
      map.setPaintProperty(t, 'line-opacity', sel ? (sel === t ? 1 : 0.25) : 0.85);
    }
    if (sel && map.getLayer(sel)) map.moveLayer(sel);
  }

  /* --- metadata ------------------------------------------------------------ */
  function renderPilotNote(m) {
    if (!pilotNote) return;
    pilotNote.classList.remove('warn');
    const bits = [];
    if (m.pilot) bits.push('Pilot area: ' + m.pilot);
    if (Array.isArray(m.available_hours) && m.available_hours.length) {
      bits.push('hours ' + m.available_hours[0] + '\u2013' + m.available_hours[m.available_hours.length - 1]);
    }
    if (m.is_sample) {
      pilotNote.textContent = (bits.join(' \u00b7 ') || 'Pilot area loaded') + ' \u2014 warning: this API is serving the synthetic sample graph, not real routing data.';
      pilotNote.classList.add('warn');
    } else {
      pilotNote.textContent = (bits.join(' \u00b7 ') || 'Pilot area loaded') + ' \u00b7 real OpenStreetMap graph';
    }
    if (disclaimerEl && m.disclaimer) disclaimerEl.textContent = m.disclaimer;
  }

  (async function loadMeta() {
    if (typeof CHHAON === 'undefined') return;
    try {
      state.meta = await CHHAON.fetchMeta();
      renderPilotNote(state.meta);
    } catch (err) {
      if (pilotNote) {
        pilotNote.textContent = 'Pilot-area details could not be loaded. You can still try a route search, or reload the page.';
        pilotNote.classList.add('warn');
      }
    }
  })();

  /* --- rendering ----------------------------------------------------------- */
  function addLi(ul, value, label) {
    const li = document.createElement('li');
    if (value) { const b = document.createElement('b'); b.textContent = value; li.appendChild(b); }
    li.appendChild(document.createTextNode(label));
    ul.appendChild(li);
  }

  function cards(routes) {
    resultsEl.innerHTML = '';
    for (const r of routes) {
      const card = document.createElement('button');
      card.type = 'button';
      card.className = 'route ' + r.type;
      card.dataset.type = r.type;
      card.setAttribute('aria-pressed', 'false');

      const h3 = document.createElement('h3');
      h3.textContent = r.label;
      const est = document.createElement('span');
      est.className = 'muted small';
      est.textContent = ' (estimated)';
      h3.appendChild(est);

      const ul = document.createElement('ul');
      addLi(ul, r.minutes === null ? null : CHHAON.formatMinutes(r.minutes), ' walk');
      addLi(ul, r.distanceKm === null ? null : CHHAON.formatKm(r.distanceKm), ' distance');
      addLi(ul, r.shadePercent === null ? null : CHHAON.formatPercent(r.shadePercent), ' shade');
      addLi(ul, r.exposureIndex === null ? null : String(r.exposureIndex), ' exposure index (0\u2013100)');
      if (r.reductionVsFastest !== null && r.reductionVsFastest !== undefined) {
        addLi(ul, CHHAON.formatPercent(r.reductionVsFastest), ' less sun than fastest');
      }
      if (r.sameAs) {
        const li = document.createElement('li');
        li.className = 'muted small';
        li.textContent = 'Same route as ' + (CHHAON.ROUTE_LABELS[r.sameAs] || r.sameAs);
        ul.appendChild(li);
      }

      card.appendChild(h3);
      card.appendChild(ul);
      card.addEventListener('click', () => selectRoute(r.type));
      resultsEl.appendChild(card);
    }
  }

  function selectRoute(type) {
    state.selected = type;
    for (const c of resultsEl.querySelectorAll('.route')) {
      const on = c.dataset.type === type;
      c.classList.toggle('sel', on);
      c.setAttribute('aria-pressed', on ? 'true' : 'false');
    }
    applySelection();
  }

  function renderAdvice(res) {
    state.advice = res.advice || {};
    adviceBox.hidden = false;
    setLang(state.lang);
    if (adviceSourceEl) {
      if (res.adviceSource && res.adviceSource !== 'bedrock') {
        adviceSourceEl.hidden = false;
        adviceSourceEl.textContent = res.adviceSource === 'template' ? 'template fallback' : res.adviceSource;
      } else {
        adviceSourceEl.hidden = true;
      }
    }
  }

  function setLang(lang) {
    state.lang = lang;
    const a = state.advice || {};
    let text = a[lang];
    if (!text) text = lang === 'hi'
      ? '\u0938\u0932\u093e\u0939 \u0909\u092a\u0932\u092c\u094d\u0927 \u0928\u0939\u0940\u0902 \u0939\u0948\u0964'
      : 'Advice is not available for this request.';
    adviceText.textContent = text;
    bEn.classList.toggle('on', lang === 'en');
    bHi.classList.toggle('on', lang === 'hi');
    bEn.setAttribute('aria-pressed', lang === 'en' ? 'true' : 'false');
    bHi.setAttribute('aria-pressed', lang === 'hi' ? 'true' : 'false');
  }

  function summaryLine(res, req) {
    const parts = [];
    if (res.pilot) parts.push(res.pilot);
    if (req && req.labels) parts.push(req.labels.start + ' \u2192 ' + req.labels.end);
    if (res.walker) parts.push(CHHAON.WALKER_LABELS[res.walker] || res.walker);
    if (res.hourUsed !== null && res.hourUsed !== undefined) parts.push('at ' + CHHAON.formatHour(res.hourUsed));
    const missing = res.routes.filter((r) => !r.geometry).length;
    if (missing) parts.push(missing + ' of ' + res.routes.length + ' with no map geometry');
    return parts.join(' \u00b7 ');
  }

  function renderResults(res, req) {
    if (resultsMetaEl) resultsMetaEl.textContent = summaryLine(res, req);
    cards(res.routes);
    drawRoutes(res.routes, req.start, req.end);
    renderAdvice(res);
    if (disclaimerEl && res.disclaimer) disclaimerEl.textContent = res.disclaimer;
  }

  /* --- status helpers ------------------------------------------------------ */
  function showError(msg) {
    if (!statusEl) return;
    statusEl.textContent = msg;
    statusEl.classList.add('error');
  }
  function setBusy(busy) {
    form.setAttribute('aria-busy', busy ? 'true' : 'false');
    if (findBtn) {
      findBtn.disabled = busy;
      findBtn.textContent = busy ? 'Finding\u2026' : 'Find my route';
    }
    if (statusEl && busy) { statusEl.classList.remove('error'); statusEl.textContent = 'Finding cooler routes\u2026'; }
  }
  function errorMessage(err) {
    if (!err) return 'Something went wrong. Please try again.';
    if (err.name === 'ApiError') {
      if (err.kind === 'network') return err.message;
      if (err.kind === 'input') return err.message;
      if (err.kind === 'invalid-response') return err.message;
      if (err.status === 400) return err.message || 'The service rejected that request.';
      if (err.status === 404) return 'No walking route was found between those points.';
      if (err.status >= 500) return 'The route service had a problem. Please try again shortly.';
      return err.message;
    }
    return 'Something went wrong. Please try again.';
  }

  /* --- development-only mock (never used as a fallback) -------------------- */
  function mockRoutes() {
    return new Promise((resolve) => {
      const cx = CONFIG.map.center[0], cy = CONFIG.map.center[1];
      const line = (dx, n) => ({ type: 'LineString',
        coordinates: Array.from({ length: n }, (_, i) => [cx - 0.02 + (0.04 * i) / (n - 1), cy + dx * Math.sin((i / (n - 1)) * Math.PI)]) });
      setTimeout(() => resolve({
        hour_used: 13, walker: 'adult',
        routes: [
          { type: 'fastest', minutes: 15, distance_km: 1.1, shade_percent: 18, exposure_index: 70, geojson: line(0.002, 24) },
          { type: 'balanced', minutes: 17, distance_km: 1.3, shade_percent: 45, exposure_index: 50, geojson: line(0.008, 24) },
          { type: 'coolest', minutes: 20, distance_km: 1.5, shade_percent: 72, exposure_index: 30, geojson: line(0.014, 24) }],
        advice: { en: 'DEVELOPMENT MOCK advice.', hi: '\u0921\u0947\u0935\u0932\u092a\u092e\u0947\u0902\u091f \u092e\u0949\u0915\u0964' },
        advice_source: 'template',
        disclaimer: 'DEVELOPMENT MOCK \u2014 not from the API.',
        graph_meta: { pilot: 'DEVELOPMENT MOCK' },
      }), 400);
    });
  }
  const useMock = (typeof location !== 'undefined' && /[?&]mock=1\b/.test(location.search)) || CONFIG.dev.useMockRoutes;

  /* --- language toggle ----------------------------------------------------- */
  bEn.addEventListener('click', () => setLang('en'));
  bHi.addEventListener('click', () => setLang('hi'));

  /* --- submit -------------------------------------------------------------- */
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (inFlight) return;                     // prevent duplicate submissions

    if (typeof CHHAON === 'undefined') {
      showError('The API client failed to load. Please reload the page.');
      return;
    }
    if (statusEl) { statusEl.classList.remove('error'); statusEl.textContent = ''; }

    let req;
    try {
      req = CHHAON.buildRouteRequest({
        startText: inStart.value,
        endText: inDest.value,
        hourValue: inTime.value,
        walker: inWho.value,
      }, state.meta);
    } catch (err) {
      showError(errorMessage(err));
      return;
    }

    // Reset previous output so stale results are never shown as current.
    setBusy(true);
    resultsEl.innerHTML = '';
    if (resultsMetaEl) resultsMetaEl.textContent = '';
    adviceBox.hidden = true;
    state.selected = null;
    state.routes = null;
    clearMapRoutes();

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    inFlight = controller;
    try {
      const payload = useMock
        ? await mockRoutes()
        : await CHHAON.fetchRoutes(req, controller.signal);
      const res = CHHAON.normalizeRoutes(payload);
      if (!res.routes.length) {
        showError('No usable route alternatives were returned. Try different points or another time of day.');
        return;
      }
      state.routes = res.routes;
      state.req = req;
      renderResults(res, req);
    } catch (err) {
      if (err && err.name === 'AbortError') {
        showError('The route service took too long to respond. Please try again.');
      } else {
        showError(errorMessage(err));
      }
    } finally {
      clearTimeout(timer);
      inFlight = null;
      setBusy(false);
    }
  });

  // Opt-in read-only debug hook for automated tests (?debug=1). Not exposed
  // during normal use.
  if (typeof location !== 'undefined' && /[?&]debug=1\b/.test(location.search)) {
    window.__chhaon = {
      state: state,
      mapReady: function () { return mapReady; },
      styleName: function () {
        return map && map.getStyle && map.getStyle().name ? map.getStyle().name : '';
      },
      layerIds: function () {
        return map && map.getStyle && map.getStyle().layers
          ? map.getStyle().layers.map(function (l) { return l.id; })
          : [];
      },
    };
  }
})();
