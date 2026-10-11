# Chhaon — backend

Walking-route service that finds the **coolest, safest route** on hot days for
elderly people, children, street vendors and other adults.

> **Everything here is an estimate.** Route times, shade, heat and pollution
> come from map data and simple geometric models. They are **not** measured
> temperatures or measured air quality. The API says so on every response.

Pilot area: **Connaught Place, Delhi** — centre `28.6314, 77.2197`, radius
1000 m. Region: **ap-south-1**. One area only.

---

## Architecture

```
        laptop (offline)                         AWS
  ┌───────────────────────┐         ┌─────────────────────────────────────┐
  │ make_sample_graph.py  │         │  S3 (private)                       │
  │   (Phase 0, synthetic)│  upload │   graph.json.gz                     │
  │ build_graph.py        │ ──────► │        ▲                            │
  │   (Phase 1, osmnx)    │         │        │ GetObject (once, cold start)│
  └───────────────────────┘         │  Lambda (py3.12, 1024MB, 15s)       │
                                    │   networkx + numpy                  │
                                    │        │        │                   │
                                    │        │        └─► Bedrock Converse  │
                                    │        │            (advice, EN+HI)   │
                                    │        │        └─► DynamoDB (log)    │
                                    │   API Gateway HTTP API  ──► browser   │
                                    └─────────────────────────────────────┘
```

- The graph is built **offline** and uploaded once; the Lambda only reads it.
- Routing is done by **our own Lambda over our own graph** — Amazon Location
  Service routing cannot take a custom cost function, so it is **not** used for
  routing. The frontend uses Location Service only for the map and address search.
- Lambda dependencies are kept light: **numpy + networkx** only.

---

## File layout

```
backend/
├── template.yaml                  SAM: Lambda + HTTP API + S3 + DynamoDB + IAM
├── samconfig.toml.example         copy to samconfig.toml
├── requirements.txt               local dev/test deps
├── data/graph.json.gz             generated graph (gitignored)
├── scripts/
│   ├── make_sample_graph.py        PHASE 0  synthetic 15x15 grid
│   ├── build_graph.py              PHASE 1  real osmnx graph
│   ├── run_local.py                PHASE 2  local HTTP server over the handler
│   ├── upload_graph.sh             upload graph.json.gz to S3
│   └── requirements-offline.txt    heavy offline deps
├── tests/                         30 offline pytest tests
├── NEXT_STEPS.md                  remaining work: real graph, origin, frontend, Hindi
└── src/api/
    ├── app.py                      PHASE 2/3/4  the Lambda handler
    └── requirements.txt            numpy + networkx
```

---

## Data sources

- **OpenStreetMap** contributors (ODbL) — street network, building footprints,
  building heights/levels, trees, parks and water.
- **Optional Landsat land-surface-temperature GeoTIFF** — used only when
  `--lst-raster` is passed; otherwise a built/green proxy is used.

---

## How each attribute is estimated

| Attribute | Method | Notes |
|---|---|---|
| `length_m` | OSM way length in a projected CRS | metres |
| `geometry` | OSM way geometry | `[lon, lat]`, 5 decimals |
| `shade_by_hour` | `1 − (1−building)(1−tree)` per hour 8–18 | see below |
| building shade | convex hull of footprint + footprint shifted by `height / tan(altitude)` along the shadow direction | height from `height` tag → `building:levels × 3 m` → default **9 m** |
| tree shade | edge buffered 12 m; overlap with tree points (4 m) + park/wood/garden polygons | constant across hours |
| `heat` | `0.6 × built_fraction + 0.4 × (1 − green_fraction)` in a 60 m buffer | or LST zonal mean when `--lst-raster` is given |
| `pollution` | road-class proxy + 0.1 bonus within 50 m of a primary-or-higher road | a **proxy**, not a measurement |
| `solar_intensity_by_hour` | `sin(solar altitude)` (pvlib, or NOAA fallback) | stored once in metadata |

Representative hot day (default): **15 May**, timezone of the pilot area.

### Routing cost

```
cost_edge = time_s + length_m × (a × sun_exposure + b × heat + c × pollution)
sun_exposure = (1 − shade_at_hour) × solar_intensity_at_hour
```

Equivalent seconds per metre `(a, b, c)`:

| walker | a (sun) | b (heat) | c (pollution) | speed km/h |
|---|---|---|---|---|
| adult   | 0.30 | 0.15 | 0.10 | 5.0 |
| vendor  | 0.20 | 0.10 | 0.10 | 4.5 |
| elderly | 0.80 | 0.40 | 0.25 | 3.5 |
| child   | 0.80 | 0.40 | 0.30 | 3.5 |

