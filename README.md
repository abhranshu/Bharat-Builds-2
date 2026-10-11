# 🌳 Chhaon — Find the Coolest Way to Walk

> **Chhaon (छाँव) means "shade".** A pilot web app that finds the *coolest, safest* walking route on hot days — not just the shortest one — for the elderly, children, street vendors and other pedestrians.

[![AWS](https://img.shields.io/badge/Built%20on-AWS-FF9900?logo=amazonaws&logoColor=white)](https://aws.amazon.com/)
[![Region](https://img.shields.io/badge/Region-ap--south--1-blue)](https://aws.amazon.com/about-aws/global-infrastructure/regions/)
[![Backend](https://img.shields.io/badge/Backend-Python%203.12%20Lambda-3776AB?logo=python&logoColor=white)](backend/)
[![Frontend](https://img.shields.io/badge/Frontend-Vanilla%20JS%20%2B%20MapLibre-000000)](frontend/)

> ⚠️ **Everything here is an estimate.** Route times, shade, heat and pollution come from map data + simple geometric models. They are **not measured temperatures or measured air quality**. Every API response carries this disclaimer.

**Live API:** `https://ie5jb9r5u2.execute-api.ap-south-1.amazonaws.com/prod`
**Pilot area:** Connaught Place, Delhi — centre `28.6314, 77.2197`, radius `1000 m` · **Region:** `ap-south-1` · **One area only.**

---

## 📖 Table of Contents

- [1. The Problem & The Idea](#1-the-problem--the-idea)
- [2. What It Does / Demo](#2-what-it-does--demo)
- [3. System Design & Architecture](#3-system-design--architecture)
- [4. How It Is Formed — Data & Routing Methodology](#4-how-it-is-formed--data--routing-methodology)
- [5. How AWS Is Used](#5-how-aws-is-used)
- [6. Tech Stack](#6-tech-stack)
- [7. File Structure](#7-file-structure)
- [8. API Reference](#8-api-reference)
- [9. Frontend](#9-frontend)
- [10. Getting Started](#10-getting-started)
- [11. Configuration](#11-configuration)
- [12. Testing](#12-testing)
- [13. Privacy, Security & Costs](#13-privacy-security--costs)
- [14. Limitations & Disclaimer](#14-limitations--disclaimer)
- [15. Roadmap / Known Gaps](#15-roadmap--known-gaps)
- [16. Acknowledgements](#16-acknowledgements)

---

## 1. 🥵 The Problem & The Idea

On a 43°C day in Delhi, a 15-minute walk in the sun can be dangerous.

For the elderly. For children. For vendors who have no choice but to walk.

> Every map optimises for **speed**. None optimises for **survival**.

**Chhaon** scores every street segment for **sun, heat and pollution** at the hour you walk, then returns **3 alternatives**:

| Route | What it means |
|-------|---------------|
| ⚡ `fastest` | Shortest time (no shade penalty) |
| ⚖️ `balanced` | Small detour for meaningful shade |
| 🌳 `coolest` | Maximum shade / minimum exposure, longer walk |

Plus short bilingual advice (**English + Hindi**).

Built as a **scroll-story + tool**: an AI-generated scroll-driven film (land → deforestation → urbanisation → heat) leads into a live route-finder map.

---

## 2. ✨ What It Does / Demo

### 60-second demo script

1. `GET /health` → `{"status":"ok","graph_loaded":true}` (fast when warm)
2. `GET /meta` → *"one pilot area, Connaught Place, hours 8–18, four walker types"*
3. `POST /route` for `walker:"elderly"` at `hour:13` → *fastest* is shortest but exposed; *coolest* is longer but shadier with lower `exposure_index`; note `sun_exposure_reduction_vs_fastest_percent`.
4. Point at bilingual `advice` + `advice_source` (`bedrock` vs `template`).
5. Repeat with `walker:"child"` at `hour:16` → different trade-off.
6. Show the `disclaimer`: *"estimates, not measurements."*

### Example request

```bash
curl -X POST https://ie5jb9r5u2.execute-api.ap-south-1.amazonaws.com/prod/route \
  -H "Content-Type: application/json" \
  -d '{"start":{"lat":28.6314,"lon":77.2197},"end":{"lat":28.6273,"lon":77.2258},"hour":13,"walker":"elderly"}'
```
Example trimmed response:

```json
{
  "hour_used": 13,
  "walker": "elderly",
  "routes": [
    {"type": "fastest", "minutes": 14.2, "distance_km": 1.18,
     "shade_percent": 22.5, "exposure_index": 68.4,
     "geojson": {"type": "LineString", "coordinates": [[77.2197, 28.6314]]}},
    {"type": "coolest", "minutes": 17.8, "distance_km": 1.42,
     "shade_percent": 61.3, "exposure_index": 34.1,
     "sun_exposure_reduction_vs_fastest_percent": 42.5,
     "geojson": {"type": "LineString", "coordinates": []}}
  ],
  "advice": {"en": "...", "hi": "..."},
  "advice_source": "bedrock",
  "disclaimer": "Estimated values for a pilot area, based on map data and simple models. Not measured conditions.",
  "graph_meta": {"pilot": "Connaught Place, Delhi"}
}
```

---

## 3. 🏗️ System Design & Architecture

### 3.1 High-level diagram

```
      laptop (offline, once)                    AWS ap-south-1
+-- make_sample_graph.py (Phase 0) --+   +-- S3 (private, SSE-AES256) ------+
|   synthetic 15x15 grid             |   |   graph.json.gz                 |
|   build_graph.py (Phase 1)         |   |        ^ GetObject (cold start) |
+-- real OSMnx graph ---- upload ----+-->|        |                        |
  scripts/upload_graph.sh  s3 cp         |  Lambda py3.12 1024MB 15s       |
                                         |   numpy + networkx only         |
                                         |     |         |                 |
                                         |     |         +-> Bedrock (EN+HI)
                                         |     |         +-> DynamoDB (TTL)
                                         |   API Gateway HTTP API (prod) --+--> browser
                                         |   Location Service (map+search) <--+
                                         +-----------------------------------+
Frontend: static index.html + api.js + app.js + MapLibre GL (no build step)
Film: 6 clips x 120 webp frames, scroll-driven canvas
```

### 3.2 Request lifecycle

```
Browser (frontend/api.js)
  1. GET /meta -> pilot bbox, hours, walkers, place list
  2. resolveLocation(text) -> lat/lon (pilot places or "lat,lon")
  3. POST /route {start, end, hour, walker}
  |
API Gateway HTTP API (CORS, throttle burst 20 / rate 10 req/s)
  |
Lambda handler (src/api/app.py:handler)
  - validate -> 400 outside pilot area, 404 no route
  - snap start/end to nearest graph node (<=300 m, haversine + numpy)
  - 3x Dijkstra with different cost weights -> fastest/balanced/coolest
  - stats -> minutes, km, shade %, exposure_index, GeoJSON
  - Bedrock Converse (6 s timeout, 1 retry) -> {en, hi} or template fallback
  - DynamoDB PutItem (async, failure-tolerant, 3-decimal coords, no IP)
  |
Browser (frontend/app.js)
  - normalizeRoutes() -> cards + MapLibre layers (grey/amber/green)
  - advice toggle EN/HI + disclaimer
```

### 3.3 Data pipeline (offline -> online)

```
OpenStreetMap (walk network, buildings, trees, parks, water)
+ optional Landsat LST GeoTIFF
  |
scripts/build_graph.py [osmnx, geopandas, shapely, rasterio, pvlib, astral]
  - project to metric CRS -> lengths, geometries
  - sun position (15 May, Asia/Kolkata, 8-18) -> building shadows
  - 12 m buffer + canopy/green -> tree shade
  - 60 m buffer -> heat proxy; road class -> pollution proxy
  - writes data/graph.json.gz {meta, nodes[], edges[]}
  |
scripts/upload_graph.sh <bucket> -> s3://chhaon-graph-<acct>-ap-south-1/graph.json.gz
  |
Lambda cold start: S3 GetObject once -> networkx.Graph in memory -> warm reuses
NOTE: new graph needs container recycle (touch function config) to go live.
```


---

## 4. 🧪 How It Is Formed — Data & Routing Methodology

### 4.1 Sources

- **OpenStreetMap contributors (ODbL)** — street network, building footprints, `height`/`levels`, trees, parks, water.
- **Optional Landsat land-surface-temperature GeoTIFF** — when `--lst-raster` is passed; otherwise a built/green proxy.

### 4.2 Per-edge attributes

| Attribute | Method | Notes |
|-----------|--------|-------|
| `length_m` | OSM way length in projected CRS | metres |
| `geometry` | OSM way geometry | `[lon,lat]`, 5 decimals |
| `shade_by_hour[11]` | `1 - (1-building)(1-tree)` per hour 8–18 | core signal |
| `building_shade` | convex hull of footprint + footprint shifted by `height/tan(altitude)` along shadow azimuth | height from tag, else `levels x 3 m`, else `9 m` default |
| `tree_shade` | 12 m buffer overlap with canopy/green polygons | static across hours |
| `heat` | `0.6 x built share + 0.4 x (1-green share)` in 60 m buffer, or normalised LST if supplied | proxy |
| `pollution` | road-class base (motorway/trunk 1.0 down to footway 0.1) + near-major-road bonus | proxy |
| `solar_intensity` | sun altitude on representative hot day (15 May) | 0–1 per hour |

Full narrative in [`backend/METHODOLOGY.md`](backend/METHODOLOGY.md).

### 4.3 Routing

Each edge costs:

```
cost = time + length x (a x sun_exposure + b x heat + c x pollution)
where sun_exposure = (1 - shade_at_hour) x solar_intensity
```

Walker weights (`src/api/app.py:CONFIG`):

| Walker | a (sun) | b (heat) | c (poll) | speed |
|--------|---------|----------|----------|-------|
| adult | 0.30 | 0.15 | 0.10 | 5.0 km/h |
| vendor | 0.20 | 0.10 | 0.10 | 4.5 km/h |
| elderly | 0.80 | 0.40 | 0.25 | 3.5 km/h |
| child | 0.80 | 0.40 | 0.30 | 3.5 km/h |

Route multipliers: `fastest: 0.0`, `balanced: 0.5`, `coolest: 1.5`.

```
exposure_index = 100 x mean(0.6 x sun + 0.25 x heat + 0.15 x pollution)
shade_percent  = 100 x length-weighted mean shade
```

Snapping: haversine nearest node via numpy, `max_snap_m: 300`, `bbox_margin_m: 300`. Outside returns `400 outside pilot area`.

### 4.4 Advice (Bedrock + fallback)

- **Primary:** Amazon Bedrock Converse (`global.amazon.nova-2-lite-v1:0`) returns short EN+HI advice grounded in route stats.
- **Fallback:** deterministic EN+HI template, `advice_source:"template"`. Any Bedrock failure (timeout 6 s, 1 retry, wrong model id) falls back silently — always check `advice_source`. Hindi template needs native-speaker review.

### 4.5 Graph file format

```jsonc
{
  "meta": {"pilot": "Connaught Place, Delhi", "centre": [28.6314, 77.2197],
           "radius_m": 1000, "hours": [8, 9, "...", 18], "is_sample": false},
  "nodes": [{"id": 0, "lat": 28.63, "lon": 77.21}],
  "edges": [{"u": 0, "v": 1, "length_m": 42.5, "highway": "footway",
             "shade_by_hour": [], "tree_shade": 0.3,
             "heat": 0.6, "pollution": 0.2, "geometry": [[77.21, 28.63]]}]
}
```

`make_sample_graph.py` (synthetic grid, Phase 0) and `build_graph.py` (real OSM, Phase 1) emit the same shape so swaps are code-free.


---

## 5. ☁️ How AWS Is Used

Region **`ap-south-1` (Mumbai)** · Stack **`chhaon`**. Infra-as-code in `backend/template.yaml` (SAM / CloudFormation).

| AWS Service | Resource / Config | Role in Chhaon |
|-------------|-------------------|----------------|
| **Lambda** | `chhaon-route-chhaon` · `python3.12`, `1024 MB`, `15 s`, `x86_64` · handler `app.handler` · deps `numpy==2.2.5 + networkx==3.5` (boto3 from runtime) | Loads graph once on cold start, runs 3x Dijkstra, calls Bedrock + DynamoDB, returns JSON. Light deps = fast cold start (~2.5 s). |
| **API Gateway (HTTP API)** | stage `prod` · routes `GET /health`, `GET /meta`, `POST /route` (+ `OPTIONS`) · CORS `AllowOrigins=*` (tighten before prod) · throttle `burst 20 / rate 10 req/s` | Public HTTPS front door. CORS + throttling only — no auth (public demo API). |
| **S3** | `chhaon-graph-<acct>-ap-south-1` · key `graph.json.gz` (~38 KB sample now) · private (all 4 public blocks true) · `SSE-AES256` · lifecycle 30 d · IAM `s3:GetObject` on that key only | Stores the pre-scored graph built offline. Lambda `GetObject` once per cold start. Upload via `scripts/upload_graph.sh`. |
| **DynamoDB** | `chhaon-RequestLogTable-*` · `PAY_PER_REQUEST` · PK `request_id` · TTL `expires_at` (7 days) · SSE on · IAM `dynamodb:PutItem` only | Anonymous log: request id, ts, walker, hour, route stats, coords rounded to 3 decimals. No IP. Failure-tolerant. |
| **Bedrock** | Converse API · inference profile `global.amazon.nova-2-lite-v1:0` · IAM `bedrock:InvokeModel` on `foundation-model/*` + `inference-profile/*` | Bilingual advice. Enable model access first. Base model ids are rejected — use inference profiles. Use `apac.*` if residency must stay in-region. |
| **Location Service** | Map resource (e.g. `chhaon-map`) + API key (`geo-maps:GetTile`, `GetStyleDescriptor`, `geo-places:SearchText/Suggest`, referer-locked, short expiry) | **Map + address search only.** MapLibre loads the v2 style descriptor. **Never used for routing** — it cannot do a custom shade cost. Fallback to demotiles if Amazon style fails. |
| **SAM + CloudFormation** | `template.yaml` + `samconfig.toml` (`stack_name=chhaon`, `region=ap-south-1`, `use_container=true`) | `sam build --use-container` (Docker needed for Linux numpy wheel) then `sam deploy`. |
| **IAM** | Least-privilege inline policies on Lambda role | Single-object S3 read, PutItem-only DDB, InvokeModel-only Bedrock (both profile + foundation-model ARNs needed or silent template fallback). |
| **CloudWatch** (implicit) | Lambda logs | Check for `bedrock advice failed`, cold-start duration, `graph_loaded`. |

### Why NOT Amazon Location routing?

Location Service routing cannot take a custom cost function (`a x sun + b x heat + c x pollution` per walker/hour). So routing is done by **our own Lambda over our own OSM graph**; Location is map + search only.

### Deployed reference (verified 2026-10-10)

| Thing | Value |
|-------|-------|
| API base | `https://ie5jb9r5u2.execute-api.ap-south-1.amazonaws.com/prod` |
| Lambda | `chhaon-route-chhaon` — py3.12, 1024 MB, 15 s |
| Bucket | `chhaon-graph-574748894960-ap-south-1` / `graph.json.gz` (sample) |
| Table | `chhaon-RequestLogTable-P46U8QY6HJW4` (TTL `expires_at`) |
| Model | `global.amazon.nova-2-lite-v1:0` → `advice_source:"bedrock"` in ~1 s warm (2.9 s cold) |
| CORS | `*` — tighten before sharing |
| Tests | 30 passed · `sam validate` OK |

> **Stale-graph trap (measured):** uploading a new `graph.json.gz` alone does NOT update warm Lambdas (`/meta` still shows the old pilot). Recycle containers with `aws lambda update-function-configuration ...` + `wait function-updated`. See `backend/NEXT_STEPS.md`.


---

## 6. 🛠️ Tech Stack

**Backend:** Python 3.12 · `numpy` + `networkx` (Lambda) · `boto3` (from runtime) · offline: `osmnx`, `geopandas`, `shapely`, `pyproj`, `rasterio`, `pandas`, `pvlib`, `astral` · `pytest`, `PyYAML`
**Infra:** AWS SAM / CloudFormation · Docker (`--use-container` builds)
**Frontend:** Vanilla JS (no bundler) · MapLibre GL `4.7.1` · Amazon Location Service Style v2 · custom CSS
**Data:** OpenStreetMap (ODbL) · optional Landsat LST · `graph.json.gz` (gzip JSON)
**Media:** Google Flow (AI visuals) → `video/*.mp4` (6 cropped clips, gitignored) → `frame/*.webp` (720 scroll frames via ffmpeg, gitignored) → canvas film

---

## 7. 📁 File Structure

```
bharat_build2/
├── README.md                  <- you are here (project overview)
├── .gitignore                 <- ignores video/, frame/, *.webp, *.mp4...
├── sd4.json                   <- Amazon Location Standard-Global-Light style snapshot (v2 tiles)
├── backend/                   <- routing API (SAM app)
│   ├── README.md              <- backend deep-dive (endpoints, phases, console checklist)
│   ├── METHODOLOGY.md         <- paste-ready methodology for submission
│   ├── NEXT_STEPS.md          <- honest gap list + exact fix commands
│   ├── template.yaml          <- SAM: Lambda + HttpApi + S3 + DynamoDB + IAM + outputs
│   ├── samconfig.toml         <- stack_name=chhaon, region=ap-south-1, use_container=true
│   ├── samconfig.toml.example <- template to copy
│   ├── requirements.txt       <- local dev/test deps (numpy, networkx, boto3, pytest, yaml)
│   ├── data/graph.json.gz     <- generated graph (gitignored, sample now)
│   ├── src/api/
│   │   ├── app.py             <- THE Lambda: config, graph cache, snap, route, advice, log, handler
│   │   └── requirements.txt   <- Lambda deps: numpy==2.2.5, networkx==3.5 only
│   ├── scripts/
│   │   ├── make_sample_graph.py   <- PHASE 0: synthetic 15x15 grid (no network)
│   │   ├── build_graph.py         <- PHASE 1: real OSMnx graph (centre/radius/date/tz/LST)
│   │   ├── run_local.py           <- PHASE 2: local HTTP server wrapping handler
│   │   ├── upload_graph.sh        <- s3 cp graph to private bucket
│   │   └── requirements-offline.txt <- heavy offline deps (never in Lambda)
│   └── tests/                 <- 30 offline pytest tests
│       ├── conftest.py
│       ├── test_routing.py    <- fastest/balanced/coolest trade-offs
│       ├── test_snapping.py   <- bbox + 300 m snap + outside-area
│       ├── test_advice.py     <- bedrock vs template, EN+HI
│       ├── test_contract.py   <- response shape
│       └── test_errors.py     <- 400/404/405 handling
├── frontend/                  <- static site (no build)
│   ├── index.html             <- story sections + tool form + map + advice box
│   ├── styles.css             <- film, story, tool, map, cards styling
│   ├── api.js                 <- CHHAON client: base URL, 12 PILOT_PLACES, validation, fetch, normalize
│   ├── app.js                 <- film scroll (canvas lerp/preload) + map + form submit + render
│   └── test/
│       ├── api.test.js        <- Node unit tests for api.js
│       └── browser.e2e.mjs    <- browser smoke (?mock=1, ?debug=1 hooks)
├── video/  (gitignored)       <- source AI clips: clip1..6_cropped.mp4 (~80 MB)
└── frame/  (gitignored)       <- scroll frames: clip1_f_0001..0120.webp ... clip6 (720 files)
```

**Read first:** `backend/src/api/app.py` (logic) → `backend/template.yaml` (infra) → `frontend/api.js` (contract) → `backend/NEXT_STEPS.md` (open gaps).

---

## 8. 📡 API Reference

Base: `https://ie5jb9r5u2.execute-api.ap-south-1.amazonaws.com/prod`

| Method | Path | Body | Success | Errors |
|--------|------|------|---------|--------|
| `GET` | `/health` | — | `200 {"status":"ok","graph_loaded":true}` | `500` |
| `GET` | `/meta` | — | `200 {pilot, centre, bbox, radius_m, hours[8..18], walkers[], graph_built, is_sample, methods}` | `500` |
| `POST` | `/route` | `{start:{lat,lon}, end:{lat,lon}, hour?:8-18, walker?:elderly\|child\|vendor\|adult}` | `200 {hour_used, walker, routes[3], advice{en,hi}, advice_source, disclaimer, graph_meta}` | `400` bad input / outside area · `404` no route · `405` wrong method |
| `OPTIONS` | `/route` | CORS preflight | `204` | — |

Route object: `{type, minutes, distance_km, shade_percent, exposure_index, sun_exposure_reduction_vs_fastest_percent?, same_as?, geojson: LineString [lon,lat]}`.

Frontend mirrors backend validation: `parseLatLon`, `parseHour`, `validateWalker`, `insideBbox`, 12 curated `PILOT_PLACES` (each within ~60 m of a node).

---

## 9. 🎨 Frontend

- **No build step.** Classic `<script>` tags (`api.js` then `app.js`); also `require`-able in Node for tests. Serve with any static server.
- **Film:** scroll-driven canvas. `CONFIG.film`: `basePath:../frame/`, `clips:[clip1..6]`, `120` frames each, `lerp:0.12`, `maxDPR:1.5`, `preloadFirst:20` + idle rest. Captions (land → deforestation → urbanisation → emissions → waste/cooling → hotter). Respects `prefers-reduced-motion`, reverses on scroll-up, cover-fit.
- **Tool:** form (start, destination + datalist, time, walker) → `buildRouteRequest` → `fetchRoutes` (20 s abort) → `normalizeRoutes` → cards + MapLibre layers (fastest grey `#9aa0a6`, balanced amber `#e0a100`, coolest green `#3fa46a`) + EN/HI toggle + disclaimer. `?mock=1` renders dev mock only; never as failure fallback. `?debug=1` exposes `window.__chhaon`.
- **Map:** MapLibre `4.7.1` loads Amazon Location v2 descriptor from `ap-south-1` with a restricted public key (scoped to `geo-maps`/`geo-places`, referer-locked — the documented AWS pattern). Falls back to `demotiles.maplibre.org` so the map is never blank. Centre `[77.2090, 28.6139]`, zoom `13`.


---

## 10. 🚀 Getting Started

### Prerequisites

- Python `3.12`, Node `18+` (frontend tests), AWS CLI v2 + credentials, SAM CLI (or `python -m samcli`), Docker (for `sam build --use-container`), `conda` (for offline geo deps).

### A. Run backend locally (no AWS)

```bash
cd backend
pip install -r requirements.txt
python scripts/make_sample_graph.py --out data/graph.json.gz
python scripts/run_local.py --port 8000 --graph data/graph.json.gz
```

Then open `http://127.0.0.1:8000/health`, `/meta`, `/route` (template advice, no Bedrock).

### B. Run frontend locally

```bash
npx serve frontend -l 3000
```

> `frontend/api.js:API_BASE_URL` points at prod. For local dev point it to `http://127.0.0.1:8000`.

### C. Build the REAL graph (highest value)

```bash
conda create -n chhaon-graph python=3.12 -c conda-forge \
  osmnx geopandas shapely pyproj rasterio pandas pvlib astral -y
conda activate chhaon-graph
pip install numpy networkx boto3
cd backend
python scripts/build_graph.py --centre 28.6314 77.2197 --radius 1000 \
  --pilot "Connaught Place, Delhi" --no-upload --out data/graph.json.gz
python scripts/run_local.py
```

### D. Deploy to AWS

```bash
cd backend
sam build --use-container
sam deploy --guided
BUCKET=$(aws cloudformation describe-stacks --stack-name chhaon --region ap-south-1 \
  --query "Stacks[0].Outputs[?OutputKey=='GraphBucketName'].OutputValue" --output text)
bash scripts/upload_graph.sh "$BUCKET" graph.json.gz data/graph.json.gz
aws lambda update-function-configuration --function-name chhaon-route-chhaon --region ap-south-1 \
  --environment "Variables={GRAPH_BUCKET=$BUCKET,GRAPH_KEY=graph.json.gz,BEDROCK_MODEL_ID=global.amazon.nova-2-lite-v1:0,ALLOWED_ORIGIN=https://your-site,TABLE_NAME=your-table}"
aws lambda wait function-updated --function-name chhaon-route-chhaon --region ap-south-1
```

Enable Bedrock model access in the console before setting `BedrockModelId`.

---

## 11. ⚙️ Configuration

| Var | Default | Meaning |
|-----|---------|---------|
| `GRAPH_BUCKET` | — | Private S3 bucket (from GraphBucket ref) |
| `GRAPH_KEY` | `graph.json.gz` | Object key (`GraphKey` param) |
| `BEDROCK_MODEL_ID` | `""` (template only) | e.g. `global.amazon.nova-2-lite-v1:0` |
| `ALLOWED_ORIGIN` | `*` | CORS origin — set to your domain in prod |
| `TABLE_NAME` | — | DynamoDB log table |
| `GRAPH_LOCAL_PATH` | local only | Used by `run_local.py` |

Tunables in `app.py:CONFIG`: `walker_weights`, `walker_speeds_kmh`, `route_multiplier`, `exposure_weights`, `bbox_margin_m`, `max_snap_m`, `bedrock_timeout_s`, `default_hour:13`.

---

## 12. ✅ Testing

```bash
cd backend
pytest -q
sam validate
cd ../frontend
node test/api.test.js
node test/browser.e2e.mjs
```


Live checks: `GET /health`, `GET /meta`, `POST /route` (CP centre to Agrasen ki Baoli), Mumbai start gives `400`, CORS preflight gives `204`, no `bedrock advice failed` in CloudWatch, DynamoDB row has `expires_at = ts + 604800`.

---

## 13. 🔒 Privacy, Security & Costs

- **Privacy:** DynamoDB stores only `request_id`, `ts`, `walker`, `hour`, route stats, coords rounded to 3 decimals (~100 m). No IP, no exact location. TTL 7 days auto-expires.
- **Security:** S3 private + SSE + all public blocks on; IAM least-privilege; frontend holds no secrets (public API + restricted map key with referer lock + short expiry); CORS is browser-only, not auth — tighten `AllowedOrigin` and rotate the `chhaon-web` key for prod.
- **Costs (pilot-scale):** Lambda 1024 MB x <3 s, one small S3 object, tiny on-demand DDB writes, throttled API Gateway, per-request Bedrock Nova Lite, per-load Location tiles. Set an **AWS Budget alert with notifications** (currently missing — see NEXT_STEPS).

---

## 14. ⚠️ Limitations & Disclaimer

- One pilot area only; outside returns `400 outside pilot area`.
- Shade is geometric (hull shadows + canopy buffers), not measured; ignores foliage season, street furniture.
- `heat`/`pollution` are proxies from land cover + road class.
- Fixed walker speeds; no terrain / crossing delays.
- Missing OSM heights fall back to 9 m (share in `graph_meta`).
- Hindi template must be reviewed by a native speaker.
- `global.` profile routes cross-region; use `apac.*` if residency must stay in-region.
- Demo uses `*` CORS and a demo map key — restrict both for production.

---

## 15. 🗺️ Roadmap / Known Gaps

From [`backend/NEXT_STEPS.md`](backend/NEXT_STEPS.md) (2026-10-10):

1. **Real OSM graph** — only unexercised deliverable (sample grid is live now).
2. **Tighten `AllowedOrigin`** from `*` — one deploy once frontend origin is known.
3. **Frontend wiring** — confirm geocoding/UX against the real API.
4. **Hindi quality** — fix mixed English + native review.
5. **Inference residency** — decide `global.` vs `apac.` profile.
6. **Budget alerts, git safety, `--use-container` discipline.**

---

## 16. 🙏 Acknowledgements

- **OpenStreetMap contributors (ODbL)** — streets, buildings, trees, parks.
- **AWS** — Lambda, API Gateway, S3, DynamoDB, Bedrock (Nova 2 Lite), Location Service, SAM/CloudFormation/IAM.
- **MapLibre GL**, **OSMnx / GeoPandas / Shapely / pvlib / astral** communities.
- **Google Flow** — AI film visuals (`video/` to `frame/` via ffmpeg).
- Built for the elderly, children and vendors who walk because they must — *walk cooler, arrive safer*.

---

### 📚 Further reading

- [`backend/README.md`](backend/README.md) — backend deep-dive + Location console checklist
- [`backend/METHODOLOGY.md`](backend/METHODOLOGY.md) — paste-ready methodology for submission
- [`backend/NEXT_STEPS.md`](backend/NEXT_STEPS.md) — exact commands to close each gap + measured evidence
- `backend/template.yaml` — the SAM source of truth for all AWS resources

