import json
import os
import aiofiles
import asyncio
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

CONFIG_FILE = 'config.json'
PERSISTENCE_FILE = 'bot_data.pickle'
STATS_FILE = 'stats.jsonl'
SUPER_ADMIN_ID = 7616185765  # Primary administrator — do not change

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MAPBOX_TOKEN = os.getenv("MAPBOX_TOKEN")

if not TELEGRAM_TOKEN or not MAPBOX_TOKEN:
    raise ValueError("CRITICAL: Missing API tokens in .env file. Please set TELEGRAM_TOKEN and MAPBOX_TOKEN.")

# ==========================================
# STATE DEFINITIONS
# ==========================================

# Customer E-commerce States
STATE_BROWSE_CATALOG = 1
STATE_CONFIRM_LOCATION = 2
STATE_CONFIRM_ORDER = 3

# Admin Add Branch States
ADMIN_WAIT_BRANCH_PIN = 4
ADMIN_CONFIRM_BRANCH_PIN = 5

# Admin Add Item Wizard States
ADMIN_ADD_PHOTO = 6
ADMIN_ADD_NAME = 7
ADMIN_ADD_PRICE = 8
ADMIN_ADD_VOL = 9
ADMIN_ADD_CAT = 10
ADMIN_ADD_STOCK = 11

# Admin Edit Price State
ADMIN_EDIT_PRICE = 12

# ==========================================
# CONFIGURATION MANAGEMENT
# ==========================================

config_lock = asyncio.Lock()

def load_config_sync():
    """Loads configuration synchronously on startup."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)

    default_config = {
        # System & Messages
        "maintenance_mode": False,
        "maintenance_msg": "The system is currently undergoing maintenance. Please check back later. / පද්ධතිය නඩත්තු වෙමින් පවතී.",
        "welcome_msg": "Welcome to the Logistics Engine.\nTap below to browse our product catalogue. / ආරම්භ කිරීමට පහත බොත්තම ඔබන්න.",
        "receipt_footer": "Thank you for your order. / ඔබගේ ඇණවුම සඳහා ස්තූතියි.",
        "contact_info": "@Admin",

        # Pricing & Logistics Variables
        "diesel_price_per_liter": 350.0,
        "driver_hourly_rate": 361.0,
        "additional_fee": 0.0,
        "base_fare": 0.0,
        "free_km": 10.0,
        "surge_multiplier": 1.0,
        "discount_percentage": 0.0,
        "tax_rate_percentage": 0.0,
        "fuel_safety_margin": 1.2,
        "driver_distance_threshold_km": 50.0,

        # Operational Constraints
        "max_distance_km": 500.0,
        "max_capacity_cubes": 100.0,
        "work_hours": {"start": "00:00", "end": "23:59"},
        "low_stock_threshold": 5,

        # Databases
        "branches": {},
        "catalog": {},
        "active_orders": {},

        # Vehicle Tiers
        "vehicle_tiers": [
            {"name": "Small (Batta)", "max_quantity_cubes": 1.0, "fuel_efficiency_kmpl": 10.0},
            {"name": "Medium (Canter)", "max_quantity_cubes": 3.0, "fuel_efficiency_kmpl": 8.0},
            {"name": "Large (Tipper)", "max_quantity_cubes": 999.0, "fuel_efficiency_kmpl": 4.0}
        ],

        # Access Control
        "admins": [SUPER_ADMIN_ID],
        "banned_users": []
    }
    with open(CONFIG_FILE, 'w') as f:
        json.dump(default_config, f, indent=4)
    return default_config

async def save_config(config_data):
    """Saves configuration asynchronously to prevent blocking the event loop."""
    async with config_lock:
        async with aiofiles.open(CONFIG_FILE, mode='w') as f:
            await f.write(json.dumps(config_data, indent=4))

# Load into memory globally
config = load_config_sync()

# ==========================================
# SECURITY HELPERS
# ==========================================

def is_admin(user_id):
    """Returns True if the user is the primary administrator or a registered admin."""
    return user_id == SUPER_ADMIN_ID or user_id in config.get("admins", [])

def is_banned(user_id):
    """Returns True if the user is on the banned list."""
    return user_id in config.get("banned_users", [])