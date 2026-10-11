# Chhaon — remaining work, step by step

Everything in this file is work that is **still open**. The API itself is built,
deployed and passing its checks; what follows is the honest list of gaps plus the
exact commands to close each one.

---

## Reference: the deployed environment

Copy values from `aws cloudformation describe-stacks --stack-name chhaon --region ap-south-1`
if anything drifts.

| Thing | Value |
|---|---|
| Stack | `chhaon` (region `ap-south-1`, account `574748894960`) |
| API base URL | `https://ie5jb9r5u2.execute-api.ap-south-1.amazonaws.com/prod` |
| Lambda function | `chhaon-route-chhaon` — python3.12, 1024 MB, 15 s, x86_64 |
| API throttle | rate 10 req/s, burst 20 (stage `prod`) |
| Graph bucket (private) | `chhaon-graph-574748894960-ap-south-1` |
| Graph key | `graph.json.gz` (currently 38,006 bytes — the **synthetic sample**) |
| Request log table | `chhaon-RequestLogTable-P46U8QY6HJW4` (TTL: `expires_at`, 7 days) |
| Advice model | `global.amazon.nova-2-lite-v1:0` (cross-region inference profile) |
| CORS origin | `*` — **not yet tightened** |

`sam` is not on `PATH` in Git Bash here; it is installed as a Python package, so
invoke it as `python -m samcli …`. Its build step needs Docker, which is running
(`--use-container` is required so numpy ships as a Linux wheel).

---

## Status at a glance

| # | Item | State |
|---|---|---|
| 1 | Real graph from OSM (`build_graph.py`) | **Open** — the only unexercised deliverable |
| 2 | Tighten `AllowedOrigin` from `*` | **Open** — one command |
| 3 | Frontend still renders mock routes | **Open** — needs geocoding decision |
| 4 | Hindi quality (English words mixed in; template unreviewed) | **Open** — part code, part human |
| 5 | Inference residency (`global.` profile) | **Open** — one command |
| — | AWS Budget alert has no notifications | **Open** — housekeeping, see §6 |
| — | No git repository in `backend/` | **Open** — no rollback safety net |

---

## Step 1 — Build the real graph (`build_graph.py`)

Highest-value step: every number the demo shows depends on it. Currently the API
serves a synthetic 15×15 grid labelled `(SAMPLE)`.

### 1a. Install the offline dependencies

On Windows, conda-forge is far more reliable than pip for geopandas/rasterio
(prebuilt binaries, no GDAL compile):

```bash
conda create -n chhaon-graph python=3.12 -c conda-forge \
  osmnx geopandas shapely pyproj rasterio pandas pvlib astral -y
conda activate chhaon-graph
pip install numpy networkx boto3
```

pip-only alternative: `pip install -r scripts/requirements-offline.txt`.
Note these are **offline-only** deps — they never go into the Lambda package.

### 1b. Dry run locally, without uploading

The first pass downloads OSM data and may hit library API quirks (`osmnx` 2.x
renamed several functions), so keep it off S3 until you have seen it work.

```bash
cd backend
python scripts/build_graph.py --centre 28.6314 77.2197 --radius 1000 \
  --pilot "Connaught Place, Delhi" --no-upload --out data/graph.json.gz
```

Full option list (`python scripts/build_graph.py --help`): `--pilot`, `--centre LAT LON`,
`--radius`, `--date` (default `2026-05-15`), `--tz` (`Asia/Kolkata`), `--lst-raster`
(optional Landsat LST GeoTIFF), `--cache` (`.osmnx_cache`), `--out`, `--bucket`,
`--key`, `--no-upload`. Hours are 8–18 inclusive; the default centre/radius is the
Connaught Place pilot area.

Expect a `wrote data/graph.json.gz (N KiB) in NNN s` line plus a summary with node
and edge counts. The run needs internet access (OSM) and takes minutes; the cache
means a retry does not re-download. `--cache` defaults to `.osmnx_cache`, which is
gitignored.

### 1c. Sanity-check the output before trusting it

Shade must actually vary. If every edge comes back at the same value, the geometry
step silently no-op'd and the graph is useless for the demo.

