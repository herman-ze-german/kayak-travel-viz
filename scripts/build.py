#!/usr/bin/env python3

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from dateutil import tz

DATE_FMT = "%Y-%m-%d %H:%M:%S.%f"
IATA_RE = re.compile(r"\b\(?([A-Z]{3})\)?\b")

OURAIRPORTS_URL = "https://ourairports.com/data/airports.csv"
OPENFLIGHTS_URL = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat"

# Some older Kayak emails contain deprecated IATA codes (e.g., Berlin TXL/SXF).
# OurAirports / OpenFlights coverage for closed airports varies, so we alias them.
IATA_ALIASES = {
    "TXL": "BER",  # Berlin Tegel -> Berlin Brandenburg
    "SXF": "BER",  # Berlin Schönefeld -> Berlin Brandenburg
    # City codes / aggregations sometimes appear in emails. Pick a reasonable default.
    "NYC": "JFK",  # New York City -> JFK
}

# Manual patches for airports missing from datasets.
# Format: IATA -> (lat, lon, name, iso_country)
MANUAL_AIRPORTS: Dict[str, Tuple[float, float, str, Optional[str]]] = {
    "LGP": (13.1575, 123.7350, "Legazpi Airport", "PH"),
}

# Only treat rawAddress tokens as IATA when they look like airport codes (avoid Hbf/ZOB/etc.)
IATA_STRICT_RE = re.compile(r"^(?P<c1>[A-Z]{3})(?:\s*\((?P<c2>[A-Z]{3})\))?$")
IATA_PAREN_RE = re.compile(r"\(([A-Z]{3})\)")


def parse_dt(s: Optional[str], tzid: Optional[str] = None) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    try:
        dt = datetime.strptime(s, DATE_FMT)
    except ValueError:
        # fallback: try without fractional
        try:
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    if tzid:
        try:
            dt = dt.replace(tzinfo=tz.gettz(tzid))
        except Exception:
            pass
    return dt


def dt_iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt.isoformat()
    return dt.astimezone(tz.UTC).isoformat()


def download_airports_csv(cache_path: Path) -> Path:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists() and cache_path.stat().st_size > 100_000:
        return cache_path

    r = requests.get(OURAIRPORTS_URL, timeout=60)
    r.raise_for_status()
    cache_path.write_bytes(r.content)
    return cache_path


def download_openflights_dat(cache_path: Path) -> Path:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists() and cache_path.stat().st_size > 200_000:
        return cache_path

    r = requests.get(OPENFLIGHTS_URL, timeout=60)
    r.raise_for_status()
    cache_path.write_bytes(r.content)
    return cache_path


def load_iata_index(airports_csv: Path, openflights_dat: Optional[Path] = None) -> Dict[str, Tuple[float, float, str, Optional[str]]]:
    # returns IATA -> (lat, lon, name, iso_country)
    idx: Dict[str, Tuple[float, float, str, Optional[str]]] = {}

    # OurAirports CSV
    with airports_csv.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            iata = (row.get("iata_code") or "").strip().upper()
            if not iata or len(iata) != 3:
                continue
            try:
                lat = float(row.get("latitude_deg") or "")
                lon = float(row.get("longitude_deg") or "")
            except ValueError:
                continue
            name = (row.get("name") or "").strip()
            iso_country = (row.get("iso_country") or "").strip().upper() or None
            idx[iata] = (lat, lon, name, iso_country)

    # OpenFlights airports.dat (fills gaps; format is CSV-ish with quotes)
    if openflights_dat and openflights_dat.exists():
        with openflights_dat.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                # Fields: Airport ID,Name,City,Country,IATA,ICAO,Lat,Lon,...
                # https://openflights.org/data.html
                parts = list(csv.reader([line]))[0]
                if len(parts) < 8:
                    continue
                iata = (parts[4] or "").strip().upper()
                if not iata or iata == "\\N" or len(iata) != 3:
                    continue
                if iata in idx:
                    continue
                try:
                    lat = float(parts[6])
                    lon = float(parts[7])
                except ValueError:
                    continue
                name = (parts[1] or "").strip()
                # country code not available here; keep None
                idx[iata] = (lat, lon, name, None)

    # Apply manual patches last
    for code, rec in MANUAL_AIRPORTS.items():
        if code not in idx:
            idx[code] = rec

    return idx


