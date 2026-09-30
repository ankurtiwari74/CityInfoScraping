"""Print a per-field fill-rate report for output/properties.jsonl."""
import json
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "output/properties.jsonl"

FIELDS = [
    "title", "location", "operator", "type", "available_seats", "address",
    "total_seats", "occupancy_certificate", "fire_noc", "parking_facility",
    "total_built_up_area", "area_available", "possession_status",
    "no_of_floors", "per_floor_area", "year_of_completion",
    "about_project", "floor_plan", "amenities", "location_map", "property_url",
]

items = [json.loads(line) for line in open(PATH) if line.strip()]
n = len(items)
print(f"Total items: {n}")
if n == 0:
    sys.exit(0)

print(f"{'field':24s} {'non-null':>10s} {'fill %':>8s}")
for f in FIELDS:
    if f in ("amenities", "floor_plan"):
        non_null = sum(1 for it in items if it.get(f))
    else:
        non_null = sum(1 for it in items if it.get(f) not in (None, ""))
    pct = 100.0 * non_null / n
    flag = "  <-- under 80%" if pct < 80 else ""
    print(f"{f:24s} {non_null:10d} {pct:7.1f}%{flag}")

urls = [it.get("property_url") for it in items]
dupes = len(urls) - len(set(urls))
print(f"\nDuplicate property_url count: {dupes}")
