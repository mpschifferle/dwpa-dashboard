"""
Real coastlines for the globe, without shipping a map file.

Natural Earth's 1:110m land polygons are public domain. We fetch them once,
rasterise them into a 1-degree grid of longitude runs (a few kB of JSON) and
serve that to the TV page. If no file and no network, the page falls back to its
built-in 5-degree mask.
"""

from __future__ import annotations

import json
import os
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_SOURCE = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
                  "master/geojson/ne_110m_land.geojson")
USER_AGENT = "hc-dashboard/1.0 (local help_crack dashboard)"
STEP = 1          # degrees per cell; 1 deg = 360 x 180 cells
COAST_SCALE = 100  # coastline coordinates are stored in hundredths of a degree
_LOCK = threading.Lock()


def _rings(geojson):
    """yield every polygon ring as a list of (lon, lat) tuples"""
    for feature in geojson.get("features") or []:
        geometry = feature.get("geometry") or {}
        kind = geometry.get("type")
        coords = geometry.get("coordinates") or []
        polygons = [coords] if kind == "Polygon" else coords if kind == "MultiPolygon" else []
        for polygon in polygons:
            for ring in polygon:
                yield [(float(point[0]), float(point[1])) for point in ring]


def fetch(source, timeout=60):
    """download and parse the land polygons"""
    request = Request(source, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def coast_rings(geojson, scale=COAST_SCALE):
    """polygon rings as flat lists of hundredths of a degree (lon, lat, lon, lat…)

    Drawing these as real polygons gives smooth coastlines; the 1-degree grid
    below is only the fallback for when this data is not available.
    """
    rings = []
    for ring in _rings(geojson):
        points = []
        previous = None
        for lon, lat in ring:
            pair = (int(round(lon * scale)), int(round(lat * scale)))
            if pair != previous:
                points.extend(pair)
                previous = pair
        # a closed ring repeats its first point; the renderer closes it itself
        if len(points) >= 8 and points[0] == points[-2] and points[1] == points[-1]:
            points = points[:-2]
        if len(points) >= 8:
            rings.append(points)
    return rings


def build_coast(dest, source=DEFAULT_SOURCE, timeout=60):
    """download + convert to coastline rings; returns (ring count, point count)"""
    data = {
        "scale": COAST_SCALE,
        "source": source,
        "built": round(time.time(), 3),
        "rings": coast_rings(fetch(source, timeout)),
    }
    tmp = f"{dest}.tmp"
    with open(tmp, "w", encoding="utf-8") as fd:
        json.dump(data, fd, separators=(",", ":"))
    os.replace(tmp, dest)
    return len(data["rings"]), sum(len(ring) // 2 for ring in data["rings"])


def _inside(lon, lat, ring):
    """ray casting point in polygon"""
    hit = False
    count = len(ring)
    for index in range(count):
        x1, y1 = ring[index]
        x2, y2 = ring[(index + 1) % count]
        if (y1 > lat) != (y2 > lat):
            x = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x:
                hit = not hit
    return hit


def rasterise(geojson, step=STEP):
    """polygons -> {'step': step, 'rows': [[[from, to], ...], ...]} (90N first)"""
    rows_count = int(180 / step)
    cols_count = int(360 / step)
    rings = []
    for ring in _rings(geojson):
        if len(ring) < 3:
            continue
        lons = [point[0] for point in ring]
        lats = [point[1] for point in ring]
        rings.append((ring, min(lons), max(lons), min(lats), max(lats)))

    rows = []
    for row in range(rows_count):
        lat = 90 - (row + 0.5) * step
        runs = []
        run_start = None
        for col in range(cols_count):
            lon = -180 + (col + 0.5) * step
            land = False
            for ring, lon_min, lon_max, lat_min, lat_max in rings:
                if lon < lon_min or lon > lon_max or lat < lat_min or lat > lat_max:
                    continue
                if _inside(lon, lat, ring):
                    land = True
                    break
            if land and run_start is None:
                run_start = col
            elif not land and run_start is not None:
                runs.append([run_start, col - 1])
                run_start = None
        if run_start is not None:
            runs.append([run_start, cols_count - 1])
        rows.append(runs)
    return {"step": step, "rows": rows}


def build(dest, source=DEFAULT_SOURCE, timeout=60):
    """download + rasterise + write; returns the number of cells written"""
    data = rasterise(fetch(source, timeout))
    data["source"] = source
    data["built"] = round(time.time(), 3)

    tmp = f"{dest}.tmp"
    with open(tmp, "w", encoding="utf-8") as fd:
        json.dump(data, fd, separators=(",", ":"))
    os.replace(tmp, dest)
    return sum(len(row) for row in data["rows"])


def load(dest):
    """the cached grid, or None"""
    try:
        with open(dest, "r", encoding="utf-8") as fd:
            data = json.load(fd)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "rows" not in data:
        return None
    return data


def ensure(dest, source=DEFAULT_SOURCE):
    """load the cache, building it once if it does not exist yet"""
    cached = load(dest)
    if cached:
        return cached, False
    with _LOCK:
        cached = load(dest)          # another thread may have built it meanwhile
        if cached:
            return cached, False
        try:
            build(dest, source)
        except (HTTPError, URLError, OSError, ValueError):
            return None, False
        return load(dest), True


def load_coast(dest):
    """the cached coastline rings, or None"""
    try:
        with open(dest, "r", encoding="utf-8") as fd:
            data = json.load(fd)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("rings"):
        return None
    return data


def ensure_coast(dest, source=DEFAULT_SOURCE):
    """load the coastline cache, building it once if it does not exist yet"""
    cached = load_coast(dest)
    if cached:
        return cached, False
    with _LOCK:
        cached = load_coast(dest)
        if cached:
            return cached, False
        try:
            build_coast(dest, source)
        except (HTTPError, URLError, OSError, ValueError):
            return None, False
        return load_coast(dest), True
