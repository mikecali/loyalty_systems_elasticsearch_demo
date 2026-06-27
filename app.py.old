#!/usr/bin/env python3
"""
Optimized Jollibee BeeLoyalty System - Main Flask Application
Performance optimized version with bulk operations
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

# Configure logging
logging.basicConfig(level=getattr(logging, Config.LOG_LEVEL), format=Config.LOG_FORMAT)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Initialize Jollibee service
jollibee_service = JollibeeService()

# =============================================================================
# WEB INTERFACE ROUTES
# =============================================================================

@app.route('/')
def dashboard():
    """Serve the main dashboard"""
    return render_template_string(DASHBOARD_HTML)

@app.route('/demo')
def demo_page():
    """Serve the demo guide page"""
    return render_template_string(DEMO_HTML)

# =============================================================================
# API ROUTES
# =============================================================================

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        health_data = jollibee_service.es_client.health_check()
        return jsonify(health_data)
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return jsonify({"status": "unhealthy", "error": str(e), "timestamp": datetime.now().isoformat()}), 503

@app.route('/api/customers/<customer_id>', methods=['GET'])
def get_customer(customer_id):
    """Get customer profile by ID"""
    try:
        customer_data = jollibee_service.get_customer(customer_id)
        if customer_data:
            return jsonify({"success": True, "customer": customer_data})
        else:
            return jsonify({"success": False, "error": "Customer not found"}), 404
    except Exception as e:
        logger.error(f"Error retrieving customer {customer_id}: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error"}), 500

@app.route('/api/customers/<customer_id>/recommendations', methods=['GET'])
def get_recommendations(customer_id):
    """Get personalized menu recommendations"""
    try:
        customer = jollibee_service.get_customer(customer_id)
        if not customer:
            return jsonify({"success": False, "error": "Customer not found"}), 404

        recommendations = jollibee_service.get_customer_recommendations(customer_id)
        return jsonify({
            "success": True,
            "customer_name": customer['personal_info']['name'],
            "recommendations": recommendations
        })
    except Exception as e:
        logger.error(f"Error getting recommendations for {customer_id}: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error"}), 500

@app.route('/api/customers/<customer_id>/redeem', methods=['POST'])
def redeem_points(customer_id):
    """Redeem points for rewards"""
    try:
        data = request.get_json()
        points_to_redeem = data.get('points', 0)
        item_name = data.get('item_name', '')

        if points_to_redeem <= 0:
            return jsonify({"success": False, "error": "Invalid points amount"}), 400

        success, message, result_data = jollibee_service.redeem_points(customer_id, points_to_redeem, item_name)

        if success:
            return jsonify({
                "success": True,
                "message": message,
                "new_balance": result_data.get("new_balance"),
                "redeemed_item": item_name
            })
        else:
            return jsonify({"success": False, "error": message}), 400
    except Exception as e:
        logger.error(f"Error redeeming points for {customer_id}: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error"}), 500

@app.route('/api/transactions', methods=['POST'])
def create_transaction():
    """Create new transaction and update connected analytics - OPTIMIZED"""
    try:
        data = request.get_json()

        customer_id = data.get('customer_id')
        items = data.get('items', [])
        channel = data.get('channel', 'dine-in')
        store_info = data.get('store', {})
        payment_method = data.get('payment_method', 'cash')

        if not customer_id or not items:
            return jsonify({"success": False, "error": "Missing required fields"}), 400

        success, message, result_data = jollibee_service.create_transaction(
            customer_id, items, channel, store_info, payment_method
        )

        if success:
            return jsonify({
                "success": True,
                "message": message,
                **result_data,
                "analytics_refresh_needed": True
            })
        else:
            return jsonify({"success": False, "error": message}), 500

    except Exception as e:
        logger.error(f"Error creating transaction: {str(e)}")
        return jsonify({"success": False, "error": "Internal server error"}), 500

@app.route('/api/simulate/bulk-orders', methods=['POST'])
def simulate_bulk_orders():
    """OPTIMIZED: Simulate bulk orders using batch processing"""
    try:
        data = request.get_json()
        scenario = data.get('scenario', 'lunch_rush')
        store_id = data.get('store_id', 'store_001')

        start_time = datetime.now()
        logger.info(f"Starting optimized bulk simulation: {scenario} at {store_id}")

        scenarios = {
            "lunch_rush": {
                "name": "Lunch Rush (12:00-13:00)",
                "orders": 25,
                "items": [
                    {"name": "1 Pc Chickenjoy Solo", "price": 82, "weight": 30},
                    {"name": "Jolly Spaghetti Solo", "price": 60, "weight": 25},
                    {"name": "Yumburger Solo", "price": 40, "weight": 20},
                    {"name": "6 Pc Chickenjoy Bucket Solo", "price": 449, "weight": 10},
                    {"name": "Regular Fries", "price": 50, "weight": 15}
                ],
                "channels": ["dine-in", "app"],
                "description": "High-volume lunch period with popular items"
            },
            "family_dinner": {
                "name": "Family Dinner Rush (18:00-20:00)",
                "orders": 15,
                "items": [
                    {"name": "6 Pc Chickenjoy Bucket Solo", "price": 449, "weight": 40},
                    {"name": "8 Pc Chickenjoy Bucket Solo", "price": 549, "weight": 20},
                    {"name": "Jolly Spaghetti Solo", "price": 60, "weight": 25},
                    {"name": "Peach Mango Pie", "price": 48, "weight": 15}
                ],
                "channels": ["dine-in", "delivery"],
                "description": "Family-focused orders with larger portions"
            },
            "weekend_special": {
                "name": "Weekend Special Promotion",
                "orders": 35,
                "items": [
                    {"name": "Cheesy Yumburger Solo", "price": 69, "weight": 30},
                    {"name": "Iced Coffee Regular", "price": 64, "weight": 25},
                    {"name": "1 Pc Chickenjoy Solo", "price": 82, "weight": 20},
                    {"name": "6 Pc Chickenjoy Bucket Solo", "price": 449, "weight": 15},
                    {"name": "Peach Mango Pie", "price": 48, "weight": 10}
                ],
                "channels": ["app", "delivery", "dine-in"],
                "description": "High-volume weekend promotion impact"
            }
        }

        if scenario not in scenarios:
            return jsonify({"success": False, "error": "Invalid scenario"}), 400

        scenario_config = scenarios[scenario]
        customers = ["mike001", "zander001", "john001", "melvin001", "carms001"]

        transaction_requests = []
        total_items_sold = {}

        for i in range(scenario_config["orders"]):
            items = scenario_config["items"]
            weights = [item["weight"] for item in items]
            selected_item = random.choices(items, weights=weights)[0]
            quantity = random.choices([1, 2], weights=[80, 20])[0]
            customer_id = random.choice(customers)
            channel = random.choice(scenario_config["channels"])

            order_items = [{
                "name": selected_item["name"],
                "price": selected_item["price"],
                "quantity": quantity
            }]

            transaction_requests.append({
                "customer_id": customer_id,
                "items": order_items,
                "channel": channel,
                "store_info": {"store_id": store_id, "store_name": "Demo Store"},
                "payment_method": "gcash"
            })

            item_name = selected_item["name"]
            if item_name not in total_items_sold:
                total_items_sold[item_name] = 0
            total_items_sold[item_name] += quantity

        bulk_result = jollibee_service.create_bulk_transactions(transaction_requests)
        processing_time = (datetime.now() - start_time).total_seconds()

        if bulk_result["success"]:
            logger.info(f"Bulk simulation completed in {processing_time:.2f}s: {bulk_result['transactions_created']} orders, ₱{bulk_result['total_revenue']}")
            return jsonify({
                "success": True,
                "scenario": scenario_config["name"],
                "description": scenario_config["description"],
                "orders_created": bulk_result["transactions_created"],
                "total_revenue": bulk_result["total_revenue"],
                "items_sold": total_items_sold,
                "store_id": store_id,
                "processing_time_seconds": processing_time,
                "performance_improvement": f"Processed {bulk_result['transactions_created']} orders in {processing_time:.2f}s using bulk operations",
                "message": f"Successfully simulated {bulk_result['transactions_created']} orders for {scenario_config['name']}",
                "analytics_refresh_needed": True,
                "processing_mode": "OPTIMIZED_BULK"
            })
        else:
            return jsonify({"success": False, "error": bulk_result.get("error", "Bulk processing failed")}), 500

    except Exception as e:
        logger.error(f"Optimized bulk order simulation error: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/menu/search', methods=['POST'])
def search_menu():
    """Semantic search through menu items"""
    try:
        data = request.get_json()
        search_text = data.get('query', '')

        if not search_text:
            return jsonify({"success": False, "error": "Search query required"}), 400

        menu_items = jollibee_service.search_menu(search_text)
        return jsonify({
            "success": True,
            "query": search_text,
            "results": menu_items,
            "total_found": len(menu_items)
        })
    except Exception as e:
        logger.error(f"Menu search error: {str(e)}")
        return jsonify({"success": False, "error": "Search failed"}), 500

@app.route('/api/analytics/stores', methods=['GET'])
def get_store_analytics():
    """Get store location and performance analytics with real-time data"""
    try:
        analytics_data = jollibee_service.get_store_analytics()
        return jsonify(analytics_data)
    except Exception as e:
        logger.error(f"Store analytics error: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/analytics/inventory', methods=['GET'])
def get_inventory_analytics():
    """Get real-time inventory analytics"""
    try:
        store_id = request.args.get('store_id', 'store_001')
        analytics_data = jollibee_service.get_inventory_analytics(store_id)
        return jsonify(analytics_data)
    except Exception as e:
        logger.error(f"Inventory analytics error: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/analytics/customer-segments', methods=['GET'])
def customer_segments():
    """Get customer segmentation analytics"""
    try:
        agg_query = {
            "size": 0,
            "aggs": {
                "tiers": {
                    "terms": {"field": "loyalty_profile.tier"},
                    "aggs": {
                        "avg_spending": {"avg": {"field": "loyalty_profile.annual_spending"}},
                        "avg_frequency": {"avg": {"field": "purchase_behavior.frequency_score"}},
                        "total_orders": {"sum": {"field": "purchase_behavior.total_orders"}}
                    }
                }
            }
        }

        results = jollibee_service.es_client.aggregation_search(Config.INDEX_CUSTOMERS, agg_query)

        if results and results.get('aggregations'):
            tier_analysis = []
            for bucket in results['aggregations']['tiers']['buckets']:
                tier_analysis.append({
                    "tier": bucket['key'],
                    "customer_count": bucket['doc_count'],
                    "avg_annual_spending": round(bucket['avg_spending']['value'], 2),
                    "avg_frequency_score": round(bucket['avg_frequency']['value'], 2),
                    "total_orders": bucket['total_orders']['value']
                })
            return jsonify({"success": True, "tier_analysis": tier_analysis})
        else:
            return jsonify({"success": False, "error": "Analytics query failed"}), 500

    except Exception as e:
        logger.error(f"Customer segments error: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/debug/inventory', methods=['GET'])
def debug_inventory():
    """Debug endpoint to check inventory data"""
    try:
        count_query = {"query": {"match_all": {}}, "size": 0}
        count_response = jollibee_service.es_client.aggregation_search(Config.INDEX_INVENTORY, count_query)
        total_count = count_response.get('hits', {}).get('total', {}).get('value', 0) if count_response else 0

        sample_query = {"query": {"match_all": {}}, "size": 10}
        sample_response = jollibee_service.es_client.aggregation_search(Config.INDEX_INVENTORY, sample_query)

        sample_items = []
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
                store_id = item.get('store_id', 'unknown')
                store_breakdown[store_id] = store_breakdown.get(store_id, 0) + 1

        agg_query = {"size": 0, "aggs": {"stores": {"terms": {"field": "store_id", "size": 10}}}}
        agg_response = jollibee_service.es_client.aggregation_search(Config.INDEX_INVENTORY, agg_query)
        store_counts = {}
        if agg_response and agg_response.get('aggregations'):
            for bucket in agg_response['aggregations']['stores']['buckets']:
                store_counts[bucket['key']] = bucket['doc_count']

        return jsonify({
            "success": True,
            "total_inventory_items": total_count,
            "sample_items": sample_items,
            "store_breakdown": store_breakdown,
            "detailed_store_counts": store_counts,
            "debug_note": "This endpoint helps diagnose inventory data issues"
        })
    except Exception as e:
        logger.error(f"Inventory debug error: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

# =============================================================================
# ERROR HANDLERS
# =============================================================================

@app.errorhandler(404)
def not_found(error):
    return jsonify({"success": False, "error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {str(error)}")
    return jsonify({"success": False, "error": "Internal server error"}), 500

# =============================================================================
# APPLICATION STARTUP — SSL added
# =============================================================================

def main():
    """Main application entry point"""
    try:
        Config.validate()

        health = jollibee_service.es_client.health_check()
        if health["status"] != "healthy":
            logger.error("Elasticsearch connection failed!")
            return

        # SSL context — auto-detect cert.pem / key.pem
        ssl_context = None
        cert_file = os.getenv('SSL_CERT', 'cert.pem')
        key_file  = os.getenv('SSL_KEY',  'key.pem')
        if os.path.exists(cert_file) and os.path.exists(key_file):
            ssl_context = (cert_file, key_file)
            logger.info(f"🔒 SSL enabled — using {cert_file} + {key_file}")
        else:
            logger.warning("⚠️  No certs found — running plain HTTP")

        protocol = "https" if ssl_context else "http"
        logger.info(f"🚀 Starting OPTIMIZED {Config.APP_NAME} v{Config.APP_VERSION}")
        logger.info("=" * 70)
        logger.info(f"🌐 Platform: {protocol}://{Config.FLASK_HOST}:{Config.FLASK_PORT}")
        logger.info(f"📋 Demo Guide: {protocol}://{Config.FLASK_HOST}:{Config.FLASK_PORT}/demo")
        logger.info(f"🔧 Debug: {protocol}://{Config.FLASK_HOST}:{Config.FLASK_PORT}/api/debug/inventory")
        logger.info("=" * 70)
        logger.info("⚡ PERFORMANCE OPTIMIZATIONS:")
        logger.info("  • Bulk transaction processing")
        logger.info("  • Reduced Elasticsearch round trips")
        logger.info("  • Optimized inventory queries")
        logger.info("  • Batch customer updates")
        logger.info("=" * 70)
        logger.info("✨ Features:")
        logger.info("  • ELSER-powered semantic search")
        logger.info("  • Real-time connected analytics")
        logger.info("  • AI-driven recommendations")
        logger.info("  • Multilingual search support")
        logger.info("  • Live inventory management")
        logger.info("=" * 70)

        app.run(
            debug=Config.FLASK_DEBUG,
            host=Config.FLASK_HOST,
            port=Config.FLASK_PORT,
            ssl_context=ssl_context,
            use_reloader=False
        )

    except Exception as e:
        logger.error(f"Failed to start application: {str(e)}")
        raise

if __name__ == '__main__':
    main()
