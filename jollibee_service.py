"""
Modernized Jollibee BeeLoyalty Service
All Claude AI calls go through es_client.claude_complete() which hits:
  POST /_inference/completion/.anthropic-claude-4.5-haiku-completion
Elastic manages the Anthropic credential — zero API keys in Python.
"""

import uuid
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests as http_requests

from elasticsearch_client import ElasticsearchClient
from config import Config

logger = logging.getLogger(__name__)


# ── Weather helper (Open-Meteo, free, no key) ──────────────────────────────

def _fetch_weather(lat: float, lon: float) -> Dict:
    """
    Fetch live weather from Open-Meteo (free, no key).
    Falls back to Manila defaults if the server has no outbound internet.
    """
    try:
        url  = (f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}&current_weather=true&timezone=auto")
        resp = http_requests.get(url, timeout=8)
        if resp.status_code == 200:
            cw = resp.json().get("current_weather", {})
            return {"temperature_c": cw.get("temperature", 30),
                    "condition":     _wmo_to_label(cw.get("weathercode", 0)),
                    "weathercode":   cw.get("weathercode", 0),
                    "source":        "live"}
        logger.warning(f"Open-Meteo non-200: {resp.status_code}")
    except Exception as e:
        logger.warning(f"Open-Meteo unreachable: {e} — using defaults")
    # Default: hot and sunny (typical Metro Manila)
    return {"temperature_c": 30, "condition": "sunny",
            "weathercode": 0, "source": "default"}




def _wmo_to_label(code: int) -> str:
    if code == 0:               return "sunny"
    if code in range(1, 4):    return "partly cloudy"
    if code in range(45, 68):  return "rainy"
    if code in range(71, 78):  return "cold"
    if code in range(80, 100): return "stormy"
    return "cloudy"


# ── Preference signal builder ───────────────────────────────────────────────

def _weather_to_default_query(condition: str, temp: float) -> str:
    """Rule-based fallback query when Claude is unavailable."""
    if temp >= 32 or condition == "sunny":
        return "cold drinks iced refreshing milkshake sundae"
    if condition in ("rainy", "stormy"):
        return "hot soup hearty family meal comfort food"
    if condition == "cold":
        return "hot chocolate warm meal chicken soup"
    return "bestseller popular chicken joy burger"


def _build_preference_signal(history: List[Dict]) -> Dict:
    """
    Build a weighted preference signal from order history.

    Key principles:
      1. RECENCY — orders in the last 7 days get 3×, last 30 days 2×, older 1×
      2. CATEGORY over item name — "Burgers" beats "Double Cheesy Yumburger"
         for the search query so ES can find related items, not just exact repeats
      3. DEDUPLICATION — each unique category/item contributes once to the
         query regardless of how many times it's named
      4. TREND DETECTION — if the most recent 3 orders are all one category,
         that category is boosted as the primary signal

    Returns:
      {
        "search_query": str,          # clean query for ES hybrid search
        "primary_category": str,      # dominant recent category
        "trending_toward": str|None,  # category shift detected
        "top_categories": [...],      # ranked [(cat, weighted_score), ...]
        "top_items": [...]            # ranked [(item_name, weighted_score), ...]
      }
    """
    now = datetime.now()

    cat_scores:  Dict[str, float] = {}
    item_scores: Dict[str, float] = {}

    for order in history:
        # Recency multiplier
        try:
            ts  = datetime.fromisoformat(order.get("timestamp", ""))
            age = (now - ts).days
        except Exception:
            age = 999
        weight = 3.0 if age <= 7 else (2.0 if age <= 30 else 1.0)

        for item in order.get("items", []):
            name = item.get("name", "").strip()
            cat  = item.get("category", "").strip()
            qty  = item.get("quantity", 1)

            if cat:
                cat_scores[cat]   = cat_scores.get(cat, 0) + (weight * qty)
            if name:
                item_scores[name] = item_scores.get(name, 0) + (weight * qty)

    top_cats  = sorted(cat_scores.items(),  key=lambda x: x[1], reverse=True)
    top_items = sorted(item_scores.items(), key=lambda x: x[1], reverse=True)

    # Trend detection: what category do the 3 most recent orders belong to?
    recent_cats = []
    for order in history[:3]:
        for item in order.get("items", []):
            c = item.get("category", "")
            if c and c not in recent_cats:
                recent_cats.append(c)

    trending_toward = recent_cats[0] if recent_cats else None
    primary_cat     = top_cats[0][0] if top_cats else None

    # Build the search query:
    #   - Lead with trending category (recency signal)
    #   - Then top 2 weighted categories (breadth signal)
    #   - Then top 2 weighted item names (specificity signal)
    #   - Deduplicated, max ~8 tokens so ES isn't confused by a wall of text
    query_parts = []
    if trending_toward:
        query_parts.append(trending_toward)
    for cat, _ in top_cats[:2]:
        if cat not in query_parts:
            query_parts.append(cat)
    for name, _ in top_items[:2]:
        # Use only the first 3 words of item names to keep the query clean
        short = " ".join(name.split()[:3])
        if short not in query_parts:
            query_parts.append(short)

    search_query = " ".join(query_parts)

    logger.info(
        f"Preference signal → query='{search_query}' | "
        f"primary_cat='{primary_cat}' | trending='{trending_toward}'"
    )

    return {
        "search_query":    search_query,
        "primary_category": primary_cat,
        "trending_toward": trending_toward,
        "top_categories":  top_cats[:5],
        "top_items":       top_items[:5]
    }