```bash
python -c "
import gzip, json, statistics as s
g = json.load(gzip.open('data/graph.json.gz','rt'))
e = g['edges']; H = g['meta']['hours']
print('nodes', len(g['nodes']), 'edges', len(e), 'hours', H[0], '-', H[-1])
sh = [x['shade_by_hour'][H.index(13)] for x in e]
print('shade@13 min/max/mean', round(min(sh),3), round(max(sh),3), round(s.mean(sh),3), '| varies:', min(sh)!=max(sh))
for k in ('heat','pollution','tree_shade'):
    v = [x[k] for x in e]; print(f'{k:10} min/max/mean', round(min(v),3), round(max(v),3), round(s.mean(v),3))
print('pilot', g['meta']['pilot'], '| is_sample', g['meta']['is_sample'])
"
```

Run against the current sample it prints `nodes 225 edges 420 hours 8 - 18`,
`shade@13 min/max/mean 0.137 0.651 0.425 | varies: True`, `heat 0.548/0.995`,
`pollution 0.1/0.95`. A real graph should show wider spread than the sample.

### 1d. Smoke-test it through the real Lambda code, locally

Before it ever reaches AWS:

```bash
GRAPH_LOCAL_PATH=data/graph.json.gz python scripts/run_local.py
# in another shell:
curl -s localhost:8000/meta
curl -s -X POST localhost:8000/route -H 'Content-Type: application/json' \
  -d '{"start":{"lat":28.6310,"lon":77.2190},"end":{"lat":28.6350,"lon":77.2250},"hour":13,"walker":"elderly"}'
```

Now the three routes should genuinely differ — different `shade_percent`, and
usually different `minutes` — unlike the synthetic grid where `balanced` and
`coolest` frequently coincide.

### 1e. Upload it, then force the Lambda to notice

`build_graph.py` uploads automatically when `--bucket` (or `$GRAPH_BUCKET`) is set.
Uploading alone is **not** enough: warm Lambda containers cache the graph in module
scope, so an existing container keeps serving the old one. Update the function
configuration to drain the old containers:

```bash
GRAPH_BUCKET=chhaon-graph-574748894960-ap-south-1 python scripts/build_graph.py \
  --centre 28.6314 77.2197 --radius 1000 --pilot "Connaught Place, Delhi"
# or, if you already built locally:
bash scripts/upload_graph.sh chhaon-graph-574748894960-ap-south-1

aws lambda update-function-configuration --function-name chhaon-route-chhaon --region ap-south-1 \
  --environment "Variables={GRAPH_BUCKET=chhaon-graph-574748894960-ap-south-1,GRAPH_KEY=graph.json.gz,BEDROCK_MODEL_ID=global.amazon.nova-2-lite-v1:0,ALLOWED_ORIGIN=*,TABLE_NAME=chhaon-RequestLogTable-P46U8QY6HJW4}"
aws lambda wait function-updated --function-name chhaon-route-chhaon --region ap-south-1
```

This exact mechanism was measured — see §"Evidence" below.

**Done when** `GET /meta` no longer says `"(SAMPLE)"`, reports `"is_sample": false`,
`methods` no longer say "synthetic random sample", and `graph_built` is recent.

**Optional realism boost:** pass `--lst-raster lst.tif` with a cloud-free May Landsat
land-surface-temperature GeoTIFF over Delhi to replace the modelled heat term with
measured heat.

---

## Step 2 — Tighten `AllowedOrigin`

`app.py:_cors()` reads the env var on every request, so no code change is needed.

**A. Quick, no redeploy:**

```bash
aws lambda update-function-configuration --function-name chhaon-route-chhaon --region ap-south-1 \
  --environment "Variables={GRAPH_BUCKET=chhaon-graph-574748894960-ap-south-1,GRAPH_KEY=graph.json.gz,BEDROCK_MODEL_ID=global.amazon.nova-2-lite-v1:0,ALLOWED_ORIGIN=https://your-site.example,TABLE_NAME=chhaon-RequestLogTable-P46U8QY6HJW4}"
```

**B. Better — make the stack the source of truth**, so a future redeploy cannot
silently revert it to `*`. Edit `samconfig.toml`:

```toml
parameter_overrides = "AllowedOrigin=\"https://your-site.example\" BedrockModelId=\"global.amazon.nova-2-lite-v1:0\" GraphKey=\"graph.json.gz\""
```

then `python -m samcli build --use-container && python -m samcli deploy --no-confirm-changeset`.

**Verify:**

```bash
BASE=https://ie5jb9r5u2.execute-api.ap-south-1.amazonaws.com/prod
curl -s -o /dev/null -D - -X OPTIONS $BASE/route \
  -H 'Origin: https://your-site.example' -H 'Access-Control-Request-Method: POST' | grep -i access-control-allow-origin
# expect: access-control-allow-origin: https://your-site.example

curl -s -o /dev/null -D - -X OPTIONS $BASE/route \
  -H 'Origin: https://evil.example' -H 'Access-Control-Request-Method: POST' | grep -i access-control-allow-origin
# expect: no match
```

**Caveat, stated plainly:** CORS is enforced by *browsers only*. Anyone can still
`curl` the API; the header narrows who can call it from a page, it does not secure
it. The only abuse control today is the API throttle (10 req/s, burst 20). If you
need real protection, add an API Gateway usage plan/API key or a Cognito authorizer.

---

## Step 3 — Wire the frontend to the live API

`frontend/app.js` still returns hardcoded mock routes and takes **place names**,
while the API takes **coordinates** (`start`/`end` as `{lat, lon}`, `hour` as an
integer, `walker` as one of `adult|elderly|child|vendor`).

### Decide first: how do place names become coordinates?

**Option A — Amazon Location Service** (matches the architecture described in
`README.md` §"Frontend map key"; small cost).

1. Location Service → **Place index** → create `chhaon-places` (data provider *Esri* or *HERE*).
   Location Service is available in `ap-south-1` (verified: the API responds normally there).
2. Location Service → **API keys** → create `chhaon-web`, restricted to
   `geo-places:*SearchText`, `geo-places:*Suggest`, `geo-maps:GetTile`,
   `geo-maps:GetStyleDescriptor`; allowed origins = your site; short expiry.
3. Geocode each input in the browser, use the returned position as `start`/`end`.
4. Point MapLibre at the Location style descriptor instead of the current
   `demotiles.maplibre.org` style.

Pricing: a free trial covers the first three months (usage quotas apply); after that
geocoding is on the order of $0.50 per 1,000 requests, so a demo costs cents. Check
the current numbers on the Location Service pricing page before relying on them.

**Option B — no AWS dependency: a pilot place list.** Keep a small `PLACES` map of
the ~20 known pilot locations (Connaught Place, India Gate, …) → coordinates and
resolve inputs against it. Zero cost, no keys, works offline, and for a fixed pilot
area it is honest and reliable. Choose this if the demo must never fail.

### Then the actual wiring in `frontend/app.js`

- Replace the mock `fetchRoutes()` body with a real call:

  ```js
  const API_BASE = "https://ie5jb9r5u2.execute-api.ap-south-1.amazonaws.com/prod";
  async function fetchRoutes(p) {
    const startLL = /* resolve p.start */, endLL = /* resolve p.destination */;
    const r = await fetch(API_BASE + "/route", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start: startLL, end: endLL,
        hour: parseInt(p.time, 10),   // the form gives "13:00"
        walker: p.who
      })
    });
    if (!r.ok) throw Object.assign(new Error("api"), { status: r.status, body: await r.json() });
    return r.json();
  }
  ```

- The form fields already line up: `inStart`, `inDest`, `inTime` (parse `"13:00"` →
  `13`), `inWho` (its options are exactly the API's walker enum).
- `drawRoutes()` already consumes `geojson` LineStrings and colour-codes by `type`
  (grey/amber/green), so it works once fed real data.
- `cards()` should show the API's `minutes`, `distance_km`, `shade_percent` instead
  of mock numbers, and `sun_exposure_reduction_vs_fastest_percent` for the
  "X% less sun than the fastest route" line (that key is only on `balanced` and
  `coolest`).
- Replace `CONFIG.advice.en/hi` mocks with `data.advice.en` / `data.advice.hi`, and
  show a small badge when `advice_source === "template"` so a Bedrock failure is
  visible rather than silent.
- Handle the 400 outside-area case explicitly ("That point is outside the pilot
  area") instead of a generic error.
- Whatever origin serves the page (`http://localhost:3000`, etc.) must be in
  `AllowedOrigin` from Step 2. Note `file://` sends no `Origin` header — serve over
  a local HTTP server.

**Done when:** both fields resolve to coordinates, three lines render in
grey/amber/green, the cards show API numbers, and the advice toggle shows the API's
EN/HI text.

---

## Step 4 — Hindi

Two separate problems.

### 4a. The model mixes English words into Hindi

Bedrock's Hindi is otherwise fluent, but it currently produces things like
"कोई समय का **trade-off** नहीं है". Fix by adding one constraint to `_SYSTEM_PROMPT`
in `src/api/app.py`:

```
For the Hindi text, write in Devanagari using simple everyday Hindi.
Do not use English words or transliterate English terms
(no "trade-off", "route", "exposure") — say मार्ग, समय का अंतर.
```

Redeploy, then confirm no Latin letters appear inside the `hi` string of a `/route`
response.

### 4b. The template fallback needs a native speaker

`_template_advice()` in `app.py` is what users see whenever Bedrock is unavailable
or misconfigured, so it has to read correctly.

- Send the two Hindi sentences to a native speaker with the context (elderly walker,
  midday, shade, water).
- Ask specifically whether `"{walker} धावक"` reads naturally — धावक often reads as
  "runner" rather than "walker"; पैदल यात्री is usually the better word — and whether
  `"समय हो तो"` carries the intended "if you have the time".
- The string is already flagged in `app.py` with a comment; keep that flag until a
  speaker has signed off.
- After any edit, re-run `python -m pytest tests -q` (`tests/test_advice.py` asserts
  both languages are non-empty).

---

## Step 5 — Inference residency

The deployed value `global.amazon.nova-2-lite-v1:0` is a **cross-region** inference
profile: inference may run outside `ap-south-1`.

```bash
# 1. confirm an APAC-only profile works for this account
aws bedrock-runtime converse --region ap-south-1 --model-id apac.amazon.nova-lite-v1:0 \
  --messages '[{"role":"user","content":[{"text":"ok"}]}]' --inference-config '{"maxTokens":5}'

# 2. point the function at it (keep every other variable as-is)
aws lambda update-function-configuration --function-name chhaon-route-chhaon --region ap-south-1 \
  --environment "Variables={GRAPH_BUCKET=chhaon-graph-574748894960-ap-south-1,GRAPH_KEY=graph.json.gz,BEDROCK_MODEL_ID=apac.amazon.nova-lite-v1:0,ALLOWED_ORIGIN=*,TABLE_NAME=chhaon-RequestLogTable-P46U8QY6HJW4}"
aws lambda wait function-updated --function-name chhaon-route-chhaon --region ap-south-1

# 3. confirm it still works
curl -s -X POST $BASE/route -H 'Content-Type: application/json' \
  -d '{"start":{"lat":28.6310,"lon":77.2190},"end":{"lat":28.6350,"lon":77.2250},"hour":13,"walker":"elderly"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['advice_source'])"
# expect: bedrock   (if it says template, the model is not enabled — check the
#         "[chhaon] bedrock advice failed" line in CloudWatch Logs)
```

Models verified working from this account: `global.amazon.nova-2-lite-v1:0`,
`apac.amazon.nova-lite-v1:0`, `global.anthropic.claude-haiku-4-5-20251001-v1:0`.
Bare base-model ids (e.g. `amazon.nova-2-lite-v1:0`) are rejected with
`on-demand throughput isn't supported` — always use a profile id.

**Honest limit:** an `apac.*` profile keeps inference inside APAC, not necessarily
inside India — it can route to Singapore or Tokyo. Your *data* (S3 graph, DynamoDB
logs) stays in `ap-south-1`; only the advice prompt and its copy of the route
numbers leave the region. If a submission rule requires India-only processing, no
cross-region profile satisfies it: either state that plainly, or run template-only
(`BedrockModelId=""`), which is fully in-region and already tested.

---

## 6. Housekeeping

**Budget alert does not alert anyone.** The account has `My Monthly Cost Budget`
($50/mo) but its `NotificationsWithSubscribers` is null — a budget with no
notification emails nobody. A $5 budget with an actual-80% notification is a
sensible guard for this pilot:

```bash
aws budgets create-budget --account-id 574748894960 \
  --budget '{"BudgetName":"chhaon-pilot","BudgetLimit":{"Amount":"5","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}' \
  --notifications-with-subscribers '[{"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":80,"ThresholdType":"PERCENTAGE"},"Subscribers":[{"SubscriptionType":"EMAIL","Address":"you@example.com"}]}]'
```

**No version control here.** `git status` in `backend/` fails with
`fatal: not a git repository`, so there is no rollback if a build step goes wrong.
A `git init` plus one commit before Step 1 gives you that safety net.

---

## Order of work

1. **Step 1** (real graph) — everything you show depends on it.
2. **Step 3** (frontend) — makes the deployed API visible and Step 1's progress obvious.
3. **Step 2** (origin) — do it in the same deploy, once the frontend origin is known.
4. **Step 4** (Hindi) — needs a person, so start the ask early and land 4a now.
5. **Step 5** (residency) — one command, once the residency question is settled.

---

## Evidence: what was actually verified

Measured against the live stack on 2026-10-10, not assumed.

| Check | Result |
|---|---|
| `GET /health` | 200 `{"status":"ok","graph_loaded":true}` (2.5 s cold) |
| `GET /meta` | 200; sample-flagged; centre, bbox, hours 8–18, walker types |
| `POST /route` | 200; contract `hour_used, walker, routes, advice, advice_source, disclaimer, graph_meta` |
| Outside-area start (Mumbai) | 400 |
| CORS preflight | 204 with `allow-origin`, methods, headers |
| Advice via Bedrock | `advice_source:"bedrock"` on every call; 2.93 s cold, 1.0–2.0 s warm; no `bedrock advice failed` in CloudWatch |
| DynamoDB logging | rows written; `start=28.631,77.219` (3 decimals); `expires_at` = `ts` + 604800 s; no IP stored |
| Bucket privacy | all four public-access blocks `true` |
| Lambda config | python3.12, 1024 MB, 15 s, x86_64 |
| Throttle | burst 20, rate 10 req/s (stage `prod`) |
| `sam validate` | valid SAM template |
| Tests | 30 passed |

### The stale-graph trap — measured

Swapping the graph with **no code changes** was tested end to end using a marked
copy of the same graph (only `meta.pilot` changed):

| Step | `/meta` reports |
|---|---|
| New object already in S3, warm container untouched | `Connaught Place, Delhi (SAMPLE)` — **stale, still the old graph** |
| After `update-function-configuration` + `wait function-updated` | `SWAP TEST MARKER` — new graph loaded |

So uploading alone is not enough (you would believe your real graph was live when it
was not), and the config nudge does drain the old containers. The original graph was
restored afterwards and the bucket now contains only `graph.json.gz`.

### Graph file format (as actually produced)

```
{
  "meta":  { "pilot", "centre", "bbox", "radius_m", "hours", "date_used", "timezone",
             "built", "is_sample", "solar_intensity_by_hour", "walker_speeds_kmh",
             "defaults", "sources", "methods" },
  "nodes": [ { "id", "lat", "lon" }, ... ],
  "edges": [ { "u", "v", "length_m", "highway",
               "shade_by_hour": [11 floats, aligned with meta.hours],
               "tree_shade", "building_shade_by_hour": [11 floats],
               "heat", "pollution", "geometry": [[lon, lat], ...] }, ... ]
}
```

Note `shade` is **per hour**, not a single value per edge — any check you write must
index `shade_by_hour` with `meta.hours.index(hour)`. `make_sample_graph.py`
(Phase 0) and `build_graph.py` (Phase 1) emit the same shape, which is what makes
the swap code-free.

---

## Known traps worth remembering

1. **A new graph is not picked up until containers are recycled** — see Step 1e.
2. **A wrong `BEDROCK_MODEL_ID` fails silently** into `advice_source:"template"`.
   Always read the source field after changing it.
3. **Base model ids are rejected**; use inference profiles.
4. **CORS is not authentication** — it only constrains browsers.
5. **Shade, heat and pollution are estimates** from map data and simple geometric
   models. Every response says so in `disclaimer`; keep that language in the UI.
6. **`--use-container` is required** on this machine, or numpy ships as a Windows
   wheel into a Linux runtime.
