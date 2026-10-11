/* Chhaon browser end-to-end check (headless Chrome over the DevTools Protocol).
 *
 * Run with:   node frontend/test/browser.e2e.mjs
 *
 * Serves the repo over http://127.0.0.1:8787, opens the real page in headless
 * Chrome, and drives the actual UI: metadata load, invalid input, a real route
 * request, route selection, and a blocked-network failure. Exits non-zero if
 * any check fails. Requires Chrome and internet access.
 */
import http from "node:http";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..", "..");
const PORT = 8787;
const CDP_PORT = 9333;
// Point the test at an already-deployed site instead of the local server:
//   E2E_URL=https://example.cloudfront.net/ node frontend/test/browser.e2e.mjs
const EXTERNAL_URL = process.env.E2E_URL || null;
const PAGE_URL = EXTERNAL_URL
  ? EXTERNAL_URL + (EXTERNAL_URL.includes("?") ? "&" : "?") + "debug=1"
  : `http://127.0.0.1:${PORT}/frontend/index.html?debug=1`;

const results = [];
function check(name, ok, detail) {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"} - ${name}${detail ? "  :: " + detail : ""}`);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const TYPES = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
  ".json": "application/json", ".webp": "image/webp", ".svg": "image/svg+xml",
};

const server = http.createServer((req, res) => {
  const urlPath = decodeURIComponent((req.url || "/").split("?")[0]);
  if (urlPath === "/favicon.ico") { res.writeHead(204); return res.end(); }
  const rel = urlPath === "/" ? "/frontend/index.html" : urlPath;
  const file = path.join(ROOT, rel);
  if (!file.startsWith(ROOT)) { res.writeHead(403); return res.end("forbidden"); }
  fs.readFile(file, (err, data) => {
    if (err) { res.writeHead(404); return res.end("not found"); }
    res.writeHead(200, { "Content-Type": TYPES[path.extname(file).toLowerCase()] || "application/octet-stream" });
    res.end(data);
  });
});

function findChrome() {
  const candidates = [
    process.env.CHROME,
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  ].filter(Boolean);
  return candidates.find((p) => fs.existsSync(p)) || null;
}

function describeArgs(args) {
  return (args || []).map((a) => (a.value !== undefined ? String(a.value) : a.description || a.type)).join(" ");
}

class CDP {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map(); this.handlers = [];
    ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id !== undefined) {
        const p = this.pending.get(msg.id);
        if (p) { this.pending.delete(msg.id); msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result); }
      } else for (const h of this.handlers) h(msg);
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  on(fn) { this.handlers.push(fn); }
}

let chromeProc = null;
let userDir = null;

async function teardown() {
  try { if (chromeProc && chromeProc.pid) spawnSync("taskkill", ["/PID", String(chromeProc.pid), "/T", "/F"], { stdio: "ignore" }); } catch {}
  try { if (server.listening) server.close(); } catch {}
  try { if (userDir) fs.rmSync(userDir, { recursive: true, force: true }); } catch {}
}

