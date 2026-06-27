#!/usr/bin/env python3
"""
Modernized Jollibee BeeLoyalty System - Main Flask Application
Adds: Hybrid Search, History Recs, Weather Recs, Upsize Suggestions
"""

import logging
import json
import uuid
import random
import os
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS

from config import Config
from jollibee_service import JollibeeService
from templates import DASHBOARD_HTML, DEMO_HTML

logging.basicConfig(level=getattr(logging, Config.LOG_LEVEL), format=Config.LOG_FORMAT)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

jollibee_service = JollibeeService()

# =============================================================================
# WEB INTERFACE
# =============================================================================

@app.route('/')
def dashboard():
    return render_template_string(DASHBOARD_HTML)

@app.route('/demo')
def demo_page():
    return render_template_string(DEMO_HTML)

# =============================================================================
# HEALTH
# =============================================================================

@app.route('/api/health', methods=['GET'])
def health_check():
    try:
        return jsonify(jollibee_service.es_client.health_check())
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({"status": "unhealthy", "error": str(e), "timestamp": datetime.now().isoformat()}), 503

# =============================================================================
# CUSTOMERS
# =============================================================================

@app.route('/api/customers/<customer_id>', methods=['GET'])
def get_customer(customer_id):
    try:
        customer_data = jollibee_service.get_customer(customer_id)
        if customer_data:
            return jsonify({"success": True, "customer": customer_data})
        return jsonify({"success": False, "error": "Customer not found"}), 404
    except Exception as e:
        logger.error(f"Error retrieving customer {customer_id}: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500

# =============================================================================
# ② RECOMMENDATIONS — Order History  (was get_customer_recommendations)
# =============================================================================

@app.route('/api/customers/<customer_id>/recommendations', methods=['GET'])
def get_recommendations(customer_id):
    """
    Personalised recommendations from order history.
    Optional ?lat=&lon= enables geo-boosting of nearby popular items.

    ES does: transaction history retrieval, item-freq aggregation, hybrid search.
    Claude does: one-sentence personalised insight.
    """
    try:
        lat = request.args.get("lat", type=float)
        lon = request.args.get("lon", type=float)

        result = jollibee_service.get_history_based_recommendations(
            customer_id, limit=8, lat=lat, lon=lon
        )

        if not result.get("recommendations"):
            return jsonify({"success": False, "error": "No recommendations available"}), 404

        return jsonify({
            "success": True,
            "customer_name": result.get("customer_name"),
            "tier": result.get("tier"),
            "recommendations": result["recommendations"],
            "insight": result.get("insight", ""),
            "based_on": result.get("based_on", ""),
            "top_ordered": result.get("top_ordered", [])
        })
    except Exception as e:
        logger.error(f"Error getting recommendations for {customer_id}: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500

# =============================================================================
# ③ RECOMMENDATIONS — Weather + Geo
# =============================================================================

@app.route('/api/customers/<customer_id>/recommendations/weather', methods=['GET'])
def get_weather_recommendations(customer_id):
    """
    Weather-aware recommendations using customer's real-time location.
    Requires ?lat=&lon=

    Open-Meteo: live weather at coordinates (free, no key).
    ES: geo_distance filter + hybrid search on weather-derived query.
    Claude: weather → food mood translation.
    """
    try:
        lat = request.args.get("lat", type=float)
        lon = request.args.get("lon", type=float)

        if lat is None or lon is None:
            return jsonify({"success": False, "error": "lat and lon are required"}), 400

        result = jollibee_service.get_weather_based_recommendations(
            customer_id, lat=lat, lon=lon, limit=6
        )
        return jsonify({"success": True, **result})
    except Exception as e:
        logger.error(f"Weather recommendations error for {customer_id}: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500

# =============================================================================
# ④ UPSIZE SUGGESTIONS — Pre-checkout
# =============================================================================

@app.route('/api/customers/<customer_id>/upsize', methods=['POST'])
def get_upsize_suggestions(customer_id):
    """
    Pre-checkout upsize suggestions using order history + live weather.

    POST body: {
        "cart":     [{"name": "...", "price": 99, "quantity": 1}],
        "lat":      14.6565,   # store lat — optional, enables weather signal
        "lon":      121.0322,  # store lon — optional, enables weather signal
        "store_id": "store_001"
    }

    ES:     order history (recency-weighted) + hybrid search for upgrade candidates.
    Weather: Open-Meteo at store lat/lon → maps to food-mood query.
    Claude: pitch per candidate referencing both weather and customer's taste.
    """
    try:
        body = request.get_json(silent=True) or {}
        cart = body.get("cart", [])
        lat  = body.get("lat")
        lon  = body.get("lon")

        if not cart:
            return jsonify({"success": False, "error": "cart is required"}), 400

        result = jollibee_service.get_upsize_suggestions(
            customer_id,
            current_cart=cart,
            lat=float(lat) if lat is not None else None,
            lon=float(lon) if lon is not None else None,
        )
        return jsonify({"success": True, **result})
    except Exception as e:
        logger.error(f"Upsize suggestions error for {customer_id}: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500

# =============================================================================
# POINTS REDEMPTION
# =============================================================================

@app.route('/api/customers/<customer_id>/redeem', methods=['POST'])
def redeem_points(customer_id):
    try:
        data = request.get_json()
        points_to_redeem = data.get('points', 0)
        item_name = data.get('item_name', '')

        if points_to_redeem <= 0:
            return jsonify({"success": False, "error": "Invalid points amount"}), 400

        success, message, result_data = jollibee_service.redeem_points(
            customer_id, points_to_redeem, item_name
        )
        if success:
            return jsonify({
                "success": True,
                "message": message,
                "new_balance": result_data.get("new_balance"),
                "redeemed_item": item_name
            })
        return jsonify({"success": False, "error": message}), 400
    except Exception as e:
        logger.error(f"Error redeeming points for {customer_id}: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500

# =============================================================================
# TRANSACTIONS
# =============================================================================

@app.route('/api/transactions', methods=['POST'])
def create_transaction():
    try:
        data = request.get_json()
        customer_id   = data.get('customer_id')
        items         = data.get('items', [])
        channel       = data.get('channel', 'dine-in')
        store_info    = data.get('store', {})
        payment_method = data.get('payment_method', 'cash')

        if not customer_id or not items:
            return jsonify({"success": False, "error": "Missing required fields"}), 400

        success, message, result_data = jollibee_service.create_transaction(
            customer_id, items, channel, store_info, payment_method
        )
        if success:
            return jsonify({"success": True, "message": message, **result_data,
                            "analytics_refresh_needed": True})
        return jsonify({"success": False, "error": message}), 500
    except Exception as e:
        logger.error(f"Error creating transaction: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500

# =============================================================================
# BULK ORDER SIMULATION
# =============================================================================

@app.route('/api/simulate/bulk-orders', methods=['POST'])
def simulate_bulk_orders():
    try:
        data     = request.get_json()
        scenario = data.get('scenario', 'lunch_rush')
        store_id = data.get('store_id', 'store_001')
        start_time = datetime.now()

        scenarios = {
            "lunch_rush": {
                "name": "Lunch Rush (12:00-13:00)",
                "orders": 25,
                "items": [
                    {"name": "1 Pc Chickenjoy Solo",         "price": 82,  "weight": 30},
                    {"name": "Jolly Spaghetti Solo",          "price": 60,  "weight": 25},
                    {"name": "Yumburger Solo",                "price": 40,  "weight": 20},
                    {"name": "6 Pc Chickenjoy Bucket Solo",   "price": 449, "weight": 10},
                    {"name": "Regular Fries",                 "price": 50,  "weight": 15}
                ],
                "channels": ["dine-in", "app"],
                "description": "High-volume lunch period with popular items"
            },
            "family_dinner": {
                "name": "Family Dinner Rush (18:00-20:00)",
                "orders": 15,
                "items": [
                    {"name": "6 Pc Chickenjoy Bucket Solo",  "price": 449, "weight": 40},
                    {"name": "8 Pc Chickenjoy Bucket Solo",  "price": 549, "weight": 20},
                    {"name": "Jolly Spaghetti Solo",         "price": 60,  "weight": 25},
                    {"name": "Peach Mango Pie",              "price": 48,  "weight": 15}
                ],
                "channels": ["dine-in", "delivery"],
                "description": "Family-focused orders with larger portions"
            },
            "weekend_special": {
                "name": "Weekend Special Promotion",
                "orders": 35,
                "items": [
                    {"name": "Cheesy Yumburger Solo",        "price": 69,  "weight": 30},
                    {"name": "Iced Coffee Regular",          "price": 64,  "weight": 25},
                    {"name": "1 Pc Chickenjoy Solo",         "price": 82,  "weight": 20},
                    {"name": "6 Pc Chickenjoy Bucket Solo",  "price": 449, "weight": 15},
                    {"name": "Peach Mango Pie",              "price": 48,  "weight": 10}
                ],
                "channels": ["app", "delivery", "dine-in"],
                "description": "High-volume weekend promotion impact"
            }
        }

        if scenario not in scenarios:
            return jsonify({"success": False, "error": "Invalid scenario"}), 400

        cfg       = scenarios[scenario]
        customers = ["mike001", "zander001", "john001", "melvin001", "carms001"]
        transaction_requests = []
        total_items_sold = {}

        for _ in range(cfg["orders"]):
            weights  = [i["weight"] for i in cfg["items"]]
            selected = random.choices(cfg["items"], weights=weights)[0]
            qty      = random.choices([1, 2], weights=[80, 20])[0]

            transaction_requests.append({
                "customer_id": random.choice(customers),
                "items": [{"name": selected["name"], "price": selected["price"], "quantity": qty}],
                "channel": random.choice(cfg["channels"]),
                "store_info": {"store_id": store_id, "store_name": "Demo Store"},
                "payment_method": "gcash"
            })
            total_items_sold[selected["name"]] = total_items_sold.get(selected["name"], 0) + qty

        bulk_result    = jollibee_service.create_bulk_transactions(transaction_requests)
        processing_time = (datetime.now() - start_time).total_seconds()

        if bulk_result["success"]:
            return jsonify({
                "success": True,
                "scenario": cfg["name"],
                "description": cfg["description"],
                "orders_created": bulk_result["transactions_created"],
                "total_revenue": bulk_result["total_revenue"],
                "items_sold": total_items_sold,
                "store_id": store_id,
                "processing_time_seconds": processing_time,
                "performance_improvement": (
                    f"Processed {bulk_result['transactions_created']} orders "
                    f"in {processing_time:.2f}s using bulk operations"
                ),
                "message": (
                    f"Successfully simulated {bulk_result['transactions_created']} "
                    f"orders for {cfg['name']}"
                ),
                "analytics_refresh_needed": True,
                "processing_mode": "OPTIMIZED_BULK"
            })
        return jsonify({"success": False, "error": bulk_result.get("error", "Bulk processing failed")}), 500

    except Exception as e:
        logger.error(f"Bulk order simulation error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# =============================================================================
# ① MENU SEARCH — Hybrid (BM25 + ELSER RRF)
#    Accepts both POST (old frontend) and GET (new REST style)
# =============================================================================

@app.route('/api/menu/search', methods=['GET', 'POST'])
def search_menu():
    """
    Hybrid BM25 + ELSER search fused server-side via Elasticsearch RRF.

    POST body : { "query": "...", "lat": float, "lon": float, "category": "..." }
    GET params: ?q=...&lat=...&lon=...&category=...
    """
    try:
        if request.method == 'POST':
            body        = request.get_json() or {}
            search_text = body.get('query', '').strip()
            lat         = body.get('lat')
            lon         = body.get('lon')
            category    = body.get('category')
        else:
            search_text = request.args.get('q', '').strip()
            lat         = request.args.get('lat', type=float)
            lon         = request.args.get('lon', type=float)
            category    = request.args.get('category')

        if not search_text:
            return jsonify({"success": False, "error": "Search query required"}), 400

        menu_items = jollibee_service.search_menu(
            search_text, limit=10, lat=lat, lon=lon, category=category
        )
        return jsonify({
            "success": True,
            "query": search_text,
            "search_mode": "hybrid_rrf",
            "geo_filtered": lat is not None,
            "results": menu_items,
            "total_found": len(menu_items)
        })
    except Exception as e:
        logger.error(f"Menu search error: {e}")
        return jsonify({"success": False, "error": "Search failed"}), 500

# =============================================================================
# ⑤ STORE LOCATIONS — feeds the weather panel dropdown
# =============================================================================

@app.route('/api/stores/locations', methods=['GET'])
def get_store_locations():
    """
    Returns lat/lon for every store — used by the weather panel dropdown.
    Tries jollibee-stores index first; falls back to hardcoded Metro Manila
    coordinates if the location geo_point field hasn't been mapped yet.
    """
    try:
        resp = jollibee_service.es_client.aggregation_search(
            Config.INDEX_STORES,
            {
                "query": {"match_all": {}},
                "size": 20,
                "_source": ["store_id", "store_name", "location", "address"]
            }
        )

        stores = []
        for hit in resp.get("hits", {}).get("hits", []):
            s   = hit["_source"]
            loc = s.get("location", {})

            lat = lon = None
            if isinstance(loc, dict):
                lat = loc.get("lat") or loc.get("latitude")
                lon = loc.get("lon") or loc.get("longitude")
            elif isinstance(loc, str) and "," in loc:
                try:
                    parts = loc.split(",")
                    lat, lon = float(parts[0]), float(parts[1])
                except ValueError:
                    pass

            # Fallback: derive coordinates from store name
            if not lat or not lon:
                lat, lon = _demo_store_coords(s.get("store_name", ""))

            stores.append({
                "store_id":   s.get("store_id"),
                "store_name": s.get("store_name"),
                "lat":        lat,
                "lon":        lon,
                "address":    s.get("address", "")
            })

        # If ES returned nothing at all, use static fallback
        if not stores:
            stores = _fallback_store_list()

        return jsonify({"success": True, "stores": stores})

    except Exception as e:
        logger.error(f"Store locations error: {e}")
        # Always return something so the weather panel dropdown works
        return jsonify({"success": True, "stores": _fallback_store_list()})


def _demo_store_coords(store_name: str):
    """Map demo store names to real Metro Manila coordinates."""
    coords = {
        "SM North EDSA":      (14.6565, 121.0322),
        "BGC Central Square": (14.5501, 121.0479),
        "Makati Ayala":       (14.5547, 121.0244),
        "UP Town Center":     (14.6536, 121.0502),
        "MOA Complex":        (14.5353, 120.9822),
    }
    for name, (lat, lon) in coords.items():
        if name.lower() in store_name.lower():
            return lat, lon
    return 14.5995, 120.9842   # Manila centre default


def _fallback_store_list():
    """Static list used when the ES index is empty or unreachable."""
    return [
        {"store_id": "store_001", "store_name": "SM North EDSA",
         "lat": 14.6565, "lon": 121.0322, "address": "SM North EDSA, QC"},
        {"store_id": "store_002", "store_name": "BGC Central Square",
         "lat": 14.5501, "lon": 121.0479, "address": "BGC, Taguig"},
        {"store_id": "store_003", "store_name": "Makati Ayala",
         "lat": 14.5547, "lon": 121.0244, "address": "Ayala Ave, Makati"},
        {"store_id": "store_004", "store_name": "UP Town Center",
         "lat": 14.6536, "lon": 121.0502, "address": "UP Town Center, QC"},
        {"store_id": "store_005", "store_name": "MOA Complex",
         "lat": 14.5353, "lon": 120.9822, "address": "Mall of Asia, Pasay"},
    ]

# =============================================================================
# NEARBY STORES (geo)
# =============================================================================

@app.route('/api/stores/nearby', methods=['GET'])
def stores_nearby():
    """
    ES geo_distance query — stores sorted by distance from customer.
    ?lat=&lon=&distance_km= (default 5 km)
    """
    try:
        lat         = request.args.get("lat", type=float)
        lon         = request.args.get("lon", type=float)
        distance_km = request.args.get("distance_km", 5.0, type=float)

        if lat is None or lon is None:
            return jsonify({"success": False, "error": "lat and lon are required"}), 400

        stores = jollibee_service.es_client.find_nearby_stores(lat, lon, distance_km=distance_km)
        return jsonify({
            "success": True,
            "stores": stores,
            "count": len(stores),
            "searched_radius_km": distance_km,
            "center": {"lat": lat, "lon": lon}
        })
    except Exception as e:
        logger.error(f"Nearby stores error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# =============================================================================
# ANALYTICS
# =============================================================================

@app.route('/api/analytics/stores', methods=['GET'])
def get_store_analytics():
    try:
        return jsonify(jollibee_service.get_store_analytics())
    except Exception as e:
        logger.error(f"Store analytics error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/analytics/inventory', methods=['GET'])
def get_inventory_analytics():
    try:
        store_id = request.args.get('store_id', 'store_001')
        return jsonify(jollibee_service.get_inventory_analytics(store_id))
    except Exception as e:
        logger.error(f"Inventory analytics error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/analytics/customer-segments', methods=['GET'])
def customer_segments():
    try:
        agg_query = {
            "size": 0,
            "aggs": {
                "tiers": {
                    "terms": {"field": "loyalty_profile.tier"},
                    "aggs": {
                        "avg_spending":   {"avg": {"field": "loyalty_profile.annual_spending"}},
                        "avg_frequency":  {"avg": {"field": "purchase_behavior.frequency_score"}},
                        "total_orders":   {"sum": {"field": "purchase_behavior.total_orders"}}
                    }
                }
            }
        }
        results = jollibee_service.es_client.aggregation_search(Config.INDEX_CUSTOMERS, agg_query)

        if results and results.get('aggregations'):
            tier_analysis = [
                {
                    "tier": b['key'],
                    "customer_count": b['doc_count'],
                    "avg_annual_spending": round(b['avg_spending']['value'], 2),
                    "avg_frequency_score": round(b['avg_frequency']['value'], 2),
                    "total_orders": b['total_orders']['value']
                }
                for b in results['aggregations']['tiers']['buckets']
            ]
            return jsonify({"success": True, "tier_analysis": tier_analysis})
        return jsonify({"success": False, "error": "Analytics query failed"}), 500
    except Exception as e:
        logger.error(f"Customer segments error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# =============================================================================
# DEBUG
# =============================================================================

@app.route('/api/debug/inventory', methods=['GET'])
def debug_inventory():
    try:
        count_response = jollibee_service.es_client.aggregation_search(
            Config.INDEX_INVENTORY, {"query": {"match_all": {}}, "size": 0}
        )
        total_count = count_response.get('hits', {}).get('total', {}).get('value', 0) if count_response else 0

        sample_response = jollibee_service.es_client.aggregation_search(
            Config.INDEX_INVENTORY, {"query": {"match_all": {}}, "size": 10}
        )
        sample_items   = []
        store_breakdown = {}

        if sample_response and sample_response.get('hits', {}).get('hits'):
            for hit in sample_response['hits']['hits']:
                item = hit['_source']
                sample_items.append({
                    "id": hit['_id'],
                    "store_id": item.get('store_id'),
                    "item_name": item.get('item_name'),
                    "current_stock": item.get('current_stock'),
                    "status": item.get('status')
                })
                store_breakdown[item.get('store_id', 'unknown')] = \
                    store_breakdown.get(item.get('store_id', 'unknown'), 0) + 1

        agg_response = jollibee_service.es_client.aggregation_search(
            Config.INDEX_INVENTORY,
            {"size": 0, "aggs": {"stores": {"terms": {"field": "store_id", "size": 10}}}}
        )
        store_counts = {}
        if agg_response and agg_response.get('aggregations'):
            for bucket in agg_response['aggregations']['stores']['buckets']:
                store_counts[bucket['key']] = bucket['doc_count']

        return jsonify({
            "success": True,
            "total_inventory_items": total_count,
            "sample_items": sample_items,
            "store_breakdown": store_breakdown,
            "detailed_store_counts": store_counts
        })
    except Exception as e:
        logger.error(f"Inventory debug error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# =============================================================================
# ERROR HANDLERS
# =============================================================================

@app.errorhandler(404)
def not_found(error):
    return jsonify({"success": False, "error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({"success": False, "error": "Internal server error"}), 500

# =============================================================================
# STARTUP
# =============================================================================

def main():
    try:
        Config.validate()

        health = jollibee_service.es_client.health_check()
        if health["status"] != "healthy":
            logger.error("Elasticsearch connection failed!")
            return

        ssl_context = None
        cert_file = os.getenv('SSL_CERT', 'cert.pem')
        key_file  = os.getenv('SSL_KEY',  'key.pem')
        if os.path.exists(cert_file) and os.path.exists(key_file):
            ssl_context = (cert_file, key_file)
            logger.info(f"🔒 SSL enabled — using {cert_file} + {key_file}")
        else:
            logger.warning("⚠️  No certs found — running plain HTTP")

        protocol = "https" if ssl_context else "http"
        logger.info(f"🚀 Starting {Config.APP_NAME} v{Config.APP_VERSION}")
        logger.info("=" * 70)
        logger.info(f"🌐 Platform: {protocol}://{Config.FLASK_HOST}:{Config.FLASK_PORT}")
        logger.info(f"📋 Demo Guide: {protocol}://{Config.FLASK_HOST}:{Config.FLASK_PORT}/demo")
        logger.info(f"🔧 Debug: {protocol}://{Config.FLASK_HOST}:{Config.FLASK_PORT}/api/debug/inventory")
        logger.info("=" * 70)
        logger.info("✨ Features:")
        logger.info("  • Hybrid Search — BM25 + ELSER via Elasticsearch RRF")
        logger.info("  • AI recs from order history  → GET /api/customers/<id>/recommendations")
        logger.info("  • AI recs from weather + geo  → GET /api/customers/<id>/recommendations/weather?lat=&lon=")
        logger.info("  • AI upsize suggestions       → POST /api/customers/<id>/upsize")
        logger.info("  • Store locations (weather UI) → GET /api/stores/locations")
        logger.info("  • Nearby stores (geo)         → GET /api/stores/nearby?lat=&lon=")
        logger.info("  • Real-time analytics + inventory")
        logger.info("=" * 70)

        app.run(
            debug=Config.FLASK_DEBUG,
            host=Config.FLASK_HOST,
            port=Config.FLASK_PORT,
            ssl_context=ssl_context,
            use_reloader=False
        )

    except Exception as e:
        logger.error(f"Failed to start application: {e}")
        raise

if __name__ == '__main__':
    main()
