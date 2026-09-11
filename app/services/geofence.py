"""Geofence evaluation: circles + polygons (ticket-013, P2).

Location is evidence, never verdict. These pure functions decide only
the geometric relationship; policy decides what it means.
"""

from __future__ import annotations

import math

EARTH_M = 6371000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(math.sqrt(a))


def in_circle(lat: float, lon: float, center_lat: float,
              center_lon: float, radius_m: float) -> bool:
    """True when the point is within the circle (boundary counts as inside)."""
    return haversine_m(lat, lon, center_lat, center_lon) <= radius_m


def in_polygon(lat: float, lon: float, polygon: list) -> bool:
    """Ray-cast point-in-polygon. Boundary/vertex hits count as inside.

    Args:
        lat: Point latitude. lon: Point longitude.
        polygon: Sequence of (lat, lon) vertices (any winding).
    """
    if not polygon:
        return False
    inside = False
    n = len(polygon)
    for i in range(n):
        lat1, lon1 = polygon[i]
        lat2, lon2 = polygon[(i + 1) % n]
        # Vertex hit
        if (lat, lon) == (lat1, lon1):
            return True
        # Edge crossing test on the lon axis
        if ((lon1 > lon) != (lon2 > lon)):
            intersect = lat1 + (lon - lon1) * (lat2 - lat1) / (lon2 - lon1 or 1e-12)
            if abs(intersect - lat) < 1e-9:
                return True  # on-edge hit
            if intersect > lat:
                inside = not inside
    return inside


def evaluate(lat, lon, accuracy_m, fence: dict | None) -> str:
    """Classify a fix against a site fence.

    Args:
        lat/lon: Fix coordinates (None when the device gave nothing).
        accuracy_m: Reported accuracy (None unknown).
        fence: {"type": "circle", "lat":.., "lon":.., "radius_m":..}
            or {"type": "polygon", "points": [(lat, lon), ...]}.

    Returns:
        inside / outside / anomaly / unavailable (model LocationVerdict values).
    """
    from app.models.attendance import LocationVerdict

    if lat is None or lon is None:
        return LocationVerdict.UNAVAILABLE
    if fence is None:
        return LocationVerdict.ANOMALY
    try:
        if fence.get("type") == "circle":
            ok = in_circle(lat, lon, float(fence["lat"]),
                           float(fence["lon"]), float(fence["radius_m"]))
        elif fence.get("type") == "polygon":
            ok = in_polygon(lat, lon, list(fence.get("points") or []))
        else:
            return LocationVerdict.ANOMALY
    except (TypeError, ValueError, KeyError):
        return LocationVerdict.ANOMALY
    if not ok:
        return LocationVerdict.OUTSIDE
    if accuracy_m is not None:
        try:
            if float(accuracy_m) > 500:
                return LocationVerdict.ANOMALY
        except (TypeError, ValueError):
            return LocationVerdict.ANOMALY
    return LocationVerdict.INSIDE