- **fastest** uses `(0, 0, 0)`, **balanced** uses ½ the walker weights,
  **coolest** uses 1.5×. If two routes coincide, all three are still returned
  and the duplicate is marked `same_as`.

---

## API

**POST /route**

```json
{"start":{"lat":28.6310,"lon":77.2190},"end":{"lat":28.6350,"lon":77.2250},
 "hour":13,"walker":"elderly","lang":"en"}
```

Response 200 (abridged):

```json
{"hour_used":13,"walker":"elderly",
 "routes":[{"type":"fastest","minutes":15.0,"distance_km":1.1,
            "shade_percent":18.0,"exposure_index":70.0,
            "geojson":{"type":"LineString","coordinates":[[77.21,28.63],["..."]]}},
           {"type":"balanced","sun_exposure_reduction_vs_fastest_percent":20.0,"...":"..."},
           {"type":"coolest","sun_exposure_reduction_vs_fastest_percent":40.0,"...":"..."}],
 "advice":{"en":"...","hi":"..."},
 "advice_source":"bedrock",
 "disclaimer":"Estimated values for a pilot area, based on map data and simple models. Not measured conditions.",
 "graph_meta":{"pilot":"...","built":"...","methods":{"shade":"...","heat":"..."}}}
```

Errors are `{"error":"message"}` with the right 4xx/5xx status. CORS headers are
always returned.

**GET /health** → `{"status":"ok","graph_loaded":true}`
**GET /meta** → pilot name, centre, bbox, hours, walker types, disclaimer.

---

## Phase 0 — sample graph (do this first)

```bash
cd backend
python scripts/make_sample_graph.py            # writes data/graph.json.gz
```

**Test it:** run the local server and curl it (see Phase 2), or `pytest`.

---

## Phase 2 — run and test the Lambda locally

```bash
pip install -r requirements.txt
python scripts/make_sample_graph.py            # if not already generated

# (a) unit tests — 30 offline tests, no AWS
python -m pytest tests -q

# (b) local HTTP server over the exact Lambda handler
python scripts/run_local.py                    # http://127.0.0.1:8000
```

In another shell:

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/meta
curl -s -X POST http://127.0.0.1:8000/route -H 'Content-Type: application/json' \
  -d '{"start":{"lat":28.6310,"lon":77.2190},"end":{"lat":28.6350,"lon":77.2250},"hour":13,"walker":"elderly"}'
```

Alternatively test it as a real Lambda:

```bash
sam build --use-container
sam local start-api        # serves /health, /meta, /route on :3000
```

---

## Phase 1 — build the real graph

```bash
pip install -r scripts/requirements-offline.txt
python scripts/build_graph.py --out data/graph.json.gz
# optional Landsat heat:
python scripts/build_graph.py --lst-raster lst.tif --no-upload
```

It prints a summary (node count, edge count, mean shade by hour, methods used)
and the exact upload command. Then swap the graph in S3 **with no code changes**:

```bash
scripts/upload_graph.sh <GraphBucketName> graph.json.gz data/graph.json.gz
```

The next cold start loads the new file.

---

## Deploy to AWS

> **Before you deploy:** create an **AWS Budgets** alert (Billing → Budgets) so
> a runaway bill cannot surprise you. Set a low threshold, e.g. $5.

```bash
# 0. once: SAM CLI + AWS creds for ap-south-1
aws --version && sam --version

# 1. build (container build gets the right numpy wheel for Lambda)
sam build --use-container

# 2. deploy interactively
sam deploy --guided
#   stack name        : chhaon
#   region            : ap-south-1
#   AllowedOrigin     : *            (tighten to your site domain later)
#   BedrockModelId    : ""           (blank = template advice; see Phase 3)
#   confirm changeset : Y
#   allow IAM role    : Y

# 3. upload the graph to the private bucket from the Outputs
scripts/upload_graph.sh <GraphBucketName> graph.json.gz data/graph.json.gz

# 4. curl the live API (base URL is the ApiUrl output)
BASE=https://xxxxxxxxxx.execute-api.ap-south-1.amazonaws.com/prod
curl -s $BASE/health
curl -s $BASE/meta
curl -s -X POST $BASE/route -H 'Content-Type: application/json' \
  -d '{"start":{"lat":28.6310,"lon":77.2190},"end":{"lat":28.6350,"lon":77.2250},"hour":13,"walker":"elderly"}'
