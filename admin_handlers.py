import json
import os
import math
import time
import datetime
import zipfile
import random
import string
import asyncio
import psutil
import telegram.error
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler

from config_manager import (
    config, save_config, is_admin, SUPER_ADMIN_ID, STATS_FILE,
    ADMIN_WAIT_BRANCH_PIN, ADMIN_CONFIRM_BRANCH_PIN,
    ADMIN_ADD_PHOTO, ADMIN_ADD_NAME, ADMIN_ADD_PRICE,
    ADMIN_ADD_VOL, ADMIN_ADD_CAT, ADMIN_ADD_STOCK, ADMIN_EDIT_PRICE
)
from logistics_engine import reverse_geocode
import license

ITEMS_PER_PAGE = 5

# ==========================================
# 1. STORE ADMIN DASHBOARD
# ==========================================
def build_admin_dashboard_kb():
    """Builds the admin control panel inline keyboard."""
    status_emoji = '🔴' if config.get('maintenance_mode') else '🟢'
    status_text = 'Offline — Maintenance' if config.get('maintenance_mode') else 'Online'

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 View / Edit Catalogue", callback_data='admin_view_cat')],
        [InlineKeyboardButton("➕ Add New Item", callback_data='admin_add_item')],
        [InlineKeyboardButton(f"{status_emoji} System Status: {status_text}", callback_data='admin_toggle_maint')],
        [InlineKeyboardButton("🔄 Refresh", callback_data='admin_dash')]
    ])

async def admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    cat_len = len(config.get('catalog', {}))
    orders_len = len(config.get('active_orders', {}))

    text = (
        f"👑 *Store Admin Panel*\n━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 *Catalogue Items:* {cat_len}\n"
        f"🚚 *Active Orders:* {orders_len}\n\n"
        f"💰 *Pricing & Logistics:*\n"
        f"Base Fare: Rs. {config['base_fare']} | Free: {config['free_km']}km\n"
        f"Surge: x{config['surge_multiplier']} | Tax: {config['tax_rate_percentage']}%\n"
        f"Fuel Margin: {config['fuel_safety_margin']}x | Driver Threshold: {config['driver_distance_threshold_km']}km\n\n"
        f"_Use setter commands (e.g. /setbasefare 500) to update values._"
    )

    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(text, reply_markup=build_admin_dashboard_kb(), parse_mode='Markdown')
        except telegram.error.BadRequest:
            pass
    else:
        await update.message.reply_text(text, reply_markup=build_admin_dashboard_kb(), parse_mode='Markdown')

async def admin_dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await admin_dashboard(update, context)

async def toggle_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    config['maintenance_mode'] = not config.get('maintenance_mode', False)
    await save_config(config)
    status_label = 'Offline' if config['maintenance_mode'] else 'Online'
    await query.answer(f"System is now {status_label}.", show_alert=True)
    await admin_dashboard(update, context)

# ==========================================
# 2. CATALOGUE VIEW & INLINE EDITING
# ==========================================
async def view_paginated_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    page = int(query.data.split('admin_cat_page_')[1]) if query.data.startswith('admin_cat_page_') else 0
    items = list(config.get('catalog', {}).items())

    if not items:
        return await query.edit_message_text(
            "The catalogue is currently empty.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Dashboard", callback_data='admin_dash')]])
        )

    total_pages = math.ceil(len(items) / ITEMS_PER_PAGE)
    current_items = items[page * ITEMS_PER_PAGE: (page + 1) * ITEMS_PER_PAGE]

    text = f"📋 *Catalogue (Page {page + 1}/{total_pages})*\n━━━━━━━━━━━━━━━━━━━━\n"
    kb = []

    for i_id, i in current_items:
        text += f"📦 *{i['name']}* (`{i_id}`)\n"
        text += f"Category: {i['category']} | Volume: {i['volume_cubes']}cu | Stock: {i.get('stock', '∞')}\n"
        text += f"Price: Rs. {i['price']:,.2f}\n\n"

        kb.append([
            InlineKeyboardButton(f"✏️ Edit Price: {i['name'][:10]}", callback_data=f'edit_price_{i_id}'),
            InlineKeyboardButton("❌ Remove", callback_data=f'rm_admin_itm_{i_id}')
        ])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f'admin_cat_page_{page - 1}'))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f'admin_cat_page_{page + 1}'))
    if nav_row:
        kb.append(nav_row)

    kb.append([InlineKeyboardButton("🔙 Back to Dashboard", callback_data='admin_dash')])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def remove_item_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    i_id = query.data.split('rm_admin_itm_')[1]

    if config['catalog'].pop(i_id, None):
        await save_config(config)
        await query.answer("Item removed.", show_alert=True)
    else:
        await query.answer("Item not found.", show_alert=True)
    await view_paginated_catalog(update, context)

