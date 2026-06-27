"""
Modernized Elasticsearch Client — Jollibee BeeLoyalty System
Hybrid Search (BM25 + ELSER RRF), Geo queries, ES-managed Claude inference.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Union

import requests

from config import Config

logging.basicConfig(level=getattr(logging, Config.LOG_LEVEL), format=Config.LOG_FORMAT)
logger = logging.getLogger(__name__)


class ElasticsearchClient:

    def __init__(self):
        Config.validate()
        self.endpoint = Config.ELASTICSEARCH_ENDPOINT.rstrip('/')
        self.headers  = {
            "Authorization": f"ApiKey {Config.ELASTICSEARCH_API_KEY}",
            "Content-Type":  "application/json"
        }
        logger.info(f"Initialized Elasticsearch client → {self.endpoint}")

    # ── Core HTTP ──────────────────────────────────────────────────────────────

    def request(self, method: str, path: str,
                data: Optional[Dict] = None,
                raw_body: Optional[str] = None,
                extra_headers: Optional[Dict] = None) -> Optional[requests.Response]:
        url  = f"{self.endpoint}{path}"
        hdrs = {**self.headers, **(extra_headers or {})}
        try:
            kwargs: Dict = {"headers": hdrs, "timeout": 30}
            if raw_body is not None:
                kwargs["data"] = raw_body
            elif data is not None:
                kwargs["json"] = data
            resp = getattr(requests, method.lower())(url, **kwargs)
            logger.debug(f"{method.upper()} {url} → {resp.status_code}")
            return resp
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {method} {url} – {e}")
            return None

    # ── Cluster ────────────────────────────────────────────────────────────────

    def health_check(self) -> Dict:
        resp = self.request("GET", "/_cluster/health")
        if resp and resp.status_code == 200:
            d = resp.json()
            return {"status": "healthy", "cluster_status": d.get("status"),
                    "cluster_name": d.get("cluster_name"),
                    "timestamp": datetime.now().isoformat()}
        return {"status": "unhealthy", "timestamp": datetime.now().isoformat()}

    def refresh_index(self, index_names: Union[str, List[str]]) -> bool:
        indices = ",".join(index_names) if isinstance(index_names, list) else index_names
        resp    = self.request("POST", f"/{indices}/_refresh")
        return bool(resp and resp.status_code == 200)

    # ── Index management ───────────────────────────────────────────────────────

    def create_index(self, index_name: str, mapping: Dict) -> bool:
        self.request("DELETE", f"/{index_name}")
        resp = self.request("PUT", f"/{index_name}", mapping)
        ok   = bool(resp and resp.status_code in [200, 201])
        if ok:
            logger.info(f"Created index: {index_name}")
        else:
            logger.error(f"Failed to create index: {index_name} — "
                         f"{resp.text if resp else 'no response'}")
        return ok

    # ── Document CRUD ──────────────────────────────────────────────────────────

    def get_document(self, index_name: str, doc_id: str) -> Optional[Dict]:
        resp = self.request("GET", f"/{index_name}/_doc/{doc_id}")
        if resp and resp.status_code == 200:
            return resp.json()["_source"]
        logger.warning(f"Document not found: {index_name}/{doc_id}")
        return None

    def update_document(self, index_name: str, doc_id: str, document: Dict) -> bool:
        resp = self.request("PUT", f"/{index_name}/_doc/{doc_id}", document)
        return bool(resp and resp.status_code in [200, 201])

    def aggregation_search(self, index_name: str, query: Dict) -> Dict:
        resp = self.request("POST", f"/{index_name}/_search", query)
        if resp and resp.status_code == 200:
            return resp.json()
        logger.error(f"Aggregation search failed on {index_name}")
        return {}

    def count_documents(self, index_name: str, query: Optional[Dict] = None) -> int:
        query = query or {"query": {"match_all": {}}}
        resp  = self.request("POST", f"/{index_name}/_count", query)
        if resp and resp.status_code == 200:
            return resp.json().get("count", 0)
        return 0

    def delete_by_query(self, index_name: str, query: Dict) -> bool:
        resp = self.request("POST", f"/{index_name}/_delete_by_query", query)
        return bool(resp and resp.status_code == 200)

    # ── Bulk indexing ──────────────────────────────────────────────────────────

    def bulk_index(self, index_name: str, documents: List[Dict]) -> bool:
        bulk_lines = []
        for doc in documents:
            doc_id = self._determine_document_id(index_name, doc) or str(uuid.uuid4())
            bulk_lines.append(json.dumps({"index": {"_index": index_name, "_id": doc_id}}))
            bulk_lines.append(json.dumps(doc))
        bulk_body = "\n".join(bulk_lines) + "\n"
        resp = requests.post(
            f"{self.endpoint}/_bulk",
            headers={**self.headers, "Content-Type": "application/x-ndjson"},
            data=bulk_body, timeout=60
        )
        if resp.status_code == 200:
            errors = [i for i in resp.json().get("items", [])
                      if "error" in i.get("index", {})]
            if errors:
                logger.warning(f"{len(errors)} bulk index errors")
            return len(errors) < len(documents) * 0.1
        logger.error(f"Bulk index failed: {resp.status_code} — {resp.text}")
        return False

    def _determine_document_id(self, index_name: str, doc: Dict) -> Optional[str]:
        priorities = {
            Config.INDEX_INVENTORY:    ["inventory_id", "id"],
            Config.INDEX_CUSTOMERS:    ["customer_id",  "id"],
            Config.INDEX_TRANSACTIONS: ["transaction_id","id"],
            Config.INDEX_STORES:       ["store_id",     "id"],
            Config.INDEX_MENU:         ["item_id",      "id"],
        }
        for field in priorities.get(index_name, ["id"]):
            if doc.get(field):
                return str(doc[field])
        for k, v in doc.items():
            if k.endswith("_id") and v:
                return str(v)
        return None

    # ── ① Pure ELSER (baseline fallback) ──────────────────────────────────────

    def semantic_search(self, index_name: str, query_text: str, size: int = 10,
                        source_fields: Optional[List[str]] = None) -> Dict:
        query: Dict = {
            "query": {"text_expansion": {"ml.tokens": {
                "model_id": Config.ELSER_MODEL_ID,
                "model_text": query_text
            }}},
            "size": size
        }
        if source_fields:
            query["_source"] = source_fields
        resp = self.request("POST", f"/{index_name}/_search", query)
        if resp and resp.status_code == 200:
            return resp.json()
        return {"hits": {"hits": []}}

    # ── ② Hybrid Search: BM25 + ELSER via RRF ─────────────────────────────────

    def hybrid_search(self, index_name: str, query_text: str, size: int = 10,
                      source_fields: Optional[List[str]] = None,
                      geo_filter: Optional[Dict] = None,
                      category_filter: Optional[str] = None,
                      boost_bestseller: bool = True) -> Dict:
        """
        Elasticsearch RRF fusion of BM25 + ELSER — all ranking done server-side.
        geo_filter = {"lat": float, "lon": float, "distance_km": float}
        """
        filters = []
        if geo_filter:
            filters.append({"geo_distance": {
                "distance": f"{geo_filter['distance_km']}km",
                "location": {"lat": geo_filter["lat"], "lon": geo_filter["lon"]}
            }})
        if category_filter:
            filters.append({"term": {"category.keyword": category_filter}})

        bm25_query: Dict = {"bool": {"must": [{"multi_match": {
            "query":  query_text,
            "fields": ["name^3", "searchable_text^2", "category^1.5", "description"],
            "type":   "best_fields",
            "fuzziness": "AUTO"
        }}]}}
        if boost_bestseller:
            bm25_query["bool"]["should"] = [
                {"term": {"is_bestseller": {"value": True, "boost": 2.0}}}
            ]
        if filters:
            bm25_query["bool"]["filter"] = filters

        elser_query: Dict = {"bool": {"must": [{"text_expansion": {"ml.tokens": {
            "model_id":   Config.ELSER_MODEL_ID,
            "model_text": query_text
        }}}]}}
        if filters:
            elser_query["bool"]["filter"] = filters

        body: Dict = {
            "retriever": {"rrf": {
                "retrievers": [
                    {"standard": {"query": bm25_query}},
                    {"standard": {"query": elser_query}}
                ],
                "rank_constant":   60,
                "rank_window_size": 100
            }},
            "size": size
        }
        if source_fields:
            body["_source"] = source_fields

        resp = self.request("POST", f"/{index_name}/_search", body)
        if resp and resp.status_code == 200:
            result = resp.json()
            logger.info(f"Hybrid search '{query_text}' → "
                        f"{len(result.get('hits', {}).get('hits', []))} hits")
            return result

        logger.warning("RRF hybrid search failed — falling back to ELSER")
        return self.semantic_search(index_name, query_text, size, source_fields)

    # ── ③ ES-managed Claude inference ─────────────────────────────────────────

    def claude_complete(self, prompt: str, inference_id: Optional[str] = None) -> str:
        """
        POST /_inference/completion/{inference_id}

        Uses the Elastic-managed endpoint (service: "elastic") — no Anthropic
        API key needed at runtime; Elastic holds the credential internally.

        Supported pre-provisioned endpoint used here:
          .anthropic-claude-4.5-haiku-completion  (task_type: completion, GA)

        Returns the completion text, or "" on failure.
        """
        iid  = inference_id or Config.CLAUDE_INFERENCE_ID
        url  = f"{self.endpoint}/_inference/completion/{iid}"
        body = {"input": prompt}

        try:
            resp = requests.post(url, headers=self.headers, json=body, timeout=25)
            if resp.status_code == 200:
                data = resp.json()
                text = data.get("completion", [{}])[0].get("result", "").strip()
                logger.info(f"ES inference [{iid}] → {len(text)} chars")
                return text
            logger.error(f"ES inference error {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.error(f"ES inference call failed: {e}")
        return ""

    # ── ④ Geo: nearest stores ──────────────────────────────────────────────────

    def find_nearby_stores(self, lat: float, lon: float,
                           distance_km: float = 5.0, size: int = 10) -> List[Dict]:
        query = {
            "query": {"geo_distance": {
                "distance": f"{distance_km}km",
                "location": {"lat": lat, "lon": lon}
            }},
            "sort": [{"_geo_distance": {
                "location": {"lat": lat, "lon": lon},
                "order": "asc", "unit": "km"
            }}],
            "size": size,
            "_source": ["store_id", "store_name", "address", "location",
                        "store_type", "operating_hours"]
        }
        resp = self.request("POST", f"/{Config.INDEX_STORES}/_search", query)
        if resp and resp.status_code == 200:
            hits    = resp.json().get("hits", {}).get("hits", [])
            results = []
            for hit in hits:
                store = hit["_source"]
                store["distance_km"] = round(
                    (hit.get("sort") or [0])[0] or 0, 2
                )
                results.append(store)
            logger.info(f"Found {len(results)} stores within {distance_km}km of ({lat},{lon})")
            return results
        logger.error("Geo store lookup failed")
        return []

    # ── ⑤ Transaction history for a customer ──────────────────────────────────

    def get_customer_order_history(self, customer_id: str, limit: int = 50) -> List[Dict]:
        query = {
            "query": {"term": {"customer_id": customer_id}},
            "sort":  [{"timestamp": {"order": "desc"}}],
            "size":  limit,
            "_source": ["items", "order_total", "channel", "timestamp",
                        "location", "day_of_week", "hour_of_day"]
        }
        resp = self.request("POST", f"/{Config.INDEX_TRANSACTIONS}/_search", query)
        if resp and resp.status_code == 200:
            return [h["_source"] for h in resp.json().get("hits", {}).get("hits", [])]
        return []

    # ── ⑥ Popular items near a lat/lon ────────────────────────────────────────

    def get_popular_items_near_location(self, lat: float, lon: float,
                                        distance_km: float = 3.0,
                                        top_n: int = 10) -> List[Dict]:
        nearby   = self.find_nearby_stores(lat, lon, distance_km, size=20)
        store_ids = [s["store_id"] for s in nearby]
        if not store_ids:
            return []
        query = {
            "query": {"terms": {"store_id": store_ids}},
            "size":  0,
            "aggs":  {"popular_items": {"nested": {"path": "items"}, "aggs": {
                "item_names": {"terms": {
                    "field": "items.name.keyword",
                    "size":  top_n,
                    "order": {"_count": "desc"}
                }}
            }}}
        }
        resp = self.request("POST", f"/{Config.INDEX_TRANSACTIONS}/_search", query)
        if resp and resp.status_code == 200:
            buckets = (resp.json()
                       .get("aggregations", {})
                       .get("popular_items", {})
                       .get("item_names", {})
                       .get("buckets", []))
            return [{"name": b["key"], "order_count": b["doc_count"]} for b in buckets]
        return []