```

No secrets live in code; everything is parameters/roles.

---

## Phase 3 — Bedrock advice

1. In the **Bedrock console → Model access → Manage model access**, request
   access to a text model available in **ap-south-1**. (Availability differs by
   region — check, do not assume.)
2. Most current models **reject on-demand invocation on the bare base-model id**
   (`ValidationException: ... on-demand throughput isn't supported`). List the
   model ids, then the **inference profiles** that wrap them:

   ```bash
   aws bedrock list-foundation-models --region ap-south-1 \
     --by-output-modality TEXT --query "modelSummaries[].modelId" --output text
   aws bedrock list-inference-profiles --region ap-south-1 \
     --query "inferenceProfileSummaries[].inferenceProfileId" --output text
   ```

   Use an **inference profile id**, e.g. `global.amazon.nova-2-lite-v1:0` or
   `apac.amazon.nova-lite-v1:0`. (`global.` profiles route across regions, which
   is why the function role allows foundation-model ARNs in any region — the
   single-region policy shipped in the first template silently fell back to the
   template advice for every profile id.)

   Quick check before deploying:

   ```bash
   aws bedrock-runtime converse --region ap-south-1 \
     --model-id global.amazon.nova-2-lite-v1:0 \
     --messages '[{"role":"user","content":[{"text":"ok"}]}]' \
     --inference-config '{"maxTokens":5}'
   ```
3. Set it on the deployed function:

   ```bash
   aws lambda update-function-configuration \
     --function-name chhaon-route-chhaon \
     --environment "Variables={GRAPH_BUCKET=<bucket>,GRAPH_KEY=graph.json.gz,BEDROCK_MODEL_ID=<inference-profile-id>,ALLOWED_ORIGIN=<origin>,TABLE_NAME=<table>}"
   ```

   or re-deploy with `parameter_overrides=BedrockModelId="<inference-profile-id>"`.
   If the id is wrong you get `advice_source:"template"` — a silent fallback by
   design, so always confirm the source in the response after changing this.

   Value used in the reference deployment: `global.amazon.nova-2-lite-v1:0`,
   verified live returning `advice_source:"bedrock"` in ~1 s warm (2.9 s cold)
   with both English and Hindi text. Swap to an `apac.*` profile if inference
   must stay inside the region.

On any Bedrock failure (timeout 6 s, one retry) the Lambda returns a
deterministic template in English and Hindi and sets `advice_source:"template"`.
**The Hindi template is marked in `app.py` and must be reviewed by a native
speaker before the submission.**

---

## Phase 4 — logging

`RequestLogTable` stores an anonymous record per request with a **7-day TTL**:
request id, timestamp, walker, hour used and the three routes' stats. Coordinates
are rounded to **3 decimals**; IP addresses and exact locations are never stored.
Logging is failure-tolerant — if it breaks, the response is unaffected.

---

## Limitations

- One pilot area only; points outside it return `400 outside pilot area`.
- Shade is a geometric model (building convex-hull shadows + canopy buffers),
  not measured; real shading depends on foliage season and street furniture.
- `heat` and `pollution` are **proxies** from land cover and road class.
- Walking speeds are fixed per walker type; no terrain or crossing delays.
- Building heights are frequently missing in OSM and fall back to 9 m; the
  share of defaults is recorded in `graph_meta`.
- Travel time and statistics are **estimates**, never measured values.

---

## 60-second demo script

1. `GET /health` → `{"status":"ok","graph_loaded":true}` (fast, warm).
2. `GET /meta` → "one pilot area, Connaught Place, hours 8–18, four walker types".
3. `POST /route` for an **elderly** walker at **13:00** → read the three routes:
   *fastest* is shortest but exposed; *coolest* is a bit longer but far shadier
   and a lower exposure index; note the `sun_exposure_reduction_vs_fastest`.
4. Point at the bilingual `advice` and `advice_source`.
5. Repeat with `walker:"child"` at `hour:16` → different trade-off.
6. Show the `disclaimer`: "these are estimates, not measurements."

---

## Frontend map key (Amazon Location Service)

Console checklist for the map the browser uses:

1. Location Service → **Map** → create a map resource (e.g. `chhaon-map`,
   style *HERE Explore* or *Vector*). Copy the map resource name.
2. Location Service → **API keys** → create a key, then restrict it:
   - **Allowed actions**: `geo-places:*SearchText`, `geo-places:*Suggest`,
     `geo-maps:GetTile`, `geo-maps:GetStyleDescriptor` (map + search only).
   - **Referers / allowed origins**: your site domain only
     (e.g. `https://chhaon.example.com/*`). Never leave it open in production.
   - **Expires**: set a short expiry for the demo.
3. In the browser, initialise MapLibre with
   `https://maps.geo.<region>.amazonaws.com/maps/v0/maps/<map-name>/style-descriptor?key=<api-key>`
   and use the places API for address search.
4. Remember: **Location Service is for the map + address search only.** Routing
   goes through this API.