# --- INLINE PRICE EDITING ---
async def start_price_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    item_id = query.data.split('edit_price_')[1]
    context.user_data['editing_item'] = item_id

    item_name = config['catalog'][item_id]['name']
    await query.message.reply_text(
        f"✏️ Enter the new price for *{item_name}* (numbers only):",
        parse_mode='Markdown'
    )
    return ADMIN_EDIT_PRICE

async def save_new_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    item_id = context.user_data.get('editing_item')
    try:
        new_price = float(update.message.text)
        config['catalog'][item_id]['price'] = new_price
        await save_config(config)
        await update.message.reply_text(f"✅ Price updated to Rs. {new_price:,.2f}.")
    except ValueError:
        await update.message.reply_text("⚠️ Invalid input. Please enter a numeric value. Edit cancelled.")

    context.user_data.pop('editing_item', None)
    return ConversationHandler.END

# ==========================================
# 3. ADD ITEM WIZARD
# ==========================================
async def wizard_start_add_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['new_item'] = {}
    await query.message.reply_text(
        "➕ *Add New Item*\nStep 1 of 6: Send a product photo, or type 'skip' to proceed without one.",
        parse_mode='Markdown'
    )
    return ADMIN_ADD_PHOTO

async def wizard_receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        context.user_data['new_item']['photo_file_id'] = update.message.photo[-1].file_id
    elif update.message.text.lower() != 'skip':
        await update.message.reply_text("Please send a photo or type 'skip' to continue.")
        return ADMIN_ADD_PHOTO
    await update.message.reply_text("Step 2 of 6: Enter the product name.")
    return ADMIN_ADD_NAME

async def wizard_receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_item']['name'] = update.message.text
    await update.message.reply_text("Step 3 of 6: Enter the unit price (e.g. 2400.50):")
    return ADMIN_ADD_PRICE

async def wizard_receive_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['new_item']['price'] = float(update.message.text)
    except ValueError:
        await update.message.reply_text("⚠️ Invalid input. Please enter a numeric value:")
        return ADMIN_ADD_PRICE
    await update.message.reply_text("Step 4 of 6: Enter the volume in cubic units (e.g. 0.05):")
    return ADMIN_ADD_VOL

async def wizard_receive_vol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['new_item']['volume_cubes'] = float(update.message.text)
    except ValueError:
        await update.message.reply_text("⚠️ Invalid input. Please enter a numeric value:")
        return ADMIN_ADD_VOL
    await update.message.reply_text("Step 5 of 6: Enter the product category (e.g. Cement):")
    return ADMIN_ADD_CAT

async def wizard_receive_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_item']['category'] = update.message.text.strip()
    await update.message.reply_text("Step 6 of 6: Enter the opening stock quantity (e.g. 100):")
    return ADMIN_ADD_STOCK

async def wizard_receive_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['new_item']['stock'] = int(update.message.text)
    except ValueError:
        await update.message.reply_text("⚠️ Invalid input. Please enter a whole number:")
        return ADMIN_ADD_STOCK

    i_id = "ITM-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=4))

    config.setdefault('catalog', {})[i_id] = context.user_data['new_item']
    await save_config(config)

    await update.message.reply_text(
        f"✅ *Item Added*\nID: `{i_id}`\nName: {context.user_data['new_item']['name']}",
        parse_mode='Markdown'
    )
    context.user_data.pop('new_item', None)
    return ConversationHandler.END

