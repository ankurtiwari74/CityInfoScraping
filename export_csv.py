"""Export output/properties.jsonl to output/properties.csv."""
import csv
import json
import sys

FIELDS = [
    "title", "location", "operator", "type", "available_seats", "address",
    "total_seats", "occupancy_certificate", "fire_noc", "parking_facility",
    "total_built_up_area", "area_available", "possession_status",
    "no_of_floors", "per_floor_area", "year_of_completion",
    "about_project", "floor_plan", "amenities", "location_map", "property_url",
    "scraped_at", "source_page",
]

IN_PATH = sys.argv[1] if len(sys.argv) > 1 else "output/properties.jsonl"
OUT_PATH = sys.argv[2] if len(sys.argv) > 2 else "output/properties.csv"

with open(IN_PATH, encoding="utf-8") as fin, open(OUT_PATH, "w", newline="", encoding="utf-8") as fout:
    writer = csv.DictWriter(fout, fieldnames=FIELDS, extrasaction="ignore")
    writer.writeheader()
    count = 0
    for line in fin:
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        item["amenities"] = "; ".join(item.get("amenities") or [])
        item["floor_plan"] = "; ".join(item.get("floor_plan") or [])
        writer.writerow(item)
        count += 1

print(f"Wrote {count} rows to {OUT_PATH}")