async function main() {
  if (!EXTERNAL_URL) await new Promise((r) => server.listen(PORT, "127.0.0.1", r));

  const chrome = findChrome();
  if (!chrome) throw new Error("Chrome not found; set CHROME env var to chrome.exe");
  userDir = fs.mkdtempSync(path.join(os.tmpdir(), "chhaon-chrome-"));
  chromeProc = spawn(chrome, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--disable-extensions", `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${userDir}`,
    "about:blank",
  ], { stdio: "ignore" });

  // Wait for the DevTools endpoint and a page target.
  let target = null;
  for (let i = 0; i < 100 && !target; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
      const list = await r.json();
      target = list.find((t) => t.type === "page" && t.webSocketDebuggerUrl) || null;
    } catch {}
    if (!target) await sleep(200);
  }
  if (!target) throw new Error("Chrome DevTools endpoint did not come up");

  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.addEventListener("open", res);
    ws.addEventListener("error", () => rej(new Error("CDP socket error")));
  });
  const cdp = new CDP(ws);

  const consoleErrors = [];
  const warnings = [];
  const exceptions = [];
  const apiUrls = [];
  cdp.on((msg) => {
    if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") consoleErrors.push(describeArgs(msg.params.args));
    if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "warning") warnings.push(describeArgs(msg.params.args));
    if (msg.method === "Runtime.exceptionThrown") exceptions.push(msg.params.exceptionDetails?.exception?.description || msg.params.exceptionDetails?.text);
    if (msg.method === "Log.entryAdded" && msg.params.entry.level === "error") consoleErrors.push((msg.params.entry.url || "") + " " + msg.params.entry.text);
    if (msg.method === "Network.requestWillBeSent" && /execute-api/.test(msg.params.request.url)) apiUrls.push(msg.params.request.method + " " + msg.params.request.url);
  });

  await cdp.send("Runtime.enable");
  await cdp.send("Page.enable");
  await cdp.send("Log.enable");
  await cdp.send("Network.enable");

  const evalJs = async (expression) => {
    const r = await cdp.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error("page exception: " + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
    return r.result.value;
  };
  const waitFor = async (expr, timeout = 25000, interval = 250) => {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      const v = await evalJs(expr);
      if (v) return v;
      await sleep(interval);
    }
    throw new Error("timeout waiting for: " + expr);
  };

  // --- load ---------------------------------------------------------------
  await cdp.send("Page.navigate", { url: PAGE_URL });
  await waitFor("document.readyState === 'complete'");

  // --- Test A: production metadata ---------------------------------------
  const note = await waitFor(
    "(()=>{var e=document.getElementById('pilotNote');return e&&e.textContent&&e.textContent.indexOf('Loading')===-1?e.textContent:'';})()",
    20000
  );
  check("A: /meta loaded and shown", /Connaught Place/.test(note), note);
  check("A: real graph reported (not sample)", /real OpenStreetMap graph/.test(note) && !/sample/i.test(note), note);
  const places = await evalJs("document.querySelectorAll('#startList li[role=\"option\"], #destList li[role=\"option\"]').length");
  check("A: pilot-place list populated", places >= 8, "options=" + places);

  // --- Test D: invalid input ---------------------------------------------
  const apiBeforeInvalid = apiUrls.length;
  await evalJs("(()=>{document.getElementById('inDest').value='Nowhere at all';document.getElementById('findBtn').click();return true;})()");
  const invalidMsg = await waitFor("document.getElementById('formStatus').textContent || ''", 8000);
  check("D: invalid destination shows a clear message", /not a known place/.test(invalidMsg), invalidMsg);
  check("D: no route request sent for invalid input", apiUrls.length === apiBeforeInvalid, "requests=" + (apiUrls.length - apiBeforeInvalid));
  check("D: no results rendered for invalid input", (await evalJs("document.querySelectorAll('#results .route').length")) === 0);

  // --- Test B: real route request ----------------------------------------
  const apiBeforeRoute = apiUrls.length;
  await evalJs("(()=>{document.getElementById('inDest').value='Agrasen ki Baoli';document.getElementById('inWho').value='elderly';document.getElementById('inTime').value='13:00';document.getElementById('findBtn').click();return true;})()");
  await waitFor("document.querySelectorAll('#results .route').length===3", 30000);
  check("B: real POST /route was sent", apiUrls.slice(apiBeforeRoute).some((u) => u.startsWith("POST ") && u.includes("/route")), "reqs=" + JSON.stringify(apiUrls.slice(apiBeforeRoute)));

  const cards = await evalJs(
    "Array.from(document.querySelectorAll('#results .route')).map(c=>c.dataset.type+'|'+c.textContent.replace(/\\s+/g,' ').trim())"
  );
  check("B: three alternatives rendered", cards.length === 3, JSON.stringify(cards.map((c) => c.split("|")[0])));
  check("B: cards carry real metrics", cards.every((c) => /min/.test(c) && /km/.test(c) && /shade/.test(c)), cards.join(" ;; "));
  const metaLine = await evalJs("document.getElementById('resultsMeta').textContent");
  check("B: summary uses real pilot + trip", /Connaught Place/.test(metaLine) && /Agrasen/.test(metaLine), metaLine);
  const advice = await evalJs("document.getElementById('adviceText').textContent");
  const adviceSource = await evalJs("document.getElementById('adviceSource').textContent || 'bedrock'");
  check("B: advice rendered", advice.length > 0, "source=" + adviceSource);

  // --- Test C: route selection + map --------------------------------------
  await evalJs("document.querySelector('#results .route.coolest').click()");
  const selState = await evalJs(
    "JSON.stringify({cool:document.querySelector('#results .route.coolest').classList.contains('sel'),fast:document.querySelector('#results .route.fastest').classList.contains('sel'),pressed:document.querySelector('#results .route.coolest').getAttribute('aria-pressed')})"
  );
  const sel = JSON.parse(selState);
  check("C: selecting a route highlights only it", sel.cool && !sel.fast && sel.pressed === "true", selState);

  const mapReady = await evalJs("!!(window.__chhaon && window.__chhaon.mapReady())");
  const layers = await evalJs("window.__chhaon ? window.__chhaon.layerIds() : []");
  if (mapReady) {
    check("C: map has all three route layers", ["fastest", "balanced", "coolest"].every((id) => layers.includes(id)), "layers=" + JSON.stringify(layers));
    check("C: selected route is moved on top", layers[layers.length - 1] === "coolest", "top=" + layers[layers.length - 1]);
  } else {
    check("C: map finished loading", false, "MapLibre style did not finish loading; layer checks skipped");
  }
  const styleName = await evalJs("window.__chhaon ? window.__chhaon.styleName() : ''");
  check("C: basemap is the Amazon Location style (not the demotiles fallback)", /AmazonLocation/i.test(styleName), "style=" + styleName);
  if (warnings.length) console.log("  console warnings:\n    " + warnings.join("\n    "));

  // --- console cleanliness before the failure test ------------------------
  check("G: no uncaught JS exceptions in normal flow", exceptions.length === 0, exceptions.join(" | "));
  check("G: no console errors in normal flow", consoleErrors.length === 0, consoleErrors.slice(0, 4).join(" | "));

  // --- Test E: blocked network -> error, no mock --------------------------
  await cdp.send("Network.setBlockedURLs", { urls: ["*execute-api*"] });
  await evalJs("document.getElementById('findBtn').click()");
  const failMsg = await waitFor("/Could not reach|took too long to respond|route service/i.test(document.getElementById('formStatus').textContent) ? document.getElementById('formStatus').textContent : ''", 30000);
  check("E: network failure shows an error", /route service/i.test(failMsg), failMsg);
  check("E: previous results cleared on failure", (await evalJs("document.querySelectorAll('#results .route').length")) === 0);
  check("E: submit button re-enabled", (await evalJs("document.getElementById('findBtn').disabled===false")) === true);
  check("E: no mock data silently shown", !/DEVELOPMENT MOCK/.test(await evalJs("document.body.textContent")));
  await cdp.send("Network.setBlockedURLs", { urls: [] });

  // --- duplicate-submission guard ----------------------------------------
  const apiBeforeDup = apiUrls.length;
  await evalJs("(()=>{const b=document.getElementById('findBtn');b.click();b.click();b.click();return true;})()");
  await waitFor("document.querySelectorAll('#results .route').length===3", 30000);
  const dupPosts = apiUrls.slice(apiBeforeDup).filter((u) => u.startsWith("POST ") && u.includes("/route")).length;
  check("F: triple-click sends exactly one route request", dupPosts === 1, "POSTs=" + dupPosts);

  ws.close();
}

let failed = false;
try {
  await main();
} catch (err) {
  failed = true;
  console.error("E2E ERROR: " + (err && err.stack ? err.stack : err));
} finally {
  await teardown();
}

const passed = results.filter((r) => r.ok).length;
console.log(`\n${passed}/${results.length} browser checks passed`);
if (failed || passed !== results.length) process.exit(1);