# ==========================================
# 4. ORDER MANAGEMENT
# ==========================================
async def manage_order_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    parts = data.split('_', 2)
    action, order_id = parts[1], parts[2]

    if order_id not in config.get('active_orders', {}):
        return await query.edit_message_text("⚠️ Order not found.")

    order = config['active_orders'][order_id]
    user_id = order['user_id']

    if action == 'accept':
        order['status'] = '🔵 Accepted'
        notify_msg = f"✅ Your order `{order_id}` has been accepted."
    elif action == 'reject':
        order['status'] = '🔴 Rejected'
        notify_msg = f"❌ Your order `{order_id}` could not be fulfilled."
    elif action == 'dispatch':
        order['status'] = '🚚 Dispatched'
        notify_msg = f"🚚 Your order `{order_id}` has been dispatched."
    elif action == 'deliver':
        order['status'] = '🟢 Delivered'
        notify_msg = f"✅ Your order `{order_id}` has been delivered."
    else:
        return

    await save_config(config)
    await query.edit_message_text(
        query.message.text + f"\n\n*Status Updated:* {order['status']}",
        parse_mode='Markdown'
    )
    try:
        await context.bot.send_message(chat_id=user_id, text=notify_msg, parse_mode='Markdown')
    except Exception:
        pass

# ==========================================
# 5. BRANCH LOCATION WIZARD
# ==========================================
async def cmd_addbranch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    name = ' '.join(context.args)
    if not name:
        await update.message.reply_text("⚠️ Usage: /addbranch <BranchName>")
        return ConversationHandler.END

    context.user_data['temp_branch_name'] = name
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Send Current Location", request_location=True)], ["❌ Cancel"]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await update.message.reply_text(f"📍 Send a location pin for branch '{name}'.", reply_markup=kb)
    return ADMIN_WAIT_BRANCH_PIN

async def receive_branch_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lon, lat = update.message.location.longitude, update.message.location.latitude
    context.user_data['temp_branch_coords'] = (lon, lat)
    msg = await update.message.reply_text("🔍 Verifying address...", reply_markup=ReplyKeyboardRemove())
    address = await reverse_geocode(lon, lat)

    kb = [
        [InlineKeyboardButton("✅ Save Branch", callback_data='admin_loc_confirm')],
        [InlineKeyboardButton("🔄 Retry Pin", callback_data='admin_loc_retry')]
    ]
    await msg.edit_text(
        f"📍 *Address Found:*\n`{address}`\n\nIs this correct?",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode='Markdown'
    )
    return ADMIN_CONFIRM_BRANCH_PIN

async def save_branch_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name = context.user_data.get('temp_branch_name')
    lon, lat = context.user_data.get('temp_branch_coords')

    config.setdefault("branches", {})[name] = f"{lon},{lat}"
    await save_config(config)
    await query.edit_message_text(f"✅ Branch '{name}' saved successfully.")
    return ConversationHandler.END

async def retry_branch_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Send Current Location", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Please send a new location pin.",
        reply_markup=kb
    )
    return ADMIN_WAIT_BRANCH_PIN

# ==========================================
# 6. SYSTEM ADMINISTRATION PANEL
# ==========================================
async def sysadmin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """System administration panel. Restricted to the primary administrator."""
    user_id = update.effective_user.id
    if user_id != SUPER_ADMIN_ID:
        return

    cpu = psutil.cpu_percent(interval=0.1)
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent

    lic_data = license.ACTIVE_LICENSE if license.ACTIVE_LICENSE else {}
    client_name = lic_data.get("client_name", "UNKNOWN")
    expiry = (
        datetime.datetime.fromtimestamp(lic_data.get("exp", 0)).strftime('%Y-%m-%d')
        if "exp" in lic_data else "UNKNOWN"
    )

    text = (
        f"💻 *System Administration*\n━━━━━━━━━━━━━━━━━━━━\n"
        f"🏢 *Client:* {client_name}\n"
        f"🔑 *Licence Expiry:* {expiry}\n\n"
        f"⚙️ *Server Health:*\n"
        f"• CPU Usage: `{cpu}%`\n"
        f"• RAM Usage: `{ram}%`\n"
        f"• Disk Usage: `{disk}%`\n\n"
        f"⚠️ *Restricted Actions:* Proceed with care."
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data='sysadmin_refresh')],
        [InlineKeyboardButton("📦 Download Server Backup", callback_data='sysadmin_backup')],
        [InlineKeyboardButton("🛑 Uninstall Software", callback_data='sysadmin_uninstall')]
    ])

    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
        except telegram.error.BadRequest:
            pass
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode='Markdown')

