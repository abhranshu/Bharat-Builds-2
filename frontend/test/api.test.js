/* Chhaon frontend API-layer tests.
 *
 * Run with:   node --test frontend/test
 *
 * The first block is pure unit logic (no network). The second block talks to
 * the real deployed API and is skipped automatically when offline by checking
 * a successful /meta first.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const CHHAON = require(path.join(__dirname, "..", "api.js"));

/* ------------------------------------------------------------------ *
 * Pure unit tests
 * ------------------------------------------------------------------ */
test("API base URL is the production endpoint", () => {
  assert.equal(
    CHHAON.API_BASE_URL,
    "https://ie5jb9r5u2.execute-api.ap-south-1.amazonaws.com/prod"
  );
  assert.equal(CHHAON.apiUrl("/route"), CHHAON.API_BASE_URL + "/route");
});

test("pilot places are well-formed and unique", () => {
  assert.ok(CHHAON.PILOT_PLACES.length >= 8);
  const seen = new Set();
  for (const p of CHHAON.PILOT_PLACES) {
    assert.equal(typeof p.name, "string");
    assert.ok(p.name.length > 0);
    assert.ok(isFinite(p.lat) && Math.abs(p.lat) <= 90);
    assert.ok(isFinite(p.lon) && Math.abs(p.lon) <= 180);
    assert.ok(!seen.has(p.name), "duplicate place name: " + p.name);
    seen.add(p.name);
  }
});

test("parseLatLon accepts comma and space separated coordinates", () => {
  assert.deepEqual(CHHAON.parseLatLon("28.6310, 77.2190"), { lat: 28.631, lon: 77.219 });
  assert.deepEqual(CHHAON.parseLatLon("28.6310 77.2190"), { lat: 28.631, lon: 77.219 });
  assert.equal(CHHAON.parseLatLon("not coords"), null);
  assert.equal(CHHAON.parseLatLon("91, 77"), null); // lat out of range
  assert.equal(CHHAON.parseLatLon(""), null);
});

test("resolveLocation handles places, coordinates and bad input", () => {
  const p = CHHAON.resolveLocation("connaught place (centre)", "starting point");
  assert.equal(p.kind, "place");
  assert.equal(p.lat, 28.6314);

  const partial = CHHAON.resolveLocation("Palika", "starting point");
  assert.equal(partial.kind, "place");
  assert.equal(partial.label, "Palika Bazaar");

  const c = CHHAON.resolveLocation("28.6310, 77.2190", "starting point");
  assert.equal(c.kind, "coords");
  assert.equal(c.lat, 28.631);

  assert.throws(() => CHHAON.resolveLocation("", "destination"), /Enter a destination/);
  assert.throws(() => CHHAON.resolveLocation("Atlantis", "destination"), /not a known place/);
});

test("parseHour parses HH:MM and bare hours and rejects bad values", () => {
  assert.equal(CHHAON.parseHour("13:00"), 13);
  assert.equal(CHHAON.parseHour("13"), 13);
  assert.equal(CHHAON.parseHour("08:30"), 8);
  assert.equal(CHHAON.parseHour("0"), 0);
  assert.throws(() => CHHAON.parseHour("24"), /between 0 and 23/);
  assert.throws(() => CHHAON.parseHour(""), /time of day/);
  assert.throws(() => CHHAON.parseHour("abc"), /valid time/);
});

test("validateWalker accepts only backend profiles", () => {
  for (const w of ["adult", "elderly", "child", "vendor"]) {
    assert.equal(CHHAON.validateWalker(w), w);
  }
  assert.throws(() => CHHAON.validateWalker("dog"), /profiles/);
});

test("insideBbox mirrors the backend margin", () => {
  const meta = { bbox: [28.6224179, 77.2094901, 28.640291, 77.2299026] };
  assert.equal(CHHAON.insideBbox(meta, 28.6314, 77.2197), true);
  assert.equal(CHHAON.insideBbox(meta, 19.076, 72.877), false); // Mumbai
  assert.equal(CHHAON.insideBbox(null, 0, 0), true);            // no meta -> defer to server
});
test("filterPilotPlaces returns the curated list and filters by substring", () => {
  assert.equal(CHHAON.filterPilotPlaces("").length, CHHAON.PILOT_PLACES.length);
  const hits = CHHAON.filterPilotPlaces("connaught");
  assert.ok(hits.length >= 2 && hits.every((p) => /connaught/i.test(p.name)));
  assert.equal(CHHAON.filterPilotPlaces("zzz-no-such-place").length, 0);
});

