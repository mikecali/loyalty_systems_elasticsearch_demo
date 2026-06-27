# Jollibee BeeLoyalty — Technical Reference

A loyalty and analytics platform built on **Elasticsearch** as the primary intelligence layer. Elasticsearch handles search ranking, geospatial queries, ML inference, and aggregations. Flask is deliberately thin — it resolves parameters, calls ES, and formats responses.

---

## Table of contents

1. [Architecture overview](#architecture-overview)
2. [Elasticsearch indices](#elasticsearch-indices)
3. [Feature 1 — Hybrid search](#feature-1--hybrid-search-bm25--elser-via-rrf)
4. [Feature 2 — History-based recommendations](#feature-2--history-based-recommendations)
5. [Feature 3 — Weather-based recommendations](#feature-3--weather-based-recommendations)
6. [Feature 4 — Upsize suggestions](#feature-4--upsize-suggestions)
7. [Customer data updates](#customer-data-updates)
8. [Claude via Elasticsearch inference](#claude-via-elasticsearch-inference)
9. [API reference](#api-reference)
10. [Setup and configuration](#setup-and-configuration)
11. [Project structure](#project-structure)
12. [MVP limitations](#mvp-limitations)

---

## Architecture overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser  (templates.py — single-page dashboard)                │
└────────────────────────┬────────────────────────────────────────┘
                         │ HTTPS
┌────────────────────────▼────────────────────────────────────────┐
│  Flask  (app.py)                                                │
│  • Route parsing, parameter validation                          │
│  • Recency-weighted preference signal (_build_preference_signal)│
│  • Open-Meteo weather fetch (external, free, no key)           │
│  • Response formatting                                          │
└────────────────────────┬────────────────────────────────────────┘
                         │ REST / NDJSON bulk
┌────────────────────────▼────────────────────────────────────────┐
│  Elasticsearch (Elastic Cloud)                                  │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ ELSER v2     │  │ RRF fusion   │  │ Claude Haiku 4.5     │  │
│  │ sparse model │  │ BM25 + ELSER │  │ via /_inference/     │  │
│  │ (ingest pipe)│  │ (retriever)  │  │ completion endpoint  │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
│                                                                 │
│  Indices: menu · customers · transactions · stores · inventory  │
└─────────────────────────────────────────────────────────────────┘
```

**Design principle:** ES is not just a database. It runs the ML model (ELSER), fuses ranking signals (RRF), filters by geography (`geo_distance`), aggregates behavioural data (`terms` agg), and calls the LLM (`_inference`). Python does only what ES cannot — recency weighting, weather HTTP calls, and JSON formatting.

---

## Data Flow

<img width="2358" height="1524" alt="image" src="https://github.com/user-attachments/assets/5644002b-957d-40be-af4a-bdd9aea56a1c" />


## Elasticsearch indices

| Index | Primary key | Key fields | Notable mapping |
|---|---|---|---|
| `jollibee-menu` | `item_id` | `name`, `category`, `price`, `points_value`, `is_bestseller` | `ml.tokens` (`rank_features`) — populated by ELSER ingest pipeline; `searchable_text` feeds the pipeline |
| `jollibee-customers` | `customer_id` | `loyalty_profile.{tier, total_points, annual_spending}`, `personal_info` | Full doc replaced on every transaction (no partial update) |
| `jollibee-transactions` | `transaction_id` | `customer_id`, `items[]`, `order_total`, `timestamp`, `store_id` | `items` is a `nested` type; `timestamp` sorted desc for history queries |
| `jollibee-stores` | `store_id` | `store_name`, `location`, `address` | `location` mapped as `geo_point` — enables `geo_distance` filter and sort-by-distance |
| `jollibee-inventory` | auto | `store_id`, `item_name`, `current_stock`, `reorder_point`, `status` | Updated in same bulk call as the transaction |

### ELSER ingest pipeline

Menu items are enriched at index time via an ingest pipeline:

```json
PUT _ingest/pipeline/jollibee-elser-pipeline
{
  "processors": [{
    "inference": {
      "model_id": ".elser_model_2_linux-x86_64",
      "input_output": [{
        "input_field":  "searchable_text",
        "output_field": "ml.tokens"
      }]
    }
  }]
}
```

The `searchable_text` field concatenates name, category, and description at index time. The pipeline produces a sparse vector of token weights stored in `ml.tokens` (`rank_features` type). At query time, `text_expansion` scores against this field — no Python ML code anywhere.

---

## Feature 1 — Hybrid search (BM25 + ELSER via RRF)

**Endpoint:** `POST /api/menu/search` or `GET /api/menu/search?q=...`

### How it works

Two retrieval legs run in parallel inside a single ES `_search` call. Reciprocal Rank Fusion merges their ranked lists server-side. Python sends the query and formats the response — it does not touch scores.

```
User query: "budget meal na mura"
                    │
          ┌─────────▼──────────┐
          │  ES _search (RRF)  │
          │                    │
          │  Leg 1 — BM25      │   multi_match across name^3,
          │                    │   searchable_text^2, category^1.5,
          │                    │   description. Fuzziness: AUTO.
          │                    │   Bestseller boost: 2.0×
          │                    │
          │  Leg 2 — ELSER     │   text_expansion on ml.tokens
          │                    │   (sparse semantic vector)
          │                    │
          │  RRF k=60          │   score = Σ 1/(k + rank_i)
          │  window=100        │   per retriever. No Python math.
          └─────────┬──────────┘
                    │ top-N ranked results
```

### ES query body

```json
{
  "retriever": {
    "rrf": {
      "retrievers": [
        { "standard": { "query": {
            "bool": {
              "must": [{ "multi_match": {
                "query": "<user_text>",
                "fields": ["name^3","searchable_text^2","category^1.5","description"],
                "type": "best_fields", "fuzziness": "AUTO"
              }}],
              "should": [{ "term": { "is_bestseller": { "value": true, "boost": 2.0 }}}],
              "filter": [{ "geo_distance": {
                "distance": "10km",
                "location": { "lat": 14.6565, "lon": 121.0322 }
              }}]
            }
        }}},
        { "standard": { "query": {
            "bool": {
              "must": [{ "text_expansion": {
                "ml.tokens": { "model_id": ".elser_model_2_linux-x86_64", "model_text": "<user_text>" }
              }}],
              "filter": [{ "geo_distance": { "distance": "10km", "location": {...} }}]
            }
        }}}
      ],
      "rank_constant": 60,
      "rank_window_size": 100
    }
  },
  "size": 10
}
```

The `geo_distance` filter is added to **both** legs simultaneously when `lat`/`lon` are provided — results are bounded to the store area before ranking begins. If RRF is unavailable (ES < 8.8), the client falls back to pure ELSER automatically.

---

## Feature 2 — History-based recommendations

**Endpoint:** `GET /api/customers/<id>/recommendations?lat=&lon=`

### Signal pipeline

```
ES: GET jollibee-transactions
    query: { term: { customer_id: id } }
    sort:  [ { timestamp: desc } ]
    size:  30
          │
          ▼ Python: _build_preference_signal()
    ┌─────────────────────────────────────────┐
    │  For each order, age → weight:          │
    │    ≤ 7 days  → 3×                       │
    │    ≤ 30 days → 2×                       │
    │    older     → 1×                       │
    │                                         │
    │  Accumulate: cat_scores[cat] += weight  │
    │              item_scores[name] += weight│
    │                                         │
    │  Trend detection: last 3 orders →       │
    │    if dominant cat ≠ all-time top,      │
    │    set trending_toward = recent cat     │
    │                                         │
    │  Build query string:                    │
    │    trending_cat + top_cats[:2]          │
    │    + first 3 words of top_items[:2]     │
    └──────────────┬──────────────────────────┘
                   │ e.g. "Burgers Chickenjoy Double Cheesy"
                   ▼
    ES: hybrid_search(jollibee-menu, query, geo_filter)
    → RRF-ranked menu items
                   │
                   ▼
    ES: /_inference/completion/claude-haiku
    → one-sentence personalised insight
```

**Why categories over item names:** Feeding full item names into the query string (`"6 Pc Chickenjoy Bucket with Jolly Spaghetti Family Pan"`) causes ELSER to over-index on frequent tokens in long names. Category-level terms (`"Burgers"`, `"Chickenjoy"`) produce broader, more accurate semantic matches for *related* items rather than exact repeats.

**Why recency weighting in Python:** ES `terms` aggregation counts equally across all time. There is no native time-decay in a bucket aggregation. The 3×/2×/1× weighting is the one piece of logic that lives in Python rather than ES.

---

## Feature 3 — Weather-based recommendations

**Endpoint:** `GET /api/customers/<id>/recommendations/weather?lat=&lon=`

### Data flow

```
Browser sends store lat/lon (e.g. SM North EDSA: 14.6565, 121.0322)
          │
          ▼
Flask: GET https://api.open-meteo.com/v1/forecast?latitude=...&longitude=...
       Returns: { temperature: 32, weathercode: 0 }   (free, no API key)
          │
          ▼ weathercode → condition label
          │   0 → "sunny"   45-67 → "rainy"   71-77 → "cold"   80+ → "stormy"
          │
          ▼
ES: POST /_inference/completion/.anthropic-claude-4.5-haiku-completion
    input: "Temperature: 32°C, Condition: sunny.
            Output ONLY JSON: {query: '...', reason: '...'}"
    → { "query": "cold drinks iced refreshing mango float",
        "reason": "Perfect refreshments for a hot sunny day" }
          │
          ▼
ES: hybrid_search(jollibee-menu, "cold drinks iced refreshing mango float",
                  geo_filter={lat, lon, distance_km=10})
    → RRF-ranked results geo-bounded to store area
          │
          ▼
ES: POST /jollibee-stores/_search
    query: { geo_distance: { distance: "5km", location: {lat, lon} } }
    sort:  [ { _geo_distance: { location: ..., order: "asc", unit: "km" } } ]
    → nearest store + km distance in sort[0]
```

**Open-Meteo fallback:** If the server cannot reach `api.open-meteo.com` (outbound network restriction), weather defaults to `30°C, sunny` (typical Metro Manila). The response includes a `source: "default"` flag so the UI can show an indicator.

**JSON parsing hardening:** Claude Haiku occasionally wraps JSON in markdown fences despite instruction. The service strips lines beginning with ` ``` ` before `json.loads`. If parsing still fails, `_weather_to_default_query()` provides a rule-based fallback query based on temperature and condition.

---

## Feature 4 — Upsize suggestions

**Endpoint:** `POST /api/customers/<id>/upsize`  
**Body:** `{ "cart": [...], "lat": float, "lon": float, "store_id": "..." }`

### Dual-signal approach

```
Cart items + store lat/lon
          │
          ├──► Signal 1: Order history (ES)
          │    GET jollibee-transactions (last 30, desc)
          │    → avg_order_value (spend-gap message)
          │    → _build_preference_signal() → top_category
          │      e.g. trending_toward = "Burgers"
          │
          ├──► Signal 2: Live weather (Open-Meteo → Python)
          │    → condition + temp → _weather_to_default_query()
          │      e.g. "cold drinks iced refreshing" (32°C sunny)
          │
          ▼
    Combined upgrade query:
      "{weather_signal} {history_signal} upgrade value"
      e.g. "cold drinks iced refreshing Burgers upgrade value"
          │
          ▼
    ES: hybrid_search(jollibee-menu, upgrade_query,
                      geo_filter={lat, lon, distance_km=10})
        filter out items already in cart
        → top 4 upgrade candidates
          │
          ▼
    ES: /_inference/completion/claude-haiku
        prompt includes:
          - cart contents
          - candidate names
          - customer tier
          - weather context ("32°C, sunny")
          - history context ("favourite: Burgers")
          - spend gap ("₱120 below your average")
        → JSON array: [{ item, pitch }, ...]
          │
          ▼
    UI: upsize panel with context badges
        ☀️ sunny · 32°C at SM North EDSA
        📋 You usually order: Burgers
        + per-item pitch referencing both signals
```

**Query priority logic:**
- Both signals available → `"{weather} {history} upgrade value"`
- Weather only → `"{weather} large family upgrade"`
- History only → `"{history} large bucket family upgrade value meal"`
- Neither → `"large family bucket upsize party tray value meal"`

---

## Customer data updates

Every transaction triggers a read-mutate-write cycle entirely within ES. There is no separate database.

```
POST /api/transactions
          │
          ▼
1. GET /jollibee-customers/_doc/{customer_id}
   → full customer document into Python dict
          │
          ▼
2. Python mutations (in-memory, no ES write yet):
   customer["loyalty_profile"]["total_points"]      += points_earned
   customer["loyalty_profile"]["points_earned_ytd"] += points_earned
   customer["loyalty_profile"]["annual_spending"]   += order_total
   customer["loyalty_profile"]["last_activity"]      = now()
   customer["purchase_behavior"]["total_orders"]    += 1
          │
          ▼
3. check_tier_upgrade(annual_spending):
   ≥ ₱5,000  → "BeeElite"
   ≥ ₱2,000  → "BeeFan"
   default   → "BeeBuddy"
   → customer["loyalty_profile"]["tier"] = new_tier
          │
          ▼
4. POST /_bulk  (single round trip, three index writes)
   { "index": { "_index": "jollibee-transactions", "_id": txn_id } }
   { ...transaction document... }
   { "index": { "_index": "jollibee-customers",    "_id": customer_id } }
   { ...full updated customer document... }
   { "index": { "_index": "jollibee-inventory",    "_id": inv_id } }
   { ...decremented stock, updated status... }
          │
          ▼
5. POST /jollibee-transactions,jollibee-customers,jollibee-inventory/_refresh
   → all three indices immediately searchable
          │
          ▼
6. Response: { points_earned, order_total, tier_upgraded, new_tier }
```

**Full document replace:** The customer document is overwritten in full on every transaction (`index` action, not `update`). There are no partial field patches or ES update scripts. This simplifies the code but means any field not present in the Python dict will be silently deleted.

**Bulk simulation:** `POST /api/simulate/bulk-orders` batches up to 35 transactions in a single `_bulk` call. Customers are fetched once as a group (`terms` query on `_id`), mutated in memory per transaction, then all written in one bulk body. This reduces ES round trips from `2N+1` (N transactions × get + write + 1 refresh) to `3` (batch get + bulk write + refresh).

### Points calculation

```python
base   = int(order_total / 100) * (15 if channel in ["app","delivery"] else 10)
points = int(base * tier_multiplier)

# tier_multiplier:
#   BeeBuddy → 1.0×
#   BeeFan   → 1.2×
#   BeeElite → 1.5×
```

---

## Claude via Elasticsearch inference

Claude is accessed exclusively through Elasticsearch's inference API. The Python application never calls the Anthropic API directly.

### Endpoint used

```
.anthropic-claude-4.5-haiku-completion
  task_type:  completion
  service:    elastic   (Elastic-managed, GA status)
  model_id:   anthropic-claude-4.5-haiku
```

This endpoint is pre-provisioned in Elastic Cloud. Elastic holds the Anthropic credential internally — no `ANTHROPIC_API_KEY` is needed in the application at runtime.

### Call pattern

```python
# elasticsearch_client.py
def claude_complete(self, prompt: str) -> str:
    url  = f"{self.endpoint}/_inference/completion/{Config.CLAUDE_INFERENCE_ID}"
    body = {"input": prompt}
    resp = requests.post(url, headers=self.headers, json=body, timeout=25)
    # Response shape: { "completion": [{ "result": "..." }] }
    return resp.json()["completion"][0]["result"].strip()
```

### Claude is called for copy only — not ranking

| Feature | Claude call | What ES already did |
|---|---|---|
| History recs | One-sentence insight | RRF ranking, history retrieval |
| Weather recs | Weather → query JSON + reason | Nothing yet — query comes from Claude |
| Upsize | Per-item pitch + insight sentence | Hybrid search on combined query |
| Hybrid search | **Not called** | RRF is the complete answer |

**Why Haiku:** All three prompts are short-output structured tasks (JSON object, one sentence, small JSON array). Haiku is faster and cheaper than Sonnet for this pattern. The `rank_constant=60` and `rank_window_size=100` in RRF are the performance-sensitive parameters — those run inside ES with no latency budget concerns.

---

## API reference

### Search

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/menu/search` | Hybrid BM25+ELSER search. Body: `{ query, lat?, lon?, category? }` |
| `GET` | `/api/menu/search` | Same, via query params: `?q=&lat=&lon=&category=` |

### Recommendations

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/customers/<id>/recommendations` | History-based recs. Optional `?lat=&lon=` |
| `GET` | `/api/customers/<id>/recommendations/weather` | Weather recs. Required `?lat=&lon=` |
| `POST` | `/api/customers/<id>/upsize` | Pre-checkout upsize. Body: `{ cart, lat?, lon?, store_id? }` |

### Transactions

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/transactions` | Single transaction. Triggers bulk write to 3 indices. |
| `POST` | `/api/simulate/bulk-orders` | Simulate 15–35 orders. Body: `{ scenario, store_id }` |

### Stores

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/stores/locations` | All stores with lat/lon. Feeds weather panel dropdown. |
| `GET` | `/api/stores/nearby` | `geo_distance` sort. Params: `?lat=&lon=&distance_km=` |

### Analytics

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/analytics/stores` | 24h revenue, order count, avg order per store |
| `GET` | `/api/analytics/inventory` | Stock levels + reorder recs. Param: `?store_id=` |
| `GET` | `/api/analytics/customer-segments` | Tier breakdown with avg spend and frequency |
| `GET` | `/api/customers/<id>` | Full customer profile |
| `POST` | `/api/customers/<id>/redeem` | Redeem points. Body: `{ points, item_name }` |

### System

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | ES cluster health + connection status |
| `GET` | `/api/debug/inventory` | Raw inventory counts per store |

---

## Setup and configuration

### Prerequisites

- Python 3.11+
- Elastic Cloud deployment (ES 8.8+ for RRF support)
- ELSER v2 model deployed in your ES cluster
- `.anthropic-claude-4.5-haiku-completion` inference endpoint active (pre-provisioned in Elastic Cloud)
- SSL certificates (`cert.pem` + `key.pem`) for HTTPS, or run plain HTTP

### Environment variables

```bash
# Required
ELASTICSEARCH_ENDPOINT=https://your-deployment.es.us-east-2.aws.elastic-cloud.com
ELASTICSEARCH_API_KEY=your_api_key_here

# Claude inference (default matches Elastic Cloud pre-provisioned endpoint)
CLAUDE_INFERENCE_ID=.anthropic-claude-4.5-haiku-completion

# Flask
FLASK_HOST=0.0.0.0
FLASK_PORT=3443
FLASK_DEBUG=false

# Optional overrides
ELSER_MODEL_ID=.elser_model_2_linux-x86_64
ELSER_PIPELINE_NAME=jollibee-elser-pipeline
LOG_LEVEL=INFO
```

### Install and run

```bash
# Clone and install
git clone https://github.com/mikecali/loyalty_systems_elasticsearch_demo
cd loyalty_systems_elasticsearch_demo
pip install -r requirements.txt

# Copy env template
cp .env.example .env
# Edit .env with your ES endpoint and API key

# Optional: generate self-signed cert for HTTPS
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes

# Run
python3 app.py
```

### Verify ES inference endpoint

```bash
curl -X POST "${ELASTICSEARCH_ENDPOINT}/_inference/completion/.anthropic-claude-4.5-haiku-completion" \
  -H "Authorization: ApiKey ${ELASTICSEARCH_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"input": "Say BeeLoyalty in three words."}'
```

Expected: `{ "completion": [{ "result": "..." }] }`

---

## Project structure

```
.
├── app.py                  # Flask routes — all thin wrappers
├── jollibee_service.py     # Business logic, signal building, AI orchestration
├── elasticsearch_client.py # ES HTTP client: hybrid search, geo, inference, bulk
├── config.py               # Environment config and index name constants
├── templates.py            # Single-page dashboard HTML + JS
├── requirements.txt
├── .env                    # Not committed
├── cert.pem                # SSL cert (generated locally)
└── key.pem                 # SSL key  (generated locally)
```

### Responsibility split

| File | Owns |
|---|---|
| `elasticsearch_client.py` | All ES HTTP calls: search, bulk write, geo, inference, aggregations |
| `jollibee_service.py` | Preference signal logic, weather fetch, query assembly, response shaping |
| `app.py` | Route definitions, parameter extraction, error handling |
| `templates.py` | UI, store coordinate cache, upsize panel, weather panel JS |

---

## MVP limitations

### Race condition on concurrent orders

Two simultaneous orders for the same customer will both `GET` the same pre-transaction customer document, compute points independently, and the second `_bulk` write will silently overwrite the first. Points from the first transaction are lost.

**Production fix:** Use ES optimistic concurrency (`if_seq_no` + `if_primary_term` on the bulk index action), or move the increment to an ES Painless update script that runs atomically server-side:

```json
POST /jollibee-customers/_update/{customer_id}
{
  "script": {
    "source": "ctx._source.loyalty_profile.total_points += params.pts",
    "params": { "pts": 45 }
  }
}
```

### Full document replace

The customer document is replaced in full on every transaction. Any field added directly in Kibana or via another process and not present in the Python dict will be deleted on the next order.

### No session / auth layer

The demo accepts any `customer_id` without authentication. In production, the Flask routes would validate a JWT or session token before allowing reads or writes.

### Weather defaults to Metro Manila

If the application server has no outbound internet access to `api.open-meteo.com`, all weather-based features fall back to `30°C, sunny`. The `source` field in the response distinguishes live from default.

### Annual spending not reset on calendar year

`annual_spending` accumulates indefinitely. Tier recalculation is based on this lifetime figure. A production system would need a scheduled job to snapshot and reset `annual_spending` at the start of each loyalty year.