def iata_from_raw(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    raw0 = str(raw).strip()
    raw_up = raw0.upper()

    # Strict formats:
    #  - "LHR" or "LHR (LHR)" or "MNL (MNL)"
    # Avoid free-text addresses like "Berlin Hbf" or "..., YUC., Mexico".
    m = IATA_STRICT_RE.match(raw_up)
    if m:
        code = m.group('c2') or m.group('c1')
        return IATA_ALIASES.get(code, code)

    # If it contains parenthesized codes, use the last one.
    par = IATA_PAREN_RE.findall(raw_up)
    if par:
        code = par[-1]
        return IATA_ALIASES.get(code, code)

    return None


def coords_from_address(addr: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    if not addr:
        return None, None, None
    lat = addr.get("latitude")
    lon = addr.get("longitude")
    raw = addr.get("rawAddress")
    if lat is not None and lon is not None:
        try:
            return float(lat), float(lon), str(raw) if raw else None
        except Exception:
            pass
    return None, None, str(raw) if raw else None


@dataclass
class SegmentRecord:
    trip_group_key: str
    trip_name: str
    trip_key: str  # unique per event
    type: str
    from_label: str
    to_label: str
    departure: Optional[datetime]
    arrival: Optional[datetime]
    from_lat: float
    from_lon: float
    to_lat: float
    to_lon: float


def collect_segments(raw_dir: Path, airport_index: Dict[str, Tuple[float, float, str, Optional[str]]]) -> Tuple[List[SegmentRecord], List[Dict[str, Any]]]:
    segments: List[SegmentRecord] = []
    event_summaries: List[Dict[str, Any]] = []

    for p in sorted(raw_dir.glob("*.txt")):
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue

        trip_group_key = str(obj.get("tripID") or p.stem)
        trip_name = str(obj.get("customName") or obj.get("name") or p.stem)

        events = obj.get("tripEvents") or []
        for ev_i, ev in enumerate(events):
            t = ev.get("UIDescription") or "Other"
            trip_key = f"{p.stem}:{ev.get('id', ev_i)}"

            # Hotel-ish events sometimes have venueStartDate/venueEndDate
            if t.lower() == "hotel" and (ev.get("address") or {}).get("latitude") is not None:
                addr = ev.get("address") or {}
                lat = float(addr.get("latitude"))
                lon = float(addr.get("longitude"))
                label = addr.get("locationName") or addr.get("rawAddress") or "Hotel"
                start = parse_dt(ev.get("venueStartDate"))
                end = parse_dt(ev.get("venueEndDate"))

                event_summaries.append({
                    "tripGroupKey": trip_group_key,
                    "tripName": trip_name,
                    "tripKey": trip_key,
                    "title": label,
                    "type": "Hotel",
                    "year": (start.year if start else None),
                    "start": dt_iso(start),
                    "end": dt_iso(end),
                    "segmentCount": 0,
                })
                continue

            legs = ev.get("legs") or []
            seg_count = 0
            start_dt: Optional[datetime] = None
            end_dt: Optional[datetime] = None
            title_guess: Optional[str] = None

            for leg in legs:
                for seg in (leg.get("segments") or []):
                    seg_count += 1

                    dep_dt = parse_dt(seg.get("departureDate"), seg.get("departureTimeZoneID"))
                    arr_dt = parse_dt(seg.get("arrivalDate"), seg.get("arrivalTimeZoneID"))
                    if dep_dt and (start_dt is None or dep_dt < start_dt):
                        start_dt = dep_dt
                    if arr_dt and (end_dt is None or arr_dt > end_dt):
                        end_dt = arr_dt

                    dep_addr = seg.get("departureAddress") or {}
                    arr_addr = seg.get("arrivalAddress") or {}

                    dep_lat, dep_lon, dep_raw = coords_from_address(dep_addr)
                    arr_lat, arr_lon, arr_raw = coords_from_address(arr_addr)

                    dep_label = dep_addr.get("city") or dep_addr.get("rawAddress") or dep_raw or "Departure"
                    arr_label = arr_addr.get("city") or arr_addr.get("rawAddress") or arr_raw or "Arrival"

                    # If missing coords, try resolve airport codes
                    if dep_lat is None or dep_lon is None:
                        code = iata_from_raw(dep_raw)
                        if code and code in airport_index:
                            a_lat, a_lon, a_name, a_country = airport_index[code]
                            dep_lat, dep_lon = a_lat, a_lon
                            dep_label = dep_label or f"{code}"
                            if dep_label and code not in dep_label:
                                dep_label = f"{dep_label} ({code})"

                    if arr_lat is None or arr_lon is None:
                        code = iata_from_raw(arr_raw)
                        if code and code in airport_index:
                            a_lat, a_lon, a_name, a_country = airport_index[code]
                            arr_lat, arr_lon = a_lat, a_lon
                            arr_label = arr_label or f"{code}"
                            if arr_label and code not in arr_label:
                                arr_label = f"{arr_label} ({code})"

                    if dep_lat is None or dep_lon is None or arr_lat is None or arr_lon is None:
                        # can't map it; skip
                        continue

                    if not title_guess:
                        title_guess = f"{dep_label} → {arr_label}"

                    segments.append(SegmentRecord(
                        trip_group_key=trip_group_key,
                        trip_name=trip_name,
                        trip_key=trip_key,
                        type=t,
                        from_label=str(dep_label),
                        to_label=str(arr_label),
                        departure=dep_dt,
                        arrival=arr_dt,
                        from_lat=float(dep_lat),
                        from_lon=float(dep_lon),
                        to_lat=float(arr_lat),
                        to_lon=float(arr_lon),
                    ))

            if seg_count > 0:
                event_summaries.append({
                    "tripGroupKey": trip_group_key,
                    "tripName": trip_name,
                    "tripKey": trip_key,
                    "title": title_guess or f"{t} trip",
                    "type": t,
                    "year": (start_dt.year if start_dt else None),
                    "start": dt_iso(start_dt),
                    "end": dt_iso(end_dt),
                    "segmentCount": len([s for s in segments if s.trip_key == trip_key]),
                })

    return segments, event_summaries


def to_geojson(segments: List[SegmentRecord]) -> Dict[str, Any]:
    feats: List[Dict[str, Any]] = []

    for s in segments:
        year = s.departure.year if s.departure else (s.arrival.year if s.arrival else None)
        feats.append({
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [s.from_lon, s.from_lat],
                    [s.to_lon, s.to_lat]
                ]
            },
            "properties": {
                "tripGroupKey": s.trip_group_key,
                "tripName": s.trip_name,
                "tripKey": s.trip_key,
                "type": s.type,
                "year": year,
                "fromLabel": s.from_label,
                "toLabel": s.to_label,
                "departure": dt_iso(s.departure),
                "arrival": dt_iso(s.arrival)
            }
        })

        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [s.from_lon, s.from_lat]},
            "properties": {"tripGroupKey": s.trip_group_key, "tripName": s.trip_name, "tripKey": s.trip_key, "type": s.type, "year": year, "label": s.from_label}
        })
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [s.to_lon, s.to_lat]},
            "properties": {"tripGroupKey": s.trip_group_key, "tripName": s.trip_name, "tripKey": s.trip_key, "type": s.type, "year": year, "label": s.to_label}
        })

    return {"type": "FeatureCollection", "features": feats}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    # Great-circle distance
    import math

    r = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def build_routes_png(segments: List[SegmentRecord], out_png: Path):
    import plotly.graph_objects as go

    lats = []
    lons = []
    for s in segments:
        lats += [s.from_lat, s.to_lat, None]
        lons += [s.from_lon, s.to_lon, None]

    fig = go.Figure()
    fig.add_trace(go.Scattergeo(
        lat=lats,
        lon=lons,
        mode='lines',
        line=dict(width=1, color='rgba(96,165,250,0.7)'),
        hoverinfo='skip'
    ))

    fig.update_layout(
        title="Travel Routes",
        geo=dict(
            projection_type='natural earth',
            showcountries=True,
            countrycolor='rgba(255,255,255,0.15)',
            showland=True,
            landcolor='rgb(15, 23, 42)',
            showocean=True,
            oceancolor='rgb(2, 6, 23)',
            coastlinecolor='rgba(255,255,255,0.10)',
            bgcolor='rgb(2, 6, 23)'
        ),
        paper_bgcolor='rgb(2, 6, 23)',
        plot_bgcolor='rgb(2, 6, 23)',
        font=dict(color='rgb(229,231,235)')
    )

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.write_image(str(out_png), width=1800, height=900, scale=2)