test("pinned dropdown coordinates win in buildRouteRequest", () => {
  const req = CHHAON.buildRouteRequest(
    {
      startText: "Somewhere I typed",
      endText: "Agrasen ki Baoli",
      startPinned: { lat: 28.6314, lon: 77.2197 },
      endPinned: null,
      hourValue: "13:00",
      walker: "adult",
    },
    null
  );
  assert.deepEqual(req.start, { lat: 28.6314, lon: 77.2197 });
});

test("searchPlaces rejects empty queries without network", async () => {
  const out = await CHHAON.searchPlaces("   ", null, undefined);
  assert.deepEqual(out, []);
});


test("buildRouteRequest produces the exact backend schema", () => {
  const req = CHHAON.buildRouteRequest(
    {
      startText: "Connaught Place (centre)",
      endText: "Agrasen ki Baoli",
      hourValue: "13:00",
      walker: "elderly",
    },
    null
  );
  assert.deepEqual(Object.keys(req).sort(), ["end", "hour", "labels", "start", "walker"]);
  assert.equal(req.hour, 13);
  assert.equal(req.walker, "elderly");
  assert.deepEqual(Object.keys(req.start).sort(), ["lat", "lon"]);
});

test("buildRouteRequest rejects empty, unknown and out-of-area input", () => {
  const base = {
    startText: "Connaught Place (centre)",
    endText: "Agrasen ki Baoli",
    hourValue: "13:00",
    walker: "adult",
  };
  assert.throws(() => CHHAON.buildRouteRequest({ ...base, startText: "" }, null), /starting point/);
  assert.throws(() => CHHAON.buildRouteRequest({ ...base, endText: "Nowhere" }, null), /not a known place/);
  assert.throws(() => CHHAON.buildRouteRequest({ ...base, hourValue: "99" }, null), /between 0 and 23/);
  assert.throws(() => CHHAON.buildRouteRequest({ ...base, walker: "x" }, null), /profiles/);
  const meta = { bbox: [28.6224179, 77.2094901, 28.640291, 77.2299026] };
  assert.throws(
    () => CHHAON.buildRouteRequest({ ...base, endText: "19.076, 72.877" }, meta),
    /outside the pilot area/
  );
  assert.throws(
    () => CHHAON.buildRouteRequest({ ...base, endText: base.startText }, meta),
    /same/
  );
});

test("normalizeRoutes maps the real response shape", () => {
  const payload = {
    hour_used: 13,
    walker: "elderly",
    routes: [
      { type: "fastest", minutes: 20.5, distance_km: 1.2, shade_percent: 6.2, exposure_index: 70,
        geojson: { type: "LineString", coordinates: [[77.21, 28.63], [77.22, 28.64]] } },
      { type: "balanced", minutes: 20.9, distance_km: 1.22, shade_percent: 32.2, exposure_index: 40,
        sun_exposure_reduction_vs_fastest_percent: 20, geojson: { type: "LineString", coordinates: [[77.21, 28.63], [77.23, 28.64]] } },
      { type: "coolest", minutes: 20.9, distance_km: 1.22, shade_percent: 32.2, exposure_index: 40,
        same_as: "balanced", geojson: { type: "LineString", coordinates: [[77.21, 28.63], [77.23, 28.64]] } },
    ],
    advice: { en: "hello", hi: "नमस्ते" },
    advice_source: "bedrock",
    disclaimer: "Estimated values...",
    graph_meta: { pilot: "Connaught Place, Delhi", built: "2026-10-10T18:15:07+00:00", methods: {} },
  };
  const res = CHHAON.normalizeRoutes(payload);
  assert.equal(res.routes.length, 3);
  assert.equal(res.routes[0].type, "fastest");
  assert.equal(res.routes[0].minutes, 20.5);
  assert.equal(res.routes[1].reductionVsFastest, 20);
  assert.equal(res.routes[2].sameAs, "balanced");
  assert.equal(res.advice.hi, "नमस्ते");
  assert.equal(res.adviceSource, "bedrock");
  assert.equal(res.pilot, "Connaught Place, Delhi");
});

