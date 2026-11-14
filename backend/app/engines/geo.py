"""Distance between two map points: the Haversine formula (Technical Plan issue M3).

It gives the straight-line ("as the crow flies") distance over the Earth's
surface. Road distance is longer, but for ranking "what is near me" this is
accurate enough, needs no paid map API, and works offline.

The same formula is used in two places that must agree:
  * haversine_km()       - Python, for tests and single calculations
  * haversine_km_sql()   - inside the PostgreSQL query, to filter and sort
                           thousands of listings without loading them all
"""

from math import asin, cos, radians, sin, sqrt
from typing import Any

from sqlalchemy import func

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    d_lat = radians(lat2 - lat1)
    d_lng = radians(lng2 - lng1)
    a = sin(d_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(min(1.0, a)))  # min() guards tiny rounding above 1


def haversine_km_sql(lat_column: Any, lng_column: Any, lat: float, lng: float) -> Any:
    """The same formula as a SQL expression (columns vs a fixed point)."""
    d_lat = func.radians(lat_column - lat)
    d_lng = func.radians(lng_column - lng)
    a = (func.power(func.sin(d_lat / 2), 2)
         + func.cos(func.radians(lat)) * func.cos(func.radians(lat_column)) * func.power(func.sin(d_lng / 2), 2))
    return 2 * EARTH_RADIUS_KM * func.asin(func.sqrt(func.least(1.0, a)))
