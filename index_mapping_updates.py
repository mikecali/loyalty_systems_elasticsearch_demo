"""
ES Index Mapping Updates — add to setup/menu_setup.py and setup/customer_setup.py

Key additions:
  • jollibee-stores: 'location' field → geo_point
  • jollibee-menu:   'location' field → geo_point (for branch-specific menus)
  • jollibee-menu:   'searchable_text' field kept for ELSER pipeline

Run after updating: python setup_all.py
"""

# ── jollibee-stores mapping fragment ─────────────────────────────────────────
STORES_MAPPING_UPDATE = {
    "mappings": {
        "properties": {
            "store_id":        {"type": "keyword"},
            "store_name":      {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "address":         {"type": "text"},
            # ▼ NEW: geo_point enables geo_distance queries and sort-by-distance
            "location": {
                "type": "geo_point"
                # Store format: { "lat": 14.5995, "lon": 120.9842 }
            },
            "store_type":      {"type": "keyword"},
            "operating_hours": {"type": "text"},
            "phone":           {"type": "keyword"},
            "is_active":       {"type": "boolean"}
        }
    }
}

# ── jollibee-menu mapping fragment ───────────────────────────────────────────
MENU_MAPPING_UPDATE = {
    "settings": {
        "default_pipeline": "jollibee-elser-pipeline"   # ELSER enrichment on ingest
    },
    "mappings": {
        "properties": {
            "item_id":          {"type": "keyword"},
            "name": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword"}}   # needed for term aggs
            },
            "category": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword"}}   # needed for category_filter
            },
            "description":      {"type": "text"},
            "price":            {"type": "float"},
            "points_value":     {"type": "integer"},
            "is_new":           {"type": "boolean"},
            "is_bestseller":    {"type": "boolean"},
            # ELSER sparse vector — populated by ingest pipeline
            "searchable_text":  {"type": "text"},
            "ml": {
                "properties": {
                    "tokens": {"type": "rank_features"}
                }
            },
            # ▼ Optional: geo_point if items are branch-specific
            "location": {
                "type": "geo_point"
            }
        }
    }
}

# ── Store data format ─────────────────────────────────────────────────────────
# When ingesting store documents, include location as:
EXAMPLE_STORE_DOC = {
    "store_id": "store_001",
    "store_name": "Jollibee SM Mall of Asia",
    "address": "SM Mall of Asia, Pasay City, Metro Manila",
    "location": {           # ← geo_point
        "lat": 14.5353,
        "lon": 120.9822
    },
    "store_type": "mall",
    "operating_hours": "10:00 AM - 10:00 PM",
    "is_active": True
}

# ── How geo_distance + sort works in ES ──────────────────────────────────────
EXAMPLE_GEO_QUERY = {
    "query": {
        "geo_distance": {
            "distance": "5km",
            "location": {"lat": 14.5995, "lon": 120.9842}   # customer lat/lon
        }
    },
    "sort": [
        {
            "_geo_distance": {
                "location": {"lat": 14.5995, "lon": 120.9842},
                "order": "asc",
                "unit": "km"
            }
        }
    ]
}
# ES returns docs sorted nearest-first; hit.sort[0] = distance in km.
# No Haversine math needed in Python.