def build_timeline_html(events: List[Dict[str, Any]], out_html: Path):
    import pandas as pd
    import plotly.express as px

    df = pd.DataFrame(events)
    df = df.dropna(subset=["start", "end"]).copy()
    if df.empty:
        return

    # nicer labels
    df["label"] = df.apply(lambda r: (r.get("title") or "Trip")[:80], axis=1)

    df["start_dt"] = pd.to_datetime(df["start"], utc=True, errors="coerce")
    df["end_dt"] = pd.to_datetime(df["end"], utc=True, errors="coerce")
    df = df.dropna(subset=["start_dt", "end_dt"]).copy()

    fig = px.timeline(
        df,
        x_start="start_dt",
        x_end="end_dt",
        y="label",
        color="type",
        hover_data={"tripKey": True, "year": True, "segmentCount": True, "start": True, "end": True},
        title="Timeline (bands)"
    )
    fig.update_yaxes(autorange="reversed", title=None)
    fig.update_layout(
        template="plotly_dark",
        height=max(600, 18 * len(df) + 200),
        margin=dict(l=20, r=20, t=60, b=20)
    )

    out_html.write_text(fig.to_html(include_plotlyjs="cdn", full_html=True), encoding="utf-8")


def build_calendar_heatmap_html(segments: List[SegmentRecord], events: List[Dict[str, Any]], out_html: Path):
    import numpy as np
    import pandas as pd
    import plotly.graph_objects as go

    # Metric per day: distance_km (sum of segment distances by departure day)
    rows = []
    for s in segments:
        if not s.departure:
            continue
        day = s.departure.date()
        km = haversine_km(s.from_lat, s.from_lon, s.to_lat, s.to_lon)
        rows.append({"date": pd.Timestamp(day), "distance_km": km, "segments": 1})

    if not rows:
        return

    df = pd.DataFrame(rows).groupby("date", as_index=False).sum()
    df["year"] = df["date"].dt.year

    years = sorted(df["year"].unique().tolist())
    year = years[-1]

    def fig_for_year(y: int):
        d = df[df["year"] == y].copy()
        start = pd.Timestamp(f"{y}-01-01")
        end = pd.Timestamp(f"{y}-12-31")
        all_days = pd.date_range(start, end, freq="D")
        d = all_days.to_frame(index=False, name="date").merge(d, on="date", how="left").fillna(0)

        # GitHub-like grid: week index x weekday
        d["dow"] = d["date"].dt.weekday  # Mon=0
        # Week number relative to first day
        d["week"] = ((d["date"] - start).dt.days // 7).astype(int)

        z = np.full((7, d["week"].max() + 1), np.nan)
        for _, r in d.iterrows():
            z[int(r["dow"]), int(r["week"])] = float(r["distance_km"])

        fig = go.Figure(data=go.Heatmap(
            z=z,
            x=list(range(z.shape[1])),
            y=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            colorscale=[[0, "#111827"], [0.2, "#1f2937"], [0.4, "#2563eb"], [0.7, "#60a5fa"], [1, "#93c5fd"]],
            colorbar=dict(title="km / day"),
            hoverinfo="skip"
        ))
        fig.update_layout(
            template="plotly_dark",
            title=f"Calendar heatmap — distance traveled per day ({y})",
            height=320,
            margin=dict(l=20, r=20, t=60, b=20)
        )
        return fig

    # Single-year HTML for now (latest year). Simple + fast.
    fig = fig_for_year(year)
    out_html.write_text(fig.to_html(include_plotlyjs="cdn", full_html=True), encoding="utf-8")


def build_dashboard_html(segments: List[SegmentRecord], events: List[Dict[str, Any]], out_html: Path):
    import pandas as pd
    import plotly.express as px
    import plotly.graph_objects as go
    from plotly.io import to_html

    # Yearly stats
    tdf = pd.DataFrame(events)
    tdf = tdf.dropna(subset=["start", "end"]).copy()
    if not tdf.empty:
        tdf["start_dt"] = pd.to_datetime(tdf["start"], utc=True, errors="coerce")
        tdf["end_dt"] = pd.to_datetime(tdf["end"], utc=True, errors="coerce")
        tdf["trip_days"] = (tdf["end_dt"] - tdf["start_dt"]).dt.total_seconds() / 86400.0
        yearly = tdf.groupby(tdf["start_dt"].dt.year).agg(
            trips=("tripKey", "count"),
            avg_trip_days=("trip_days", "mean"),
            segments=("segmentCount", "sum")
        ).reset_index().rename(columns={"start_dt": "year", "index": "year"})
        yearly.rename(columns={"start_dt": "year"}, inplace=True)
    else:
        yearly = pd.DataFrame(columns=["year", "trips", "avg_trip_days", "segments"])

    # Distance per year from segments
    srows = []
    for s in segments:
        if not s.departure:
            continue
        y = s.departure.year
        km = haversine_km(s.from_lat, s.from_lon, s.to_lat, s.to_lon)
        srows.append({"year": y, "km": km, "to": s.to_label, "from": s.from_label})
    sdf = pd.DataFrame(srows)

    dist_year = sdf.groupby("year", as_index=False)["km"].sum() if not sdf.empty else pd.DataFrame(columns=["year", "km"])

    # Top destinations (by arrival label)
    top_dest = (sdf.groupby("to", as_index=False)["km"].count().sort_values("km", ascending=False).head(12)
                .rename(columns={"km": "arrivals"})) if not sdf.empty else pd.DataFrame(columns=["to", "arrivals"])

    fig_year_trips = px.bar(yearly, x="year", y="trips", title="Trips per year") if not yearly.empty else go.Figure()
    fig_year_dist = px.bar(dist_year, x="year", y="km", title="Distance per year (km)") if not dist_year.empty else go.Figure()
    fig_top = px.bar(top_dest, x="arrivals", y="to", orientation="h", title="Top arrival places") if not top_dest.empty else go.Figure()

    # Routes map (same style as routes PNG)
    lats = []
    lons = []
    for s in segments:
        lats += [s.from_lat, s.to_lat, None]
        lons += [s.from_lon, s.to_lon, None]
    fig_map = go.Figure(go.Scattergeo(lat=lats, lon=lons, mode='lines', line=dict(width=1, color='rgba(96,165,250,0.7)'), hoverinfo='skip'))
    fig_map.update_layout(
        title="Routes",
        template="plotly_dark",
        geo=dict(
            projection_type='natural earth',
            showcountries=True,
            countrycolor='rgba(255,255,255,0.15)',
            showland=True,
            landcolor='rgb(15, 23, 42)',
            showocean=True,
            oceancolor='rgb(2, 6, 23)',
            coastlinecolor='rgba(255,255,255,0.10)',
            bgcolor='rgb(2, 6, 23)'
        ),
        margin=dict(l=0, r=0, t=50, b=0),
        height=520,
    )

    # Timeline (re-use function logic quickly)
    if not tdf.empty:
        tdf["label"] = tdf.apply(lambda r: (r.get("title") or "Trip")[:80], axis=1)
        fig_timeline = px.timeline(tdf, x_start="start_dt", x_end="end_dt", y="label", color="type", title="Timeline")
        fig_timeline.update_yaxes(autorange="reversed", title=None)
        fig_timeline.update_layout(template="plotly_dark", height=max(500, 18 * len(tdf) + 200))
    else:
        fig_timeline = go.Figure()

    html = f"""<!doctype html>
<html><head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Travel dashboard</title>
  <style>
    body {{ margin:0; font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial; background:#0b0e14; color:#e5e7eb; }}
    header {{ padding:14px 16px; border-bottom:1px solid rgba(255,255,255,.08); }}
    h1 {{ margin:0; font-size:18px; }}
    .wrap {{ padding: 14px 16px; display:grid; gap:16px; }}
    .grid2 {{ display:grid; grid-template-columns: 1fr 1fr; gap:16px; }}
    @media(max-width: 980px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}
    .card {{ border:1px solid rgba(255,255,255,.10); border-radius:14px; padding:10px; background:rgba(17,24,39,.6); }}
  </style>
</head>
<body>
<header><h1>Travel dashboard</h1></header>
<div class=\"wrap\">
  <div class=\"card\">{to_html(fig_map, include_plotlyjs='cdn', full_html=False)}</div>
  <div class=\"grid2\">
    <div class=\"card\">{to_html(fig_year_trips, include_plotlyjs=False, full_html=False)}</div>
    <div class=\"card\">{to_html(fig_year_dist, include_plotlyjs=False, full_html=False)}</div>
  </div>
  <div class=\"grid2\">
    <div class=\"card\">{to_html(fig_top, include_plotlyjs=False, full_html=False)}</div>
    <div class=\"card\">{to_html(fig_timeline, include_plotlyjs=False, full_html=False)}</div>
  </div>
</div>
</body></html>"""

    out_html.write_text(html, encoding="utf-8")


def build_pretty_summary_png(segments: List[SegmentRecord], events: List[Dict[str, Any]], out_png: Path):
    import pandas as pd
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    total_km = 0.0
    years = []
    for s in segments:
        total_km += haversine_km(s.from_lat, s.from_lon, s.to_lat, s.to_lon)
        if s.departure:
            years.append(s.departure.year)
    year_min = min(years) if years else None
    year_max = max(years) if years else None

    # Map trace
    lats = []
    lons = []
    for s in segments:
        lats += [s.from_lat, s.to_lat, None]
        lons += [s.from_lon, s.to_lon, None]
    map_trace = go.Scattergeo(lat=lats, lon=lons, mode='lines', line=dict(width=1, color='rgba(96,165,250,0.75)'), hoverinfo='skip')

    # Yearly km bar
    srows = []
    for s in segments:
        if not s.departure:
            continue
        srows.append({"year": s.departure.year, "km": haversine_km(s.from_lat, s.from_lon, s.to_lat, s.to_lon)})
    sdf = pd.DataFrame(srows)
    by_year = sdf.groupby("year", as_index=False)["km"].sum().sort_values("year") if not sdf.empty else pd.DataFrame(columns=["year", "km"])

    fig = make_subplots(
        rows=2, cols=2,
        specs=[[{"type": "geo", "colspan": 2}, None], [{"type": "xy"}, {"type": "indicator"}]],
        row_heights=[0.72, 0.28],
        column_widths=[0.65, 0.35],
        horizontal_spacing=0.08,
        vertical_spacing=0.08,
    )

    fig.add_trace(map_trace, row=1, col=1)
    if not by_year.empty:
        fig.add_trace(go.Bar(x=by_year["year"], y=by_year["km"], marker_color="#60a5fa", name="km"), row=2, col=1)

    fig.add_trace(go.Indicator(
        mode="number",
        value=float(total_km),
        number={"suffix": " km", "font": {"size": 34}},
        title={"text": "Total distance"},
    ), row=2, col=2)

    title = "Travel summary"
    if year_min and year_max:
        title += f" ({year_min}–{year_max})"

    fig.update_layout(
        title=title,
        template="plotly_dark",
        geo=dict(
            projection_type='natural earth',
            showcountries=True,
            countrycolor='rgba(255,255,255,0.15)',
            showland=True,
            landcolor='rgb(15, 23, 42)',
            showocean=True,
            oceancolor='rgb(2, 6, 23)',
            coastlinecolor='rgba(255,255,255,0.10)',
            bgcolor='rgb(2, 6, 23)'
        ),
        paper_bgcolor='rgb(2, 6, 23)',
        plot_bgcolor='rgb(2, 6, 23)',
        margin=dict(l=30, r=30, t=70, b=30),
        height=1000,
        width=1800,
        showlegend=False,
    )

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.write_image(str(out_png), scale=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='in_dir', required=True, help='Input dir containing Kayak *.txt files')
    ap.add_argument('--out', dest='out_dir', required=True, help='Output dir for static site files')
    ap.add_argument('--png', dest='png_path', required=False, help='Optional routes PNG path')
    ap.add_argument('--pretty', dest='pretty_png_path', required=False, help='Optional pretty summary PNG path')
    ap.add_argument('--airports-cache', default='./data/airports.csv', help='Where to cache OurAirports airports.csv')
    ap.add_argument('--openflights-cache', default='./data/openflights_airports.dat', help='Where to cache OpenFlights airports.dat')
    args = ap.parse_args()

    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir)
    airports_cache = Path(args.airports_cache)
    openflights_cache = Path(args.openflights_cache)

    airports_csv = download_airports_csv(airports_cache)
    openflights_dat = download_openflights_dat(openflights_cache)
    airport_index = load_iata_index(airports_csv, openflights_dat)

    segments, events = collect_segments(in_dir, airport_index)

    geo = to_geojson(segments)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'trips.geojson').write_text(json.dumps(geo, ensure_ascii=False), encoding='utf-8')

    # Group events by trip
    groups: Dict[str, Dict[str, Any]] = {}
    for e in events:
        gk = e.get('tripGroupKey')
        if not gk:
            continue
        g = groups.get(gk)
        if not g:
            g = {
                'tripGroupKey': gk,
                'tripName': e.get('tripName') or gk,
                'year': e.get('year'),
                'start': e.get('start'),
                'end': e.get('end'),
                'events': [],
            }
            groups[gk] = g
        g['events'].append(e)
        # update range
        if e.get('start') and (not g.get('start') or e['start'] < g['start']):
            g['start'] = e['start']
        if e.get('end') and (not g.get('end') or e['end'] > g['end']):
            g['end'] = e['end']
        if e.get('year') and (not g.get('year') or e['year'] < g['year']):
            g['year'] = e['year']

    trip_groups = list(groups.values())
    # sort inside
    for g in trip_groups:
        g['events'] = sorted(g['events'], key=lambda x: (x.get('start') or ''))

    summary = {
        "sourceFiles": len(list(in_dir.glob('*.txt'))),
        "segmentCount": len(segments),
        "tripGroups": sorted(trip_groups, key=lambda x: (x.get('start') or '')),
        "events": events,
    }
    (out_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')

    # New outputs
    build_timeline_html(events, out_dir / 'timeline.html')
    build_calendar_heatmap_html(segments, events, out_dir / 'calendar.html')
    build_dashboard_html(segments, events, out_dir / 'dashboard.html')

    if args.png_path:
        build_routes_png(segments, Path(args.png_path))
    if args.pretty_png_path:
        build_pretty_summary_png(segments, events, Path(args.pretty_png_path))

    print(f"Wrote: {out_dir / 'trips.geojson'}")
    print(f"Wrote: {out_dir / 'summary.json'}")
    print(f"Wrote: {out_dir / 'timeline.html'}")
    print(f"Wrote: {out_dir / 'calendar.html'}")
    print(f"Wrote: {out_dir / 'dashboard.html'}")
    if args.png_path:
        print(f"Wrote: {args.png_path}")
    if args.pretty_png_path:
        print(f"Wrote: {args.pretty_png_path}")


if __name__ == '__main__':
    main()