# ── Main service ────────────────────────────────────────────────────────────

class JollibeeService:
    """
    Business logic layer.
    Claude calls: self.es_client.claude_complete(prompt)
    ES inference endpoint: .anthropic-claude-4.5-haiku-completion
    """

    def __init__(self):
        self.es_client = ElasticsearchClient()
        logger.info("JollibeeService initialized — Claude via ES inference "
                    f"({Config.CLAUDE_INFERENCE_ID})")

    # ── Customer ───────────────────────────────────────────────────────────────

    def get_customer(self, customer_id: str) -> Optional[Dict]:
        c = self.es_client.get_document(Config.INDEX_CUSTOMERS, customer_id)
        if c:
            logger.info(f"Retrieved customer: {c['personal_info']['name']}")
        return c

    # ── Loyalty ────────────────────────────────────────────────────────────────

    def calculate_points(self, total: float, channel: str, tier: str) -> int:
        base = int(total / 100) * (15 if channel in ["app", "delivery"] else 10)
        return int(base * {"BeeBuddy": 1.0, "BeeFan": 1.2, "BeeElite": 1.5}.get(tier, 1.0))

    def check_tier_upgrade(self, annual: float) -> str:
        if annual >= 5000: return "BeeElite"
        if annual >= 2000: return "BeeFan"
        return "BeeBuddy"

    # ── ① Hybrid Search ────────────────────────────────────────────────────────

    def search_menu(self, query_text: str, limit: int = 10,
                    lat: Optional[float] = None, lon: Optional[float] = None,
                    category: Optional[str] = None) -> List[Dict]:
        geo = {"lat": lat, "lon": lon, "distance_km": 10.0} if lat is not None else None
        results = self.es_client.hybrid_search(
            Config.INDEX_MENU, query_text, size=limit,
            source_fields=["name", "category", "price", "description",
                           "points_value", "is_new", "is_bestseller"],
            geo_filter=geo, category_filter=category
        )
        items = []
        for hit in results.get("hits", {}).get("hits", []):
            s = hit["_source"]
            items.append({
                "name": s["name"], "category": s["category"],
                "price": s["price"], "description": s["description"],
                "points_value": s.get("points_value", 0),
                "is_new": s.get("is_new", False),
                "is_bestseller": s.get("is_bestseller", False),
                "relevance_score": hit.get("_score") or hit.get("_rank")
            })
        logger.info(f"Hybrid search '{query_text}' → {len(items)} results")
        return items

    # ── ② AI Recs: Order History ───────────────────────────────────────────────

    def get_history_based_recommendations(self, customer_id: str, limit: int = 6,
                                          lat: Optional[float] = None,
                                          lon: Optional[float] = None) -> Dict:
        """
        Recommendation logic:
          ES:     retrieves order history (newest first), builds recency-weighted
                  preference signal, runs hybrid search on category-led query.
          Claude: one-sentence insight explaining the recommendation angle
                  (references the trend if one is detected).

        The preference signal prioritises:
          - What the customer ordered RECENTLY (last 7 days: 3× weight)
          - Category-level signal so ES finds related items, not just repeats
          - Detected category shift (e.g. chicken → burgers) surfaces as the
            primary search driver
        """
        customer = self.get_customer(customer_id)
        if not customer:
            return {"recommendations": [], "insight": "Customer not found"}

        history = self.es_client.get_customer_order_history(customer_id, limit=30)
        if not history:
            return self._fallback_popular_recommendations(lat, lon, limit)

        signal = _build_preference_signal(history)

        # Optional: if a strong trend is detected toward a specific category,
        # lock the ES category filter for precision
        cat_filter = None
        if (signal["trending_toward"]
                and signal["trending_toward"] != signal["primary_category"]):
            # Customer is shifting — follow the trend
            cat_filter = signal["trending_toward"]
            logger.info(f"Trend shift detected → filtering by '{cat_filter}'")

        menu_results = self.search_menu(
            signal["search_query"], limit=limit, lat=lat, lon=lon,
            category=cat_filter
        )

        # If category filter returned too few results, fall back to unfiltered
        if len(menu_results) < 3 and cat_filter:
            logger.info("Category filter too narrow — retrying without filter")
            menu_results = self.search_menu(
                signal["search_query"], limit=limit, lat=lat, lon=lon
            )

        # Claude via ES inference — give it the signal context, not raw item names
        top_cats_str  = ", ".join(c for c, _ in signal["top_categories"][:3])
        top_items_str = ", ".join(n for n, _ in signal["top_items"][:3])
        trend_note    = (
            f"Recently shifting toward: {signal['trending_toward']}."
            if signal["trending_toward"] and
               signal["trending_toward"] != signal["primary_category"]
            else ""
        )

        prompt = (
            "You are a Jollibee loyalty app assistant. "
            "Write ONE sentence (max 25 words) explaining why these recommendations suit this customer. "
            "Reference their recent trend if present. Be warm and specific. No preamble.\n\n"
            f"Customer: {customer['personal_info']['name']} | "
            f"Tier: {customer['loyalty_profile']['tier']} | "
            f"Favourite categories: {top_cats_str} | "
            f"Recent items: {top_items_str} | "
            f"{trend_note}"
        )
        insight = (
            self.es_client.claude_complete(prompt)
            or f"Picked for your love of {signal['primary_category'] or 'Jollibee classics'}!"
        )

        return {
            "customer_name":    customer["personal_info"]["name"],
            "tier":             customer["loyalty_profile"]["tier"],
            "recommendations":  menu_results,
            "insight":          insight,
            "based_on":         f"{len(history)} past orders",
            "top_ordered":      [n for n, _ in signal["top_items"][:5]],
            "preference_signal": {
                "query_used":       signal["search_query"],
                "primary_category": signal["primary_category"],
                "trending_toward":  signal["trending_toward"]
            }
        }

    # ── ③ AI Recs: Weather ─────────────────────────────────────────────────────

    def get_weather_based_recommendations(self, customer_id: str,
                                          lat: float, lon: float,
                                          limit: int = 6) -> Dict:
        """
        Open-Meteo: live weather at lat/lon (falls back to Manila defaults).
        Claude (ES inference): maps weather -> {"query": ..., "reason": ...}.
          - Strips markdown fences if Claude ignores the no-markdown instruction
          - Falls back gracefully if JSON parse fails
        ES: hybrid search on weather-derived query + geo nearest store.
        """
        customer  = self.get_customer(customer_id)
        cust_name = customer["personal_info"]["name"] if customer else "there"
        weather   = _fetch_weather(lat, lon)

        temp      = weather["temperature_c"]
        condition = weather["condition"]
        source    = weather.get("source", "live")
        logger.info(f"Weather for ({lat},{lon}): {temp}°C, {condition} [{source}]")

        # Defaults — used if Claude call fails or returns unparseable output
        query  = _weather_to_default_query(condition, temp)
        reason = f"Great picks for {condition} weather at {temp}°C!"

        prompt = (
            "You are a food recommendation engine for Jollibee Philippines. "
            "Respond with ONLY a raw JSON object — no markdown, no code fences, "
            "no explanation before or after. Two keys only:\n"
            '  "query": string — 4-7 words describing the ideal menu items for this weather\n'
            '  "reason": string — one sentence, max 18 words, why these suit the weather\n'
            f"Weather: {temp}°C, {condition}"
        )

        raw = self.es_client.claude_complete(prompt)
        logger.info(f"Claude weather raw response: {raw!r}")

        if raw:
            # Strip markdown fences (Claude sometimes wraps JSON despite instructions)
            cleaned = raw.strip()
            if "```" in cleaned:
                cleaned = "\n".join(
                    l for l in cleaned.split("\n")
                    if not l.strip().startswith("```")
                ).strip()

            try:
                parsed = json.loads(cleaned)
                query  = parsed.get("query", query).strip()
                reason = parsed.get("reason", reason).strip()
                logger.info(f"Weather query from Claude: {query!r}")
            except json.JSONDecodeError:
                # Claude returned prose — extract the first sentence as query
                logger.warning(f"Claude weather JSON parse failed, using line extraction")
                lines = [l.strip() for l in raw.split("\n") if l.strip()]
                if lines:
                    query = lines[0][:80]

        menu_results = self.search_menu(query, limit=limit, lat=lat, lon=lon)
        nearby       = self.es_client.find_nearby_stores(lat, lon, distance_km=5.0, size=1)

        return {
            "customer_name":     cust_name,
            "weather":           {"temperature_c": temp, "condition": condition,
                                  "source": source},
            "search_query_used": query,
            "reason":            reason,
            "recommendations":   menu_results,
            "nearest_store":     nearby[0] if nearby else None,
            "location":          {"lat": lat, "lon": lon}
        }

    # ── ④ Upsize Suggestions ───────────────────────────────────────────────────

    def get_upsize_suggestions(self, customer_id: str,
                               current_cart: List[Dict],
                               lat: Optional[float] = None,
                               lon: Optional[float] = None) -> Dict:
        """
        Context-aware upsize suggestions combining TWO signals:

        Signal 1 — ORDER HISTORY (ES):
          Recency-weighted preference signal (_build_preference_signal).
          Surfaces trending category and avg order value for spend-gap message.

        Signal 2 — WEATHER (Open-Meteo + Claude via ES inference):
          Fetches live weather at the store lat/lon.
          Maps weather → food mood query (e.g. 32°C sunny → iced drinks).

        Both signals combine into a single hybrid search query for candidates.
        Claude writes pitches that reference weather AND the customer's taste.

        Returns UI context flags: weather_used, weather_condition, weather_temp,
        history_used, top_category, insight.
        """
        customer = self.get_customer(customer_id)
        if not customer:
            return {"suggestions": [], "message": ""}

        cart_total = sum(i.get("price", 0) * i.get("quantity", 1) for i in current_cart)
        cart_names = [i["name"] for i in current_cart]
        tier       = customer["loyalty_profile"]["tier"]

        # ── Signal 1: Order history ───────────────────────────────────────────
        history   = self.es_client.get_customer_order_history(customer_id, limit=30)
        avg_value = (sum(o.get("order_total", 0) for o in history) / len(history)
                     if history else 0)

        history_used   = bool(history)
        top_category   = None
        history_signal = ""
        if history:
            signal         = _build_preference_signal(history)
            top_category   = signal.get("trending_toward") or signal.get("primary_category")
            history_signal = top_category or ""

        # ── Signal 2: Weather at store location ───────────────────────────────
        weather_used      = False
        weather_condition = None
        weather_temp      = None
        weather_signal    = ""

        if lat is not None and lon is not None:
            weather           = _fetch_weather(lat, lon)
            weather_condition = weather["condition"]
            weather_temp      = weather["temperature_c"]
            weather_used      = True
            weather_signal    = _weather_to_default_query(weather_condition, weather_temp)
            logger.info(f"Upsize weather signal: {weather_temp}°C {weather_condition}")

        # ── Combined upgrade search query ─────────────────────────────────────
        if weather_signal and history_signal:
            upgrade_query = f"{weather_signal} {history_signal} upgrade value"
        elif weather_signal:
            upgrade_query = f"{weather_signal} large family upgrade"
        elif history_signal:
            upgrade_query = f"{history_signal} large bucket family upgrade value meal"
        else:
            upgrade_query = "large family bucket upsize party tray value meal"

        logger.info(f"Upsize search query: '{upgrade_query}'")

        candidates = [
            m for m in self.search_menu(upgrade_query, limit=10, lat=lat, lon=lon)
            if m["name"] not in cart_names
        ][:4]

        if not candidates:
            return {
                "suggestions": [], "message": "You've already got the best value combo!",
                "weather_used": weather_used, "history_used": history_used
            }

        # ── Spend-gap message ─────────────────────────────────────────────────
        gap = avg_value - cart_total
        opportunity = (
            f"Your average order is ₱{avg_value:.0f} — "
            f"you're ₱{gap:.0f} below your usual spend"
            if gap > 50 else "Add a perfect match to complete your order"
        )

        # ── Claude: contextual pitch per candidate ────────────────────────────
        weather_ctx = (f"Current weather at store: {weather_temp}°C, {weather_condition}."
                       if weather_used else "")
        history_ctx = (f"Customer's favourite category: {top_category}."
                       if top_category else "")

        prompt = (
            "You are a Jollibee upsell assistant. "
            "Write short, warm, non-pushy upgrade suggestions.\n\n"
            f"Cart: {', '.join(cart_names)}\n"
            f"Upgrade candidates: {', '.join(c['name'] for c in candidates)}\n"
            f"Customer tier: {tier}\n"
            f"{weather_ctx}\n"
            f"{history_ctx}\n"
            f"Spend context: {opportunity}\n\n"
            "Output ONLY a JSON array. Each element: "
            '{"item": "<exact candidate name>", '
            '"pitch": "<one warm sentence max 15 words referencing weather or their taste>"}. '
            "No markdown, no extra text."
        )
        raw = self.es_client.claude_complete(prompt)
        pitches: Dict[str, str] = {}
        if raw:
            cleaned = raw.strip()
            if "```" in cleaned:
                cleaned = "\n".join(
                    l for l in cleaned.split("\n")
                    if not l.strip().startswith("```")
                ).strip()
            try:
                for item in json.loads(cleaned):
                    pitches[item["item"]] = item["pitch"]
            except Exception:
                pass

        suggestions = [
            {**c, "pitch": pitches.get(
                c["name"],
                f"Perfect for {weather_condition} weather!" if weather_used
                else f"Upgrade to {c['name']} for the whole family!"
            )}
            for c in candidates
        ]

        # Short insight line (separate Claude call — fast with Haiku)
        insight = ""
        if weather_used or history_used:
            insight_prompt = (
                "Write ONE short sentence (max 15 words) explaining why these upgrades "
                "suit this customer right now. Warm, specific, no preamble.\n"
                f"{weather_ctx} {history_ctx}"
            )
            insight = self.es_client.claude_complete(insight_prompt) or ""

        return {
            "customer_name":        customer["personal_info"]["name"],
            "current_cart_total":   cart_total,
            "historical_avg_order": round(avg_value, 2),
            "opportunity":          opportunity,
            "suggestions":          suggestions,
            "insight":              insight,
            "weather_used":         weather_used,
            "weather_condition":    weather_condition,
            "weather_temp":         weather_temp,
            "history_used":         history_used,
            "top_category":         top_category,
        }

    # ── Fallback ───────────────────────────────────────────────────────────────

    def _fallback_popular_recommendations(self, lat, lon, limit) -> Dict:
        if lat and lon:
            popular = self.es_client.get_popular_items_near_location(lat, lon, top_n=limit)
            query   = " ".join(p["name"] for p in popular[:5]) or "bestseller popular"
        else:
            query = "bestseller popular"
        return {"recommendations": self.search_menu(query, limit=limit, lat=lat, lon=lon),
                "insight": "Popular choices near you!"}

    # ── Transactions ───────────────────────────────────────────────────────────

    def create_transaction(self, customer_id: str, items: List[Dict], channel: str,
                           store_info: Dict, payment_method: str = "cash") -> Tuple[bool, str, Dict]:
        customer = self.get_customer(customer_id)
        if not customer:
            return False, "Customer not found", {}

        order_total    = sum(i["price"] * i["quantity"] for i in items)
        tier           = customer["loyalty_profile"]["tier"]
        points_earned  = self.calculate_points(order_total, channel, tier)
        transaction_id = f"txn_{customer_id}_{str(uuid.uuid4())[:8]}"

        txn = {
            "transaction_id": transaction_id, "customer_id": customer_id,
            "store_id":  store_info.get("store_id", "store_001"),
            "timestamp": datetime.now().isoformat(),
            "channel":   channel, "location": store_info,
            "items":     items,   "order_total": order_total,
            "points_earned": points_earned, "points_redeemed": 0,
            "payment_method": payment_method, "order_type": channel,
            "hour_of_day": datetime.now().hour,
            "day_of_week": datetime.now().strftime("%A"),
            "is_weekend":  datetime.now().weekday() >= 5
        }

        new_annual    = customer["loyalty_profile"]["annual_spending"] + order_total
        new_tier      = self.check_tier_upgrade(new_annual)
        tier_upgraded = new_tier != tier

        customer["loyalty_profile"]["total_points"]      += points_earned
        customer["loyalty_profile"]["points_earned_ytd"] += points_earned
        customer["loyalty_profile"]["annual_spending"]    = new_annual
        customer["loyalty_profile"]["tier"]               = new_tier
        customer["loyalty_profile"]["last_activity"]      = datetime.now().isoformat()
        customer["purchase_behavior"]["total_orders"]     += 1
        customer.pop("ml", None)

        ok = self._batch_transaction_updates(
            transaction_id, txn, customer_id, customer,
            store_info.get("store_id", "store_001"), items
        )
        if not ok:
            return False, "Failed to process transaction", {}

        return True, f"Transaction successful! Earned {points_earned} BeePoints.", {
            "transaction_id": transaction_id, "order_total": order_total,
            "points_earned":  points_earned,  "tier_upgraded": tier_upgraded,
            "new_tier": new_tier if tier_upgraded else tier
        }

    def _batch_transaction_updates(self, transaction_id, txn_data,
                                   customer_id, customer_data,
                                   store_id, items) -> bool:
        try:
            bulk = ([{"index": {"_index": Config.INDEX_TRANSACTIONS, "_id": transaction_id}},
                     txn_data,
                     {"index": {"_index": Config.INDEX_CUSTOMERS,    "_id": customer_id}},
                     customer_data]
                    + self._prepare_inventory_updates(store_id, items))
            bulk_body = "\n".join(json.dumps(x) for x in bulk) + "\n"
            resp = http_requests.post(
                f"{self.es_client.endpoint}/_bulk",
                headers={**self.es_client.headers, "Content-Type": "application/x-ndjson"},
                data=bulk_body, timeout=30
            )
            if resp.status_code == 200:
                self.es_client.refresh_index([Config.INDEX_TRANSACTIONS,
                                              Config.INDEX_CUSTOMERS,
                                              Config.INDEX_INVENTORY])
                return True
            logger.error(f"Bulk update failed: {resp.status_code}")
            return False
        except Exception as e:
            logger.error(f"_batch_transaction_updates error: {e}")
            return False

    def _prepare_inventory_updates(self, store_id: str, items: List[Dict]) -> List:
        bulk = []
        for item in items:
            q = {"query": {"bool": {"must": [
                {"term":  {"store_id":  store_id}},
                {"match": {"item_name": item["name"]}}
            ]}}, "size": 1}
            res = self.es_client.aggregation_search(Config.INDEX_INVENTORY, q)
            if res and res.get("hits", {}).get("hits"):
                hit  = res["hits"]["hits"][0]
                inv  = hit["_source"];  inv_id = hit["_id"]
                inv["current_stock"] = max(0, inv["current_stock"] - item["quantity"])
                inv["timestamp"]     = datetime.now().isoformat()
                rp, stock = inv["reorder_point"], inv["current_stock"]
                inv["status"] = ("Critical" if stock <= rp * 0.5 else
                                 "Low"      if stock <= rp       else
                                 "Adequate" if stock <= rp * 2   else "Good")
                bulk += [{"index": {"_index": Config.INDEX_INVENTORY, "_id": inv_id}}, inv]
        return bulk

    def redeem_points(self, customer_id: str, points: int,
                      item_name: str) -> Tuple[bool, str, Dict]:
        customer = self.get_customer(customer_id)
        if not customer:
            return False, "Customer not found", {}
        current = customer["loyalty_profile"]["total_points"]
        if current < points:
            return False, f"Insufficient points. Have {current}, need {points}", {}
        customer["loyalty_profile"]["total_points"]        = current - points
        customer["loyalty_profile"]["points_redeemed_ytd"] += points
        customer["loyalty_profile"]["last_activity"]        = datetime.now().isoformat()
        customer.pop("ml", None)
        ok = self.es_client.update_document(Config.INDEX_CUSTOMERS, customer_id, customer)
        if ok:
            return True, f"Redeemed {points} points for {item_name}", \
                   {"new_balance": current - points}
        return False, "Failed to update points", {}

    def create_bulk_transactions(self, transaction_requests: List[Dict]) -> Dict:
        if not transaction_requests:
            return {"success": False, "error": "No transactions to process"}
        start    = datetime.now()
        cids     = list({r["customer_id"] for r in transaction_requests})
        custs    = self._fetch_customers_batch(cids)
        all_bulk = []; total_revenue = 0

        for req in transaction_requests:
            cid = req["customer_id"]; c = custs.get(cid)
            if not c: continue
            items  = req["items"]; channel = req["channel"]
            total  = sum(i["price"] * i["quantity"] for i in items)
            pts    = self.calculate_points(total, channel, c["loyalty_profile"]["tier"])
            tid    = f"txn_{cid}_{str(uuid.uuid4())[:8]}"
            all_bulk += [
                {"index": {"_index": Config.INDEX_TRANSACTIONS, "_id": tid}},
                {"transaction_id": tid, "customer_id": cid,
                 "store_id":  req["store_info"].get("store_id", "store_001"),
                 "timestamp": datetime.now().isoformat(),
                 "channel": channel, "location": req["store_info"],
                 "items": items, "order_total": total,
                 "points_earned": pts, "points_redeemed": 0,
                 "payment_method": req.get("payment_method", "cash"),
                 "order_type": channel,
                 "hour_of_day": datetime.now().hour,
                 "day_of_week": datetime.now().strftime("%A"),
                 "is_weekend":  datetime.now().weekday() >= 5}
            ]
            total_revenue += total
            new_annual = c["loyalty_profile"]["annual_spending"] + total
            c["loyalty_profile"]["total_points"]      += pts
            c["loyalty_profile"]["points_earned_ytd"] += pts
            c["loyalty_profile"]["annual_spending"]    = new_annual
            c["loyalty_profile"]["tier"]               = self.check_tier_upgrade(new_annual)
            c["loyalty_profile"]["last_activity"]      = datetime.now().isoformat()
            c["purchase_behavior"]["total_orders"]     += 1
            c.pop("ml", None); custs[cid] = c

        for cid, cdata in custs.items():
            all_bulk += [{"index": {"_index": Config.INDEX_CUSTOMERS, "_id": cid}}, cdata]

        bulk_body = "\n".join(json.dumps(x) for x in all_bulk) + "\n"
        resp = http_requests.post(
            f"{self.es_client.endpoint}/_bulk",
            headers={**self.es_client.headers, "Content-Type": "application/x-ndjson"},
            data=bulk_body, timeout=30
        )
        if resp.status_code == 200:
            self.es_client.refresh_index([Config.INDEX_TRANSACTIONS,
                                          Config.INDEX_CUSTOMERS, Config.INDEX_INVENTORY])
            n = len(transaction_requests)
            t = (datetime.now() - start).total_seconds()
            return {"success": True, "transactions_created": n,
                    "total_revenue": total_revenue, "processing_time_seconds": t}
        return {"success": False, "error": f"Bulk failed: {resp.status_code}"}

    def _fetch_customers_batch(self, customer_ids: List[str]) -> Dict:
        try:
            q    = {"query": {"terms": {"_id": customer_ids}}, "size": len(customer_ids)}
            resp = self.es_client.aggregation_search(Config.INDEX_CUSTOMERS, q)
            return {h["_id"]: h["_source"]
                    for h in resp.get("hits", {}).get("hits", [])}
        except Exception as e:
            logger.error(f"Batch customer fetch error: {e}")
            return {}

    def get_store_analytics(self) -> Dict:
        sr = self.es_client.aggregation_search(
            Config.INDEX_STORES, {"query": {"match_all": {}}, "size": 20})
        if not sr or not sr.get("hits", {}).get("hits"):
            return {"success": False, "error": "No stores found"}
        stores   = [h["_source"] for h in sr["hits"]["hits"]]
        last_24h = (datetime.now() - timedelta(hours=24)).isoformat()
        agg_q    = {
            "query": {"range": {"timestamp": {"gte": last_24h}}}, "size": 0,
            "aggs": {"stores": {"terms": {"field": "store_id", "size": 20}, "aggs": {
                "total_revenue":     {"sum":         {"field": "order_total"}},
                "order_count":       {"value_count": {"field": "transaction_id"}},
                "avg_order":         {"avg":         {"field": "order_total"}},
                "channel_breakdown": {"terms":       {"field": "channel"}}
            }}}
        }
        ar   = self.es_client.aggregation_search(Config.INDEX_TRANSACTIONS, agg_q)
        perf = {}
        if ar and ar.get("aggregations"):
            for b in ar["aggregations"]["stores"]["buckets"]:
                perf[b["key"]] = {
                    "recent_orders":   b["order_count"]["value"],
                    "recent_revenue":  b["total_revenue"]["value"],
                    "avg_order_value": b["avg_order"]["value"] or 0,
                    "channels": {c["key"]: c["doc_count"]
                                 for c in b["channel_breakdown"]["buckets"]}
                }
        enhanced = [{**s, **perf.get(s["store_id"], {
            "recent_orders": 0, "recent_revenue": 0,
            "avg_order_value": 0, "channels": {}
        })} for s in stores]
        return {"success": True, "stores": enhanced,
                "total_stores": len(enhanced),
                "last_updated": datetime.now().isoformat()}

    def get_inventory_analytics(self, store_id: str) -> Dict:
        q = {"query": {"bool": {"must": [{"term": {"store_id": store_id}}]}},
             "size": 100, "sort": [{"current_stock": {"order": "asc"}}]}
        resp = self.es_client.aggregation_search(Config.INDEX_INVENTORY, q)
        if not resp or not resp.get("hits", {}).get("hits"):
            return {"success": False, "error": f"No inventory for {store_id}"}
        items    = [h["_source"] for h in resp["hits"]["hits"]]
        critical = [i for i in items if i["status"] == "Critical"]
        low      = [i for i in items if i["status"] == "Low"]
        return {
            "success": True, "store_id": store_id,
            "inventory_summary": {
                "total_items":    len(items), "critical_items": len(critical),
                "low_items":      len(low),
                "adequate_items": len([i for i in items
                                       if i["status"] in ["Adequate", "Good"]])
            },
            "inventory_items": items,
            "recommendations": [
                {"item": i["item_name"],
                 "action": "CRITICAL: Immediate reorder!"
                           if i["status"] == "Critical" else "WARNING: Schedule reorder",
                 "current_stock": i["current_stock"],
                 "reorder_point": i["reorder_point"],
                 "priority": "high" if i["status"] == "Critical" else "medium"}
                for i in critical + low
            ],
            "last_updated": datetime.now().isoformat()
        }
