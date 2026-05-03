import httpx
import math
import logging
from datetime import datetime
from config_manager import MAPBOX_TOKEN, config

def haversine(lon1, lat1, lon2, lat2):
    """Calculates the straight-line distance in kilometres between two GPS coordinates."""
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * (2 * math.asin(math.sqrt(a)))

def get_nearest_branch(end_lon, end_lat):
    """Finds the closest registered branch to the given coordinates."""
    branches = config.get("branches", {})
    if not branches:
        return None, None
    nearest_name, nearest_coords, min_dist = None, None, float('inf')

    for name, coords in branches.items():
        try:
            b_lon, b_lat = map(float, coords.split(','))
        except ValueError:
            logging.error(f"Invalid coordinate format for branch '{name}': {coords}")
            continue

        dist = haversine(end_lon, end_lat, b_lon, b_lat)
        if dist < min_dist:
            min_dist, nearest_name, nearest_coords = dist, name, coords

    return nearest_name, nearest_coords

def is_working_hours():
    """Returns True if the current time falls within the configured operating hours."""
    try:
        if config.get("maintenance_mode", False):
            return False
        now = datetime.now().time()
        start = datetime.strptime(config["work_hours"]["start"], "%H:%M").time()
        end = datetime.strptime(config["work_hours"]["end"], "%H:%M").time()
        return (start <= now <= end) if start <= end else (start <= now or now <= end)
    except Exception as e:
        logging.error(f"Error reading operating hours configuration (defaulting to closed): {e}", exc_info=True)
        return False

async def get_mapbox_route(start_coords, end_coords):
    """Fetches the actual road driving distance in kilometres between two points."""
    url = f"https://api.mapbox.com/directions/v5/mapbox/driving/{start_coords};{end_coords}"
    async with httpx.AsyncClient(timeout=5.0) as client:
        res = await client.get(url, params={"access_token": MAPBOX_TOKEN, "geometries": "geojson"})
        res.raise_for_status()
        data = res.json()
        if data.get("code") != "Ok":
            raise Exception("Invalid route coordinates.")
        return data["routes"][0]["distance"] / 1000.0

async def reverse_geocode(lon, lat):
    """
    Converts GPS coordinates into a human-readable address.
    Used to verify delivery location and prevent location spoofing.
    """
    url = f"https://api.mapbox.com/geocoding/v5/mapbox.places/{lon},{lat}.json"
    params = {
        "access_token": MAPBOX_TOKEN,
        "types": "address,poi,neighborhood,locality,place",
        "limit": 1
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(url, params=params)
            if res.status_code == 200:
                data = res.json()
                if data.get("features"):
                    return data["features"][0]["place_name"]
    except Exception as e:
        logging.error(f"Reverse geocoding failed: {e}", exc_info=True)

    return f"Lat: {lat:.5f}, Lon: {lon:.5f} (Unmapped Area)"

def get_vehicle_tier(quantity):
    """Determines the appropriate vehicle tier based on total order volume in cubic units."""
    for tier in config["vehicle_tiers"]:
        if quantity <= tier["max_quantity_cubes"]:
            return tier
    return config["vehicle_tiers"][-1]