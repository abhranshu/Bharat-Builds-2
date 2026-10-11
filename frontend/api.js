/* CHHAON — API integration layer.
 *
 * Pure, DOM-free logic that connects the frontend to the deployed Chhaon API:
 *   - one central place for the API base URL
 *   - an HTTP helper that parses JSON and turns HTTP/network/parse failures
 *     into typed errors the UI can show
 *   - the pilot-area place list and location/input validation
 *   - normalization of the backend's /route response into UI-friendly objects
 *
 * It is written as a classic script (no build step) that also works under
 * Node's CommonJS loader, so the same code can be unit-tested without a browser.
 *
 * SECURITY: this file holds no credentials and never will. The Chhaon API is
 * public and unauthenticated; secrets must never live in frontend code.
 */
const CHHAON = (function () {
  "use strict";

  /* --------------------------------------------------------------------- *
   * Configuration — the single source of truth for the API location.
   * --------------------------------------------------------------------- */
  const API_BASE_URL =
    "https://ie5jb9r5u2.execute-api.ap-south-1.amazonaws.com/prod";

  // Same public chhaon-web key embedded for the basemap in app.js
  // CONFIG.map.styleUrl (?key=...). Fallback only — app.js sets the live
  // value via setPlacesApiKey() parsed from the style URL.
  const DEFAULT_PLACES_KEY =
    "v1.public.eyJqdGkiOiJmMGQ4MmM2NC05ZDNmLTRlMzEtYjkyYS1jOTMyYjVlNTljZTAifTNF3fwdkkxYtgyb-Ez8sYr5qYjFrXr_nuCngBU1g6osEt7w7wGJFz59IGtjcSvR63d_pVRlR0S0tKuCkbLuhZ-4BSqRmA7NZ9Iud8GLJcULmsgfs9CcG_X4Y6hLRYTAZtRX9NLCNsn8qB5vlHASWDjgDCCXS2F9Jvwy4Q7J16ji3LoYtJGzjZ9cy7S9IlU1U8IB86tK0K7s9Xlmmz7h_0rZNM2hfRVp6Y5XclELrKndhiU2oJKj3_69QQ2PmK66Oe_AxjTcgSA8kL0aQa9C1ElowTul4SAu1v58LfcR0yDZ2hPZK3bIMtolqM--VkFBDFh3ysV-PlWJ8QvbxH30tz4.Njg1MGZlZTUtYTI2ZS00MDdlLWJjNDktMDNmZDlkNzVmMjQ0";

  // Endpoint paths (joined onto API_BASE_URL; never duplicated in full).
  const PATHS = { health: "/health", meta: "/meta", route: "/route" };

  // Pedestrian profiles accepted by the backend (src/api/app.py CONFIG.walkers).
  const WALKERS = ["adult", "elderly", "child", "vendor"];
  const WALKER_LABELS = {
    adult: "Adult",
    elderly: "Elderly",
    child: "Child",
    vendor: "Street vendor",
  };

  // The three route alternatives the backend always returns, in order.
  const ROUTE_TYPES = ["fastest", "balanced", "coolest"];
  const ROUTE_LABELS = {
    fastest: "Fastest",
    balanced: "Balanced",
    coolest: "Coolest",
  };

  /* --------------------------------------------------------------------- *
   * Pilot places — real landmarks inside the Connaught Place pilot area.
   *
   * Every coordinate below was checked against the deployed graph: each sits
   * inside the pilot bbox and within ~60 m of a graph node (well inside the
   * backend's 300 m snap limit), so all of them route successfully.
   * --------------------------------------------------------------------- */
  const PILOT_PLACES = [
    { name: "Connaught Place (centre)", lat: 28.6314, lon: 77.2197 },
    { name: "Central Park, Connaught Place", lat: 28.6304, lon: 77.2205 },
    { name: "Palika Bazaar", lat: 28.6315, lon: 77.2183 },
    { name: "Regal Cinema", lat: 28.6313, lon: 77.2194 },
    { name: "Rajiv Chowk Metro Station", lat: 28.6328, lon: 77.2197 },
    { name: "Janpath Market", lat: 28.6276, lon: 77.2195 },
    { name: "Jantar Mantar", lat: 28.6271, lon: 77.2166 },
    { name: "Agrasen ki Baoli", lat: 28.6273, lon: 77.2258 },
    { name: "Hanuman Mandir, Baba Kharak Singh Marg", lat: 28.6306, lon: 77.2155 },
    { name: "Barakhamba Road", lat: 28.6315, lon: 77.2278 },
    { name: "Tolstoy Marg", lat: 28.6284, lon: 77.2240 },
    { name: "Kasturba Gandhi Marg", lat: 28.6285, lon: 77.2255 },
  ];

  /* --------------------------------------------------------------------- *
   * Errors
   * --------------------------------------------------------------------- */
  // kind: "network" | "http" | "invalid-response" | "input"
  class ApiError extends Error {
    constructor(kind, message, extra) {
      super(message);
      this.name = "ApiError";
      this.kind = kind;
      if (extra) Object.assign(this, extra);
    }
  }

  function inputError(message) {
    return new ApiError("input", message);
  }

  /* --------------------------------------------------------------------- *
   * URL + request helpers
   * --------------------------------------------------------------------- */
  function apiUrl(path) {
    return API_BASE_URL + path;
  }

  async function apiRequest(path, options) {
    const opts = Object.assign({}, options || {});
    let response;
    try {
      response = await fetch(apiUrl(path), opts);
    } catch (err) {
      if (err && err.name === "AbortError") throw err;
      throw new ApiError(
        "network",
        "Could not reach the route service. Check your connection and try again.",
        { cause: err }
      );
    }

    // Read as text first: API Gateway may return HTML/XML for some failures,
    // so a blind response.json() would throw an unhelpful parse error.
    let text = "";
    try {
      text = await response.text();
    } catch (err) {
      throw new ApiError(
        "network",
        "The route service closed the connection unexpectedly. Please try again.",
        { cause: err }
      );
    }

    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (err) {
        if (response.ok) {
          throw new ApiError(
            "invalid-response",
            "The server returned an invalid response.",
            { cause: err, status: response.status }
          );
        }
        data = null;
      }
    }

    if (!response.ok) {
      const message =
        (data && (data.error || data.message)) ||
        "Request failed with status " + response.status + ".";
      throw new ApiError("http", message, { status: response.status, data: data });
    }

    return data;
  }

  function fetchMeta(signal) {
    return apiRequest(PATHS.meta, { method: "GET", signal: signal });
  }

  function fetchHealth(signal) {
    return apiRequest(PATHS.health, { method: "GET", signal: signal });
  }

  // Sends exactly the shape the backend validates:
  //   { start:{lat,lon}, end:{lat,lon}, hour:int, walker:enum }
  function fetchRoutes(req, signal) {
    return apiRequest(PATHS.route, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start: { lat: req.start.lat, lon: req.start.lon },
        end: { lat: req.end.lat, lon: req.end.lon },
        hour: req.hour,
        walker: req.walker,
      }),
      signal: signal,
    });
  }
  /* --------------------------------------------------------------------- *
   * Amazon Location Service — geo-places v2 SearchText (key auth).
   *
   * Uses the SAME public `chhaon-web` key the basemap already embeds
   * (scoped to geo-maps and geo-places). Key travels as `?key=` query
   * param — the documented browser pattern, never a secret credential.
   * --------------------------------------------------------------------- */
  var PLACES_HOST = "https://places.geo.ap-south-1.amazonaws.com";
  var PILOT_BIAS = [77.2197, 28.6314]; // [lon, lat] Connaught Place centre
  var PILOT_BIAS_RADIUS_M = 2500;

  // Filled in by app.js from the styleUrl's ?key= value; falls back to the
  // embedded key so unit tests and direct file opens still work.
  var placesApiKey = null;

  function setPlacesApiKey(k) {
    if (typeof k === "string" && k) placesApiKey = k;
  }

  function placesKey() {
    return placesApiKey || DEFAULT_PLACES_KEY;
  }

  /* SearchText -> [{placeId,title,address,lat,lon}] filtered to pilot bbox.
   * Throws ApiError("network"|"http") on failure; never throws on empty. */
  function searchPlaces(query, meta, signal) {
    var q = String(query == null ? "" : query).trim();
    if (!q) return Promise.resolve([]);
    var url = PLACES_HOST + "/v2/search-text?key=" + encodeURIComponent(placesKey());
    var body = {
      QueryText: q,
      MaxResults: 8,
      Language: "en",
      BiasPosition: PILOT_BIAS,
      Filter: { Circle: { Center: PILOT_BIAS, Radius: PILOT_BIAS_RADIUS_M } },
    };
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: signal,
    })
      .then(function (resp) {
        if (!resp.ok) {
          throw new ApiError(
            "http",
            "Place search failed (HTTP " + resp.status + ").",
            { status: resp.status }
          );
        }
        return resp.json();
      })
      .then(function (data) {
        var items = data && Array.isArray(data.ResultItems) ? data.ResultItems : [];
        var out = [];
        for (var i = 0; i < items.length; i++) {
          var it = items[i] || {};
          var pos = Array.isArray(it.Position) ? it.Position : null; // [lon, lat]
          if (!pos || pos.length < 2) continue;
          var lon = Number(pos[0]);
          var lat = Number(pos[1]);
          if (!isFinite(lat) || !isFinite(lon)) continue;
          if (!insideBbox(meta, lat, lon)) continue; // keep pilot-only promise
          var addr = it.Address || {};
          var free = addr.FreeText || addr.Label || "";
          out.push({
            placeId: typeof it.PlaceId === "string" ? it.PlaceId : "",
            title: typeof it.Title === "string" ? it.Title : q,
            address: typeof free === "string" ? free : "",
            lat: lat,
            lon: lon,
            kind: "suggest",
          });
        }
        return out;
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") throw err;
        if (err instanceof ApiError) throw err;
        throw new ApiError("network", "Place search is unreachable. Check your connection and try again.");
      });
  }

  /* Local substring filter over the curated pilot list (instant, offline). */
  function filterPilotPlaces(query) {
    var q = normalizeName(query);
    if (!q) return PILOT_PLACES.slice();
    return PILOT_PLACES.filter(function (p) {
      return normalizeName(p.name).indexOf(q) !== -1;
    });
  }


  /* --------------------------------------------------------------------- *
   * Input parsing / validation
   * --------------------------------------------------------------------- */
  function normalizeName(s) {
    return String(s).trim().toLowerCase().replace(/\s+/g, " ");
  }

  function findPlace(text) {
    const q = normalizeName(text);
    if (!q) return null;
    // Exact (case/space-insensitive) match first, then a unique prefix match.
    let exact = PILOT_PLACES.find((p) => normalizeName(p.name) === q);
    if (exact) return exact;
    const partial = PILOT_PLACES.filter((p) =>
      normalizeName(p.name).indexOf(q) === 0
    );
    return partial.length === 1 ? partial[0] : null;
  }

  // Accepts "28.6314,77.2197" or "28.6314 77.2197".
  function parseLatLon(text) {
    const m = String(text).trim().match(
      /^(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)$/
    );
    if (!m) return null;
    const lat = Number(m[1]);
    const lon = Number(m[2]);
    if (!isFinite(lat) || !isFinite(lon)) return null;
    if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
    return { lat: lat, lon: lon };
  }

  /* Resolve one field to a coordinate. `pinned` is the {lat, lon} stored on
   * the input's data attributes when the user picks a dropdown option, so
   * the label can never disconnect from submitted coordinates. Text
   * matching (place name / lat,lon) remains as the fallback path. */
  function resolveLocation(text, fieldLabel, pinned) {
    const label = fieldLabel || "location";
    if (typeof text !== "string" || !text.trim()) {
      throw inputError("Enter a " + label + ".");
    }
    const raw = text.trim();
    // Pinned dropdown selection wins: coordinates the service returned.
    if (pinned && isFinite(pinned.lat) && isFinite(pinned.lon)) {
      return { lat: pinned.lat, lon: pinned.lon, label: raw, kind: "picked" };
    }
    const place = findPlace(raw);
    if (place) {
      return { lat: place.lat, lon: place.lon, label: place.name, kind: "place" };
    }
    const ll = parseLatLon(raw);
    if (ll) {
      return {
        lat: ll.lat,
        lon: ll.lon,
        label: formatLatLon(ll.lat, ll.lon),
        kind: "coords",
      };
    }
    throw inputError(
      '"' +
        raw +
        '" is not a known place in the pilot area. Pick one from the list, or ' +
        "enter coordinates as lat,lon (e.g. 28.6310, 77.2190)."
    );
  }

  function formatLatLon(lat, lon) {
    return Number(lat).toFixed(4) + ", " + Number(lon).toFixed(4);
  }

  // "13:00" | "13" -> 13. Backend accepts 0..23 and clamps to its own hours.
  function parseHour(value) {
    if (value === null || value === undefined || value === "") {
      throw inputError("Choose a time of day.");
    }
    const s = String(value).trim();
    const m = s.match(/^(\d{1,2})(?::(\d{2}))?$/);
    if (!m) throw inputError("Enter a valid time of day (e.g. 13:00).");
    const hour = Number(m[1]);
    if (!Number.isInteger(hour) || hour < 0 || hour > 23) {
      throw inputError("The hour must be between 0 and 23.");
    }
    return hour;
  }

  function validateWalker(walker) {
    if (WALKERS.indexOf(walker) === -1) {
      throw inputError("Choose who is walking from the available profiles.");
    }
    return walker;
  }

  // Mirrors the backend's bbox margin (CONFIG.bbox_margin_m = 300 m) so we do
  // not reject a point the server would accept.
  function insideBbox(meta, lat, lon, marginM) {
    if (!meta || !Array.isArray(meta.bbox) || meta.bbox.length !== 4) return true;
    const margin = (typeof marginM === "number" ? marginM : 300) / 111320.0;
    const lonMargin = margin / Math.max(0.2, Math.cos((lat * Math.PI) / 180));
    const minlat = meta.bbox[0] - margin;
    const minlon = meta.bbox[1] - lonMargin;
    const maxlat = meta.bbox[2] + margin;
    const maxlon = meta.bbox[3] + lonMargin;
    return lat >= minlat && lat <= maxlat && lon >= minlon && lon <= maxlon;
  }

  /* Turn the form's raw strings into a validated /route request body.
   * Throws "input" errors with user-facing messages. */
  function buildRouteRequest(input, meta) {
    const start = resolveLocation(input.startText, "starting point", input.startPinned);
    const end = resolveLocation(input.endText, "destination", input.endPinned);
    const hour = parseHour(input.hourValue);
    const walker = validateWalker(input.walker);

    if (meta) {
      if (!insideBbox(meta, start.lat, start.lon)) {
        throw inputError("The starting point is outside the pilot area.");
      }
      if (!insideBbox(meta, end.lat, end.lon)) {
        throw inputError("The destination is outside the pilot area.");
      }
    }
    if (start.lat === end.lat && start.lon === end.lon) {
      throw inputError("The starting point and destination are the same.");
    }
    return {
      start: { lat: start.lat, lon: start.lon },
      end: { lat: end.lat, lon: end.lon },
      hour: hour,
      walker: walker,
      labels: { start: start.label, end: end.label },
    };
  }

  /* --------------------------------------------------------------------- *
   * Response normalization
   * --------------------------------------------------------------------- */
  function num(v) {
    // Keep null/undefined/"" as null: Number(null) === 0 would otherwise show a
    // missing metric as a real zero.
    if (v === null || v === undefined || v === "") return null;
    const n = typeof v === "number" ? v : Number(v);
    return isFinite(n) ? n : null;
  }

  function validLineString(geom) {
    return !!(
      geom &&
      geom.type === "LineString" &&
      Array.isArray(geom.coordinates) &&
      geom.coordinates.length >= 2
    );
  }

  function normalizeRoute(r) {
    if (!r || typeof r !== "object") return null;
    // The backend always sends `type`; entries without one are malformed and
    // are skipped rather than guessed at by position.
    const type = typeof r.type === "string" && r.type ? r.type : null;
    if (!type) return null;
    return {
      type: type,
      label: ROUTE_LABELS[type] || type,
      minutes: num(r.minutes),
      distanceKm: num(r.distance_km),
      shadePercent: num(r.shade_percent),
      exposureIndex: num(r.exposure_index),
      reductionVsFastest: num(r.sun_exposure_reduction_vs_fastest_percent),
      sameAs: typeof r.same_as === "string" ? r.same_as : null,
      // Backend geo coordinates are already [lon, lat] GeoJSON order.
      geometry: validLineString(r.geojson) ? r.geojson : null,
    };
  }

  /* Normalize the /route response. Tolerates missing optional fields and
   * malformed route entries (they are skipped) without throwing. */
  function normalizeRoutes(payload) {
    if (!payload || typeof payload !== "object") {
      throw new ApiError(
        "invalid-response",
        "The server returned an unexpected response."
      );
    }
    const rawRoutes = Array.isArray(payload.routes) ? payload.routes : [];
    const routes = rawRoutes
      .map(normalizeRoute)
      .filter((r) => r && r.type);

    const advice = payload.advice && typeof payload.advice === "object"
      ? { en: str(payload.advice.en), hi: str(payload.advice.hi) }
      : null;

    const gm = payload.graph_meta && typeof payload.graph_meta === "object"
      ? payload.graph_meta
      : {};

    return {
      hourUsed: num(payload.hour_used),
      walker: typeof payload.walker === "string" ? payload.walker : null,
      routes: routes,
      advice: advice,
      adviceSource:
        typeof payload.advice_source === "string" ? payload.advice_source : null,
      disclaimer: str(payload.disclaimer),
      pilot: str(gm.pilot),
      graphBuilt: str(gm.built),
      methods: gm.methods && typeof gm.methods === "object" ? gm.methods : {},
    };
  }

  function str(v) {
    return typeof v === "string" ? v : "";
  }

  /* --------------------------------------------------------------------- *
   * Display helpers
   * --------------------------------------------------------------------- */
  function formatMinutes(v) {
    return v === null || v === undefined ? "—" : Number(v).toFixed(1) + " min";
  }

  function formatKm(v) {
    return v === null || v === undefined ? "—" : Number(v).toFixed(2) + " km";
  }

  function formatPercent(v) {
    return v === null || v === undefined ? "—" : Number(v).toFixed(1) + "%";
  }

  function formatHour(h) {
    if (h === null || h === undefined) return "";
    const hh = String(Math.trunc(h)).padStart(2, "0");
    return hh + ":00";
  }

  return {
    API_BASE_URL: API_BASE_URL,
    PATHS: PATHS,
    WALKERS: WALKERS,
    WALKER_LABELS: WALKER_LABELS,
    ROUTE_TYPES: ROUTE_TYPES,
    ROUTE_LABELS: ROUTE_LABELS,
    PILOT_PLACES: PILOT_PLACES,
    ApiError: ApiError,
    apiUrl: apiUrl,
    apiRequest: apiRequest,
    fetchMeta: fetchMeta,
    fetchHealth: fetchHealth,
    fetchRoutes: fetchRoutes,
    findPlace: findPlace,
    filterPilotPlaces: filterPilotPlaces,
    searchPlaces: searchPlaces,
    setPlacesApiKey: setPlacesApiKey,
    parseLatLon: parseLatLon,
    resolveLocation: resolveLocation,
    parseHour: parseHour,
    validateWalker: validateWalker,
    insideBbox: insideBbox,
    buildRouteRequest: buildRouteRequest,
    normalizeRoutes: normalizeRoutes,
    formatLatLon: formatLatLon,
    formatMinutes: formatMinutes,
    formatKm: formatKm,
    formatPercent: formatPercent,
    formatHour: formatHour,
  };
})();

// Expose for Node-based unit tests; harmless in the browser.
if (typeof module !== "undefined" && module.exports) {
  module.exports = CHHAON;
}
