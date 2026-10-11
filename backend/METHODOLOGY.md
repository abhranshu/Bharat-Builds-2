# Methodology note — Chhaon (paste into the submission)

**What it does.** Chhaon suggests a walking route that keeps you in the shade and
away from heat and pollution on hot days, instead of only the shortest one. For a
pilot area around Connaught Place, Delhi (a 1 km radius), it returns three
options: **fastest**, **balanced** and **coolest**, and a short recommendation in
English and Hindi.

**How the streets are scored.** We download the walking network, building
footprints, trees, parks and water from OpenStreetMap. For every street segment
we estimate four things:

- **Shade (per hour, 8:00–18:00).** Building shadows are approximated by taking
  each building's outline and the same outline moved along the direction of its
  shadow by `height / tan(sun altitude)`, then the convex hull of the two. A
  building's height comes from its `height` tag, or `levels × 3 m`, or a default
  of 9 m. Tree and park shade comes from buffering the street by 12 m and
  measuring overlap with tree canopies and green polygons. The two are combined:
  `shade = 1 − (1 − building_shade) × (1 − tree_shade)`.
- **Heat.** A proxy, `0.6 × built share + 0.4 × (1 − green share)` in a 60 m
  buffer. If a Landsat land-surface-temperature image is supplied, we use its
  normalised value instead and record which method was used.
- **Pollution.** A proxy based on road class (motorway/trunk 1.0 down to
  footway 0.1), plus a small bonus for being close to a major road.
- **Sun intensity**, from the sun's altitude on a representative hot day (15 May).

**How a route is chosen.** Each segment costs more than just its walking time
when it is sunny, hot or polluted:

```
cost = time + length × (a × sun_exposure + b × heat + c × pollution)
```

The weights `(a, b, c)` are larger for walkers who are more vulnerable. For an
elderly walker or a child, sun exposure counts roughly three times more than for
an adult, so the "coolest" route may detour to stay shaded.

**What we are careful about.** Every number the user sees is an **estimate**
built from map data and simple models — not a measured temperature or measured
air quality. The API labels this in every response. Where data is missing (very
often building heights) we use documented defaults and record it in the graph
metadata. We do not claim accuracy we have not measured.

**Known limits.** One pilot area only; shade ignores foliage season and street
furniture; heat and pollution are proxies; walking speed is fixed per walker type.

**Where the data comes from.** OpenStreetMap contributors (ODbL), and optionally
a Landsat land-surface-temperature image.

**What is honest to demo.** The three routes, the shade and exposure trade-off,
the bilingual advice, and the explicit "these are estimates" disclaimer.
