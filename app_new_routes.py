"""
New API routes to add to app.py for the 4 modernized features.
Paste these into your existing app.py Flask application.
"""

# ─── Add to existing imports ──────────────────────────────────────────────────
# from flask import Flask, request, jsonify, render_template
# from jollibee_service import JollibeeService
# service = JollibeeService()

# ─────────────────────────────────────────────────────────────────────────────
# ① HYBRID SEARCH — replaces /api/menu/search
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/menu/search", methods=["GET"])
def search_menu():
    """
    Hybrid BM25 + ELSER search.

    Query params:
      q        — search text (required)
      lat, lon — customer coordinates (optional, enables geo filter)
      category — category filter (optional)
      limit    — result count (default 10)

    What ES does: RRF fusion of keyword + semantic ranking.
    What app does: passes params, formats response.
    """
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Query parameter 'q' is required"}), 400

    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    category = request.args.get("category")
    limit = request.args.get("limit", 10, type=int)

    results = service.search_menu(query, limit=limit, lat=lat, lon=lon, category=category)
    return jsonify({
        "query": query,
        "search_mode": "hybrid_rrf",        # BM25 + ELSER via Elasticsearch RRF
        "geo_filtered": lat is not None,
        "results": results,
        "count": len(results)
    })


# ─────────────────────────────────────────────────────────────────────────────
# ② AI RECOMMENDATIONS — Order History
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/recommendations/history/<customer_id>", methods=["GET"])
def recommendations_history(customer_id):
    """
    Personalised recommendations from order history.

    Query params:
      lat, lon — customer location (optional, boosts geo-popular items)

    What ES does:
      • Retrieves & aggregates customer's transaction history
      • Runs hybrid search on derived preference signal
      • Optionally finds popular items near lat/lon

    What Claude does:
      • Generates a one-sentence personalised insight
    """
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)

    result = service.get_history_based_recommendations(customer_id, limit=6, lat=lat, lon=lon)
    if not result.get("recommendations"):
        return jsonify({"error": "No recommendations available"}), 404

    return jsonify(result)


# ─────────────────────────────────────────────────────────────────────────────
# ③ AI RECOMMENDATIONS — Weather
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/recommendations/weather/<customer_id>", methods=["GET"])
def recommendations_weather(customer_id):
    """
    Weather-aware recommendations using customer's lat/lon.

    Query params:
      lat, lon — REQUIRED for weather lookup and nearest-store geo query

    What ES does:
      • geo_distance query to find nearest stores
      • Hybrid search using Claude-generated weather-appropriate query
    What Open-Meteo does: returns live weather (no API key needed).
    What Claude does: maps weather → food mood + search phrase.
    """
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)

    if lat is None or lon is None:
        return jsonify({"error": "lat and lon are required for weather recommendations"}), 400

    result = service.get_weather_based_recommendations(customer_id, lat=lat, lon=lon, limit=6)
    return jsonify(result)


# ─────────────────────────────────────────────────────────────────────────────
# ④ AI UPSIZE SUGGESTIONS — Pre-checkout
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/recommendations/upsize/<customer_id>", methods=["POST"])
def recommendations_upsize(customer_id):
    """
    Upsize suggestions based on current cart + historical orders.

    POST body (JSON):
      { "cart": [ {"name": "...", "price": 99, "quantity": 1}, ... ] }

    What ES does:
      • Retrieves customer transaction history and average order value
      • Hybrid search for "large family bucket upsize" variants
    What Claude does:
      • Maps cart items → upgrade opportunities with personalised pitch
    """
    body = request.get_json(silent=True) or {}
    cart = body.get("cart", [])
    if not cart:
        return jsonify({"error": "cart is required in request body"}), 400

    result = service.get_upsize_suggestions(customer_id, current_cart=cart)
    return jsonify(result)


# ─────────────────────────────────────────────────────────────────────────────
# ⑤ NEARBY STORES (supporting endpoint)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/stores/nearby", methods=["GET"])
def stores_nearby():
    """
    ES geo_distance query — returns stores sorted by distance from customer.

    Query params:
      lat, lon         — required
      distance_km      — search radius (default 5)
    """
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    distance_km = request.args.get("distance_km", 5.0, type=float)

    if lat is None or lon is None:
        return jsonify({"error": "lat and lon are required"}), 400

    stores = service.es_client.find_nearby_stores(lat, lon, distance_km=distance_km)
    return jsonify({
        "stores": stores,
        "count": len(stores),
        "searched_radius_km": distance_km,
        "center": {"lat": lat, "lon": lon}
    })
