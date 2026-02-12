#!/usr/bin/env python3
"""Create a small fake dataset (GeoJSON + summary.json) for the static map demo.

This avoids publishing private trip history while keeping the UI functional.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path


CITIES = [
    ("Berlin", 52.5200, 13.4050),
    ("London", 51.5072, -0.1276),
    ("Lisbon", 38.7223, -9.1393),
    ("Marrakesh", 31.6295, -7.9811),
    ("Reykjavík", 64.1466, -21.9426),
    ("Tokyo", 35.6762, 139.6503),
    ("Taipei", 25.0330, 121.5654),
    ("San Francisco", 37.7749, -122.4194),
]

TYPES = ["Flight", "Train", "Hotel"]


def dt_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def make(out_dir: Path, seed: int = 42, trips: int = 8) -> None:
    random.seed(seed)
    out_dir.mkdir(parents=True, exist_ok=True)

    now = datetime(2024, 1, 1, tzinfo=timezone.utc)

    features = []
    trip_groups = []

    for i in range(trips):
        trip_name = f"Demo Trip {i+1}"
        trip_group_key = f"demo-{i+1:02d}"

        start = now + timedelta(days=i * 35)
        end = start + timedelta(days=random.randint(3, 12))
        year = start.year

        # pick 2-4 legs
        legs = random.randint(2, 4)
        events = []

        # route city sequence
        cities = random.sample(CITIES, k=legs + 1)

        # transport events
        seg_count = 0
        for s in range(legs):
            from_city, from_lat, from_lon = cities[s]
            to_city, to_lat, to_lon = cities[s + 1]
            ttype = random.choice(["Flight", "Train"])

            dep = start + timedelta(hours=6 + s * 10)
            arr = dep + timedelta(hours=random.randint(2, 8))

            seg_count += 1
            trip_key = f"{trip_group_key}:seg-{s+1}"
            title = f"{from_city} → {to_city}"

            features.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[from_lon, from_lat], [to_lon, to_lat]]},
                "properties": {
                    "tripGroupKey": trip_group_key,
                    "tripName": trip_name,
                    "tripKey": trip_key,
                    "type": ttype,
                    "year": year,
                    "fromLabel": from_city,
                    "toLabel": to_city,
                    "departure": dt_iso(dep),
                    "arrival": dt_iso(arr),
                },
            })
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [from_lon, from_lat]},
                "properties": {"tripGroupKey": trip_group_key, "tripName": trip_name, "tripKey": trip_key, "type": ttype, "year": year, "label": from_city},
            })
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [to_lon, to_lat]},
                "properties": {"tripGroupKey": trip_group_key, "tripName": trip_name, "tripKey": trip_key, "type": ttype, "year": year, "label": to_city},
            })

            events.append({
                "tripGroupKey": trip_group_key,
                "tripName": trip_name,
                "tripKey": trip_key,
                "title": title,
                "type": ttype,
                "year": year,
                "start": dt_iso(dep),
                "end": dt_iso(arr),
                "segmentCount": 1,
            })

        # one hotel block (fake)
        hotel_key = f"{trip_group_key}:hotel"
        hotel_city, _, _ = cities[-1]
        hotel_start = start + timedelta(days=1)
        hotel_end = end - timedelta(days=1)
        events.append({
            "tripGroupKey": trip_group_key,
            "tripName": trip_name,
            "tripKey": hotel_key,
            "title": f"Hotel in {hotel_city}",
            "type": "Hotel",
            "year": year,
            "start": dt_iso(hotel_start),
            "end": dt_iso(hotel_end),
            "segmentCount": 0,
        })

        trip_groups.append({
            "tripGroupKey": trip_group_key,
            "tripName": trip_name,
            "year": year,
            "start": dt_iso(start),
            "end": dt_iso(end),
            "events": sorted(events, key=lambda e: e.get("start") or ""),
        })

    geo = {"type": "FeatureCollection", "features": features}
    (out_dir / "trips.geojson").write_text(json.dumps(geo, ensure_ascii=False), encoding="utf-8")

    summary = {
        "sourceFiles": 0,
        "segmentCount": len([f for f in features if f["geometry"]["type"] == "LineString"]),
        "tripGroups": sorted(trip_groups, key=lambda g: g.get("start") or ""),
        "events": [e for g in trip_groups for e in g["events"]],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--trips", type=int, default=8)
    args = ap.parse_args()

    make(Path(args.out), seed=args.seed, trips=args.trips)


if __name__ == "__main__":
    main()