test("normalizeRoutes tolerates missing and malformed data", () => {
  assert.equal(CHHAON.normalizeRoutes({}).routes.length, 0);
  assert.equal(CHHAON.normalizeRoutes({ routes: [] }).routes.length, 0);
  assert.equal(CHHAON.normalizeRoutes({ routes: "nope" }).routes.length, 0);
  // Malformed entries (no valid `type`) are dropped; valid ones survive.
  const mixed = CHHAON.normalizeRoutes({
    routes: [null, { minutes: 5 }, { type: "fastest", minutes: "10.2", distance_km: "1.0",
      shade_percent: null, geojson: {} }],
  });
  assert.equal(mixed.routes.length, 1);
  assert.equal(mixed.routes[0].minutes, 10.2);           // coerced number
  assert.equal(mixed.routes[0].shadePercent, null);
  assert.equal(mixed.routes[0].geometry, null);           // invalid geometry -> null, not crash
  // Advice without both languages still surfaces.
  const noAdvice = CHHAON.normalizeRoutes({ routes: [], advice: { en: "x" } });
  assert.deepEqual(noAdvice.advice, { en: "x", hi: "" });
  assert.throws(() => CHHAON.normalizeRoutes(null), /unexpected response/);
});

test("format helpers render values or an em dash", () => {
  assert.equal(CHHAON.formatMinutes(20.5), "20.5 min");
  assert.equal(CHHAON.formatKm(1.2), "1.20 km");
  assert.equal(CHHAON.formatPercent(6.25), "6.3%");
  assert.equal(CHHAON.formatHour(9), "09:00");
  assert.equal(CHHAON.formatMinutes(null), "—");
});

/* ------------------------------------------------------------------ *
 * Live integration tests against the deployed API
 * ------------------------------------------------------------------ */
let online = false;
test("live: /meta is reachable and reports the real graph", async () => {
  let meta;
  try {
    meta = await CHHAON.fetchMeta();
  } catch (err) {
    console.error("  (skipping live tests: API unreachable -", err.message + ")");
    return;
  }
  online = true;
  assert.equal(meta.pilot, "Connaught Place, Delhi");
  assert.equal(meta.is_sample, false);
  assert.ok(Array.isArray(meta.bbox) && meta.bbox.length === 4);
  assert.ok(Array.isArray(meta.available_hours) && meta.available_hours.includes(13));
  assert.deepEqual(meta.walker_types.sort(), ["adult", "child", "elderly", "vendor"]);
  assert.match(meta.disclaimer, /estimate/i);
});

test("live: /health is publicly reachable", async () => {
  if (!online) return;
  const h = await CHHAON.fetchHealth();
  assert.equal(h.status, "ok");
  assert.equal(h.graph_loaded, true);
});

test("live: /route returns three real alternatives for the default trip", async () => {
  if (!online) return;
  const req = CHHAON.buildRouteRequest(
    { startText: "Connaught Place (centre)", endText: "Agrasen ki Baoli", hourValue: "13:00", walker: "elderly" },
    await CHHAON.fetchMeta()
  );
  const res = CHHAON.normalizeRoutes(await CHHAON.fetchRoutes(req));
  assert.equal(res.routes.length, 3);
  assert.deepEqual(res.routes.map((r) => r.type), ["fastest", "balanced", "coolest"]);
  for (const r of res.routes) {
    assert.ok(r.minutes > 0 && r.minutes < 200, r.type + " minutes");
    assert.ok(r.geometry && r.geometry.coordinates.length >= 2, r.type + " geometry");
    assert.ok(r.shadePercent >= 0 && r.shadePercent <= 100);
    assert.ok(r.exposureIndex >= 0 && r.exposureIndex <= 100);
  }
  assert.equal(res.walker, "elderly");
  assert.ok(res.hourUsed >= 8 && res.hourUsed <= 18);
  assert.ok(res.advice && res.advice.en.length > 0);
});

test("live: an out-of-pilot destination is rejected with a clear message", async () => {
  if (!online) return;
  const req = CHHAON.buildRouteRequest(
    { startText: "Connaught Place (centre)", endText: "19.076, 72.877", hourValue: "13:00", walker: "elderly" },
    null // bypass the client-side bbox check so the server is the authority
  );
  await assert.rejects(() => CHHAON.fetchRoutes(req), (err) => {
    assert.equal(err.name, "ApiError");
    assert.equal(err.kind, "http");
    assert.equal(err.status, 400);
    return true;
  });
});