async def sysadmin_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != SUPER_ADMIN_ID:
        return

    await query.answer("Generating backup archive...", show_alert=False)
    backup_filename = "server_backup.zip"

    files_to_backup = ['config.json', 'bot_data.pickle', 'stats.jsonl', 'license.key', 'hwid.lock', '.env']
    with zipfile.ZipFile(backup_filename, 'w') as zipf:
        for file in files_to_backup:
            if os.path.exists(file):
                zipf.write(file)

    try:
        with open(backup_filename, 'rb') as backup_file:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=backup_file,
                caption="📦 Server backup archive."
            )
    finally:
        if os.path.exists(backup_filename):
            os.remove(backup_filename)

async def sysadmin_uninstall_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != SUPER_ADMIN_ID:
        return
    await query.answer()

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm Uninstall", callback_data='sysadmin_uninstall_yes')],
        [InlineKeyboardButton("❌ Cancel", callback_data='sysadmin_refresh')]
    ])
    await query.edit_message_text(
        "⚠️ *Confirm Uninstall*\n\nThis will permanently delete all configuration, licence data, and stop the service. This action cannot be undone.",
        reply_markup=kb,
        parse_mode='Markdown'
    )

async def sysadmin_uninstall_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != SUPER_ADMIN_ID:
        return

    await query.edit_message_text("🔴 Uninstall in progress. Removing configuration files...")

    files_to_delete = ['config.json', 'bot_data.pickle', 'stats.jsonl', 'license.key', 'hwid.lock']
    for f in files_to_delete:
        if os.path.exists(f):
            os.remove(f)

    # FIX: Tell the admin to kill systemd instead of looping out
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🔴 System data removed. The configuration files have been deleted.\n\n⚠️ *CRITICAL:* Because the system uses auto-restart, you MUST log into your server terminal and run:\n`sudo systemctl stop logistics_bot`",
        parse_mode='Markdown'
    )
    os._exit(0)

# ==========================================
# 7. CONFIGURATION SETTERS
# ==========================================
async def set_numeric(update, context, key, name, type_cast=float):
    if not is_admin(update.effective_user.id):
        return
    try:
        val = type_cast(context.args[0])
        config[key] = val
        await save_config(config)
        await update.message.reply_text(f"✅ {name} updated to: {val}")
    except Exception:
        await update.message.reply_text(f"⚠️ Usage: /set{key.replace('_', '')} <number>")

async def cmd_setbasefare(u, c): await set_numeric(u, c, "base_fare", "Base Fare")
async def cmd_setfreekm(u, c): await set_numeric(u, c, "free_km", "Free KM Buffer")
async def cmd_setfuelprice(u, c): await set_numeric(u, c, "diesel_price_per_liter", "Fuel Price")
async def cmd_setsurge(u, c): await set_numeric(u, c, "surge_multiplier", "Surge Multiplier")
async def cmd_setdiscount(u, c): await set_numeric(u, c, "discount_percentage", "Discount %")
async def cmd_settaxrate(u, c): await set_numeric(u, c, "tax_rate_percentage", "Tax Rate %")
async def cmd_setfuelmargin(u, c): await set_numeric(u, c, "fuel_safety_margin", "Fuel Safety Margin")
async def cmd_setdriverthreshold(u, c): await set_numeric(u, c, "driver_distance_threshold_km", "Driver Distance Threshold")

async def cmd_ban(u, c):
    if not is_admin(u.effective_user.id):
        return
    try:
        uid = int(c.args[0])
        if uid not in config.setdefault('banned_users', []):
            config['banned_users'].append(uid)
            await save_config(config)
        await u.message.reply_text(f"✅ User {uid} has been restricted.")
    except Exception:
        pass

async def cmd_unban(u, c):
    if not is_admin(u.effective_user.id):
        return
    try:
        uid = int(c.args[0])
        if uid in config.get('banned_users', []):
            config['banned_users'].remove(uid)
            await save_config(config)
        await u.message.reply_text(f"✅ User {uid} has been unrestricted.")
    except Exception:
        pass