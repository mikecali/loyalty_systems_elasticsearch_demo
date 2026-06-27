"""
weather_debug.py — run this on the server to isolate which step is failing.

Usage:
    python weather_debug.py

Tests in order:
  1. Open-Meteo reachable from this server
  2. ES inference endpoint reachable and returning valid JSON
  3. JSON parse of Claude's weather response
"""

import json
import sys
import requests
from config import Config

Config.validate()

ES_ENDPOINT = Config.ELASTICSEARCH_ENDPOINT.rstrip('/')
ES_HEADERS  = {
    "Authorization": f"ApiKey {Config.ELASTICSEARCH_API_KEY}",
    "Content-Type":  "application/json"
}
INFERENCE_ID = Config.CLAUDE_INFERENCE_ID

# ── Test coordinates: Manila ──────────────────────────────────────────────────
LAT, LON = 14.5995, 120.9842

print("=" * 60)
print("Step 1 — Open-Meteo reachability")
print("=" * 60)
try:
    url  = (f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={LAT}&longitude={LON}&current_weather=true&timezone=auto")
    resp = requests.get(url, timeout=8)
    print(f"  Status : {resp.status_code}")
    if resp.status_code == 200:
        cw = resp.json().get("current_weather", {})
        print(f"  Temp   : {cw.get('temperature')}°C")
        print(f"  WMO    : {cw.get('weathercode')}")
        print("  ✅ Open-Meteo OK")
        weather_ok = True
        temp      = cw.get("temperature", 28)
        condition = "sunny" if cw.get("weathercode", 0) == 0 else "rainy"
    else:
        print(f"  ❌ Unexpected status: {resp.text[:200]}")
        weather_ok = False
        temp, condition = 28, "sunny"
except Exception as e:
    print(f"  ❌ FAILED: {e}")
    print("     → Server may not have outbound internet access to api.open-meteo.com")
    print("     → Weather will fall back to defaults (28°C, sunny)")
    weather_ok = False
    temp, condition = 28, "sunny"

print()
print("=" * 60)
print("Step 2 — ES inference endpoint")
print("=" * 60)
prompt = (
    "You are a food recommendation engine for Jollibee Philippines. "
    "Given current weather, output ONLY valid JSON with two keys:\n"
    '  "query": a 5-8 word menu search phrase\n'
    '  "reason": one sentence max 20 words explaining why\n'
    "No markdown, no extra text.\n\n"
    f"Temperature: {temp}°C, Condition: {condition}"
)

url  = f"{ES_ENDPOINT}/_inference/completion/{INFERENCE_ID}"
body = {"input": prompt}

try:
    resp = requests.post(url, headers=ES_HEADERS, json=body, timeout=30)
    print(f"  Status : {resp.status_code}")
    print(f"  Raw    : {resp.text[:400]}")
    if resp.status_code == 200:
        data = resp.json()
        raw  = data.get("completion", [{}])[0].get("result", "").strip()
        print(f"  Result : {raw!r}")
        print("  ✅ ES inference OK")
        inference_ok = True
    else:
        print(f"  ❌ ES inference error")
        inference_ok = False
        raw = ""
except Exception as e:
    print(f"  ❌ FAILED: {e}")
    inference_ok = False
    raw = ""

print()
print("=" * 60)
print("Step 3 — JSON parse of Claude response")
print("=" * 60)
if raw:
    # Strip markdown fences if Claude wrapped the JSON
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines   = cleaned.split("\n")
        cleaned = "\n".join(
            l for l in lines
            if not l.strip().startswith("```")
        ).strip()
    try:
        parsed = json.loads(cleaned)
        print(f"  query  : {parsed.get('query')}")
        print(f"  reason : {parsed.get('reason')}")
        print("  ✅ JSON parse OK")
    except json.JSONDecodeError as e:
        print(f"  ❌ JSON parse FAILED: {e}")
        print(f"     Cleaned text: {cleaned!r}")
        print("     → Claude returned prose instead of JSON")
        print("     → The fix is already in jollibee_service.py (strips fences)")
else:
    print("  ⏭  Skipped (no inference output to parse)")

print()
print("=" * 60)
print("Summary")
print("=" * 60)
print(f"  Open-Meteo : {'✅' if weather_ok else '❌ (fallback active)'}")
print(f"  ES inference: {'✅' if inference_ok else '❌'}")
print()
if not weather_ok:
    print("Fix: If the server has no outbound internet, set USE_LIVE_WEATHER=false")
    print("     in .env and weather will use Manila defaults.")
if not inference_ok:
    print("Fix: Check ELASTICSEARCH_API_KEY has inference_user role,")
    print("     and that CLAUDE_INFERENCE_ID matches the endpoint name exactly.")
