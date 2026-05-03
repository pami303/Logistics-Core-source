import math
import time
import asyncio
import copy
import random
import string
import json
import httpx
import telegram.error
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler

from config_manager import (
    config, save_config, is_banned, SUPER_ADMIN_ID,
    STATE_BROWSE_CATALOG, STATE_CONFIRM_LOCATION, STATE_CONFIRM_ORDER, STATS_FILE
)
from logistics_engine import get_mapbox_route, get_vehicle_tier, get_nearest_branch, is_working_hours, reverse_geocode

ITEMS_PER_PAGE = 6

# --- CART UTILITIES ---
def get_cart_total(cart): return sum(item['qty'] * item['price'] for item in cart.values())
def get_cart_volume(cart): return sum(item['qty'] * item['volume_cubes'] for item in cart.values())

# ==========================================
# FLOW 1: START & BROWSE
# ==========================================
async def start_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_banned(update.effective_user.id):
        return ConversationHandler.END
    context.user_data.setdefault('cart', {})

    radius = config.get("max_distance_km", 500.0)
    text = (
        f"{config.get('welcome_msg', 'Welcome!')}\n\n"
        f"🚚 *Notice | නිවේදනය:*\n"
        f"We currently deliver within a *{radius}km* radius. / අපගේ ප්‍රවාහන සේවාව *{radius}km* සීමාවකට යටත් වේ."
    )

    kb = [[InlineKeyboardButton("🛒 Shop Materials | භාණ්ඩ මිලදී ගන්න", callback_data='cmd_shop')]]

    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    return ConversationHandler.END

async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_working_hours():
        return await query.edit_message_text(
            f"😴 {config.get('maintenance_msg')}\nContact: {config.get('contact_info')}"
        )
    if not config.get("catalog"):
        return await query.edit_message_text("⚠️ The catalogue is currently empty. Please check back later.")

    page = int(query.data.split('page_cat_')[1]) if query.data.startswith('page_cat_') else 0
    categories = list(dict.fromkeys([item['category'] for item in config['catalog'].values()]))

    total_pages = math.ceil(len(categories) / ITEMS_PER_PAGE)
    current_cats = categories[page * ITEMS_PER_PAGE: (page + 1) * ITEMS_PER_PAGE]

    kb = [[InlineKeyboardButton(cat, callback_data=f'cat_{cat}')] for cat in current_cats]

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f'page_cat_{page - 1}'))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f'page_cat_{page + 1}'))
    if nav_row:
        kb.append(nav_row)

    cart_total = get_cart_total(context.user_data.get('cart', {}))
    if cart_total > 0:
        kb.append([InlineKeyboardButton(f"🛒 View Cart (Rs. {cart_total:,.2f})", callback_data='cmd_cart')])
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data='cmd_cancel')])

    await query.edit_message_text(
        "📂 *Select a Category | වර්ගය තෝරන්න:*",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode='Markdown'
    )
    return STATE_BROWSE_CATALOG

async def show_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith('cat_'):
        context.user_data['current_cat'] = query.data.split('cat_')[1]
        page = 0
    else:
        page = int(query.data.split('page_itm_')[1])

    category = context.user_data.get('current_cat')
    items = [(k, v) for k, v in config['catalog'].items() if v['category'] == category]

    total_pages = math.ceil(len(items) / ITEMS_PER_PAGE)
    current_items = items[page * ITEMS_PER_PAGE: (page + 1) * ITEMS_PER_PAGE]

    kb = []
    for i_id, item in current_items:
        btn_text = f"{item['name']} - Rs. {item['price']:,.2f}"
        kb.append([InlineKeyboardButton(btn_text, callback_data=f'itm_{i_id}')])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f'page_itm_{page - 1}'))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f'page_itm_{page + 1}'))
    if nav_row:
        kb.append(nav_row)

    kb.append([InlineKeyboardButton("🔙 Back to Categories", callback_data='page_cat_0')])
    await query.edit_message_text(
        f"🛠️ *{category}*\nSelect an item | භාණ්ඩයක් තෝරන්න:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode='Markdown'
    )
    return STATE_BROWSE_CATALOG

# ==========================================
# FLOW 2: INTERACTIVE QUANTITY SELECTOR
# ==========================================
def build_qty_keyboard(qty):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➖", callback_data='qty_dec_'),
            InlineKeyboardButton(f"{qty}", callback_data='noop'),
            InlineKeyboardButton("➕", callback_data='qty_inc_')
        ],
        [InlineKeyboardButton("✅ Add to Cart | එකතු කරන්න", callback_data='qty_add_')],
        [InlineKeyboardButton("🔙 Back", callback_data='page_itm_0')]
    ])

async def interactive_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    item_id = query.data.split('itm_')[1]

    item = config['catalog'].get(item_id)
    if not item:
        return await query.edit_message_text(
            "⚠️ This item is no longer available.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Browse", callback_data='cmd_shop')]])
        )

    context.user_data['selected_item'] = item_id
    context.user_data['temp_qty'] = 1

    text = f"📦 **{item['name']}**\n💰 Rs. {item['price']:,.2f}\n\nSelect quantity | ප්‍රමාණය තෝරන්න:"

    if item.get('photo_file_id'):
        try:
            await query.delete_message()
        except telegram.error.BadRequest:
            pass
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=item['photo_file_id'],
            caption=text,
            parse_mode='Markdown',
            reply_markup=build_qty_keyboard(1)
        )
    else:
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=build_qty_keyboard(1))

    return STATE_BROWSE_CATALOG

async def adjust_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    current_qty = context.user_data.get('temp_qty', 1)

    if query.data == 'qty_inc_':
        current_qty += 1
    elif query.data == 'qty_dec_' and current_qty > 1:
        current_qty -= 1

    context.user_data['temp_qty'] = current_qty
    try:
        await query.edit_message_reply_markup(reply_markup=build_qty_keyboard(current_qty))
    except telegram.error.BadRequest:
        pass  # Ignore "message not modified" errors
    await query.answer()
    return STATE_BROWSE_CATALOG

async def confirm_add_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    item_id = context.user_data.get('selected_item')
    qty = context.user_data.get('temp_qty', 1)

    catalog_item = config['catalog'].get(item_id)
    if not catalog_item:
        return await query.answer("This item is no longer available.", show_alert=True)

    cart = context.user_data.setdefault('cart', {})
    current_qty = cart.get(item_id, {}).get('qty', 0)

    if current_qty + qty > int(catalog_item.get('stock', 999999)):
        return await query.answer(
            f"Insufficient stock. Only {catalog_item.get('stock')} units available.",
            show_alert=True
        )

    cart[item_id] = {
        'name': catalog_item['name'],
        'price': catalog_item['price'],
        'volume_cubes': catalog_item['volume_cubes'],
        'qty': current_qty + qty
    }

    await query.answer(f"✅ {qty}x {catalog_item['name']} added to your cart.", show_alert=False)

    try:
        await query.delete_message()
    except telegram.error.BadRequest:
        pass

    kb = [
        [InlineKeyboardButton("🔙 Continue Shopping", callback_data='cmd_shop')],
        [InlineKeyboardButton(f"🛒 View Cart (Rs. {get_cart_total(cart):,.2f})", callback_data='cmd_cart')]
    ]
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="Item added to your cart.",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return STATE_BROWSE_CATALOG

# ==========================================
# FLOW 3: CART VIEW
# ==========================================
async def view_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    cart = context.user_data.get('cart', {})
    kb = []

    if not cart:
        text = "🛒 *Your cart is currently empty.*\nඅපගේ භාණ්ඩ නාමාවලියට පිවිසෙන්න!"
        kb.append([InlineKeyboardButton("📂 Browse Catalogue", callback_data='cmd_shop')])
    else:
        text = "🛒 *Your Cart | ඔබේ කූඩය*\n```\nItem                 Qty    Total\n---------------------------------\n"
        for i_id, item in cart.items():
            line_price = item['qty'] * item['price']
            name_short = item['name'][:18].ljust(20)
            text += f"{name_short} {str(item['qty']).ljust(4)} {line_price:,.0f}\n"
            kb.append([InlineKeyboardButton(f"❌ Remove {item['name']}", callback_data=f'rm_itm_{i_id}')])

        text += f"---------------------------------\nTotal (Rs.):        {get_cart_total(cart):,.2f}```\n"
        kb.append([InlineKeyboardButton("✅ Checkout | මුදල් ගෙවන්න", callback_data='cmd_checkout')])
        kb.append([InlineKeyboardButton("🔙 Continue Shopping", callback_data='cmd_shop')])

    markup = InlineKeyboardMarkup(kb)
    if query:
        await query.edit_message_text(text, reply_markup=markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode='Markdown')
    return STATE_BROWSE_CATALOG

async def remove_cart_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    i_id = query.data.split('rm_itm_')[1]
    context.user_data.get('cart', {}).pop(i_id, None)
    return await view_cart(update, context)

async def cmd_cart_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await view_cart(update, context)

# ==========================================
# FLOW 4: LOCATION & DELIVERY VERIFICATION
# ==========================================
async def start_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    if not context.user_data.get('cart'):
        return await view_cart(update, context)

    loc_kb = ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Share Delivery Location", request_location=True)], ["❌ Cancel"]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

    if query:
        try:
            await query.delete_message()
        except telegram.error.BadRequest:
            pass

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="📍 Please share your delivery location using the button below.\nකරුණාකර පහත බොත්තම භාවිතා කර ඔබගේ ස්ථානය ලබා දෙන්න:",
        reply_markup=loc_kb
    )
    return STATE_CONFIRM_LOCATION

async def receive_and_verify_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lon, lat = update.message.location.longitude, update.message.location.latitude
    context.user_data['temp_coords'] = (lon, lat)

    msg = await update.message.reply_text(
        "🔍 Verifying delivery address... | ස්ථානය පරීක්ෂා කරමින්...",
        reply_markup=ReplyKeyboardRemove()
    )

    address = await reverse_geocode(lon, lat)

    text = (
        f"🏠 *Delivery Address:*\n`{address}`\n\n"
        f"Is this the correct drop-off location?\nමේ ඔබගේ නිවැරදි ස්ථානයද?"
    )
    kb = [
        [InlineKeyboardButton("✅ Yes, Calculate Quote", callback_data='loc_confirm')],
        [InlineKeyboardButton("🔄 No, Send New Pin", callback_data='loc_retry')],
        [InlineKeyboardButton("❌ Cancel", callback_data='cmd_cancel')]
    ]

    await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    return STATE_CONFIRM_LOCATION

# ==========================================
# FLOW 5: QUOTATION & ORDER CONFIRMATION
# ==========================================
async def generate_hybrid_quote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    msg = await query.edit_message_text("🛣️ Calculating delivery route... | මාර්ගය ගණනය කරමින්...")

    now = time.time()
    if now - context.user_data.get('last_checkout_ts', 0) < 10:
        return await msg.edit_text("⚠️ Please wait a moment before retrying.")
    context.user_data['last_checkout_ts'] = now

    try:
        cart = context.user_data.get('cart', {})
        lon, lat = context.user_data['temp_coords']
        branch_name, branch_coords = get_nearest_branch(lon, lat)

        # FIX: Check for missing branches to prevent Mapbox API crashing
        if not branch_coords:
             raise ValueError("No delivery branches are currently available to service this area.")

        total_vol = get_cart_volume(cart)
        
        # FIX: Utilize the unused max_capacity_cubes variable
        if total_vol > config.get("max_capacity_cubes", 100.0):
             raise ValueError(f"Order volume ({total_vol:.1f} cubes) exceeds maximum single-delivery capacity.")

        await asyncio.sleep(0.5)
        await msg.edit_text("🧾 Preparing your quotation... | බිල්පත සකස් කරමින්...")

        total_dist_km = await get_mapbox_route(branch_coords, f"{lon},{lat}")

        if total_dist_km > config.get("max_distance_km", 500.0):
            raise ValueError(f"Delivery distance ({total_dist_km:.1f}km) exceeds our service radius.")

        goods_subtotal = get_cart_total(cart)

        free_km = config.get("free_km", 10.0)
        chargeable_dist = max(0.0, total_dist_km - free_km)

        shift_threshold = config.get("driver_distance_threshold_km", 50.0)
        driver_multiplier = 0 if chargeable_dist <= 0 else math.floor(chargeable_dist / shift_threshold) + 1
        driver_pay = driver_multiplier * config.get("driver_hourly_rate", 0)

        tier = get_vehicle_tier(max(0.001, total_vol))
        fuel_margin = config.get("fuel_safety_margin", 1.2)
        fuel_cost = fuel_margin * (chargeable_dist / tier["fuel_efficiency_kmpl"]) * config.get("diesel_price_per_liter", 0)

        transport_subtotal = (
            config.get("base_fare", 0) + driver_pay + fuel_cost + config.get("additional_fee", 0)
        )
        transport_surged = transport_subtotal * config.get("surge_multiplier", 1.0)

        raw_total = goods_subtotal + transport_surged
        discount_amt = raw_total * (config.get("discount_percentage", 0) / 100.0)
        tax_amt = (raw_total - discount_amt) * (config.get("tax_rate_percentage", 0) / 100.0)
        grand_total = (raw_total - discount_amt) + tax_amt

        receipt = "🧾 *Quotation | නිල බිල්පත*\n━━━━━━━━━━━━━━━━━━━━\n"
        receipt += f"📦 *Goods Subtotal:* Rs. {goods_subtotal:,.2f}\n"
        receipt += f"🚚 *Vehicle:* {tier['name']} ({total_dist_km:.1f} km)\n"
        receipt += f"🛣️ *Delivery Fee:* Rs. {transport_surged:,.2f}\n"

        if discount_amt > 0:
            receipt += f"🎉 *Discount:* -Rs. {discount_amt:,.2f}\n"
        if tax_amt > 0:
            receipt += f"🏛️ *Tax:* Rs. {tax_amt:,.2f}\n"

        receipt += f"━━━━━━━━━━━━━━━━━━━━\n💰 *Grand Total | මුළු මුදල: Rs. {grand_total:,.2f}*\n━━━━━━━━━━━━━━━━━━━━"

        context.user_data['pending_order'] = {
            'receipt': receipt,
            'grand_total': grand_total,
            'cart': copy.deepcopy(cart),
            'coords': f"{lon},{lat}",
            'dist': total_dist_km
        }

        kb = [
            [InlineKeyboardButton("✅ Confirm Order | තහවුරු කරන්න", callback_data='cmd_confirm_order')],
            [InlineKeyboardButton("❌ Cancel | අවලංගු කරන්න", callback_data='cmd_cancel')]
        ]

        await msg.edit_text(receipt, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        return STATE_CONFIRM_ORDER

    except httpx.TimeoutException:
        return await msg.edit_text(
            "⚠️ Network timeout. Please retry your location.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Retry", callback_data='loc_retry')]])
        )
    except ValueError as e:
        return await msg.edit_text(
            f"⚠️ {str(e)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛒 View Cart", callback_data='cmd_cart')]])
        )
    except Exception as e:
        return await msg.edit_text(
            "⚠️ An error occurred while calculating the route. Please verify your location or try again later.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Cart", callback_data='cmd_cart')]])
        )

async def confirm_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    order_data = context.user_data.get('pending_order')
    if not order_data:
        return await query.edit_message_text(
            "Your session has expired. Please use /start to begin a new order."
        )

    # Stock validation before committing
    for i_id, c_item in order_data['cart'].items():
        catalog_item = config['catalog'].get(i_id)
        if not catalog_item or int(catalog_item.get('stock', 0)) < c_item['qty']:
            await query.edit_message_text(
                f"⚠️ '{c_item['name']}' is out of stock or is no longer available. "
                f"Please remove the affected item from your cart and place a new order."
            )
            return ConversationHandler.END

    # Deduct stock
    for i_id, c_item in order_data['cart'].items():
        config['catalog'][i_id]['stock'] -= c_item['qty']

        if config['catalog'][i_id]['stock'] <= config.get('low_stock_threshold', 5):
            alert = (
                f"🚨 *Low Stock Alert*\n"
                f"Item: {c_item['name']}\n"
                f"Remaining: {config['catalog'][i_id]['stock']}"
            )
            for admin_id in config.get('admins', []) + [SUPER_ADMIN_ID]:
                try:
                    await context.bot.send_message(chat_id=admin_id, text=alert, parse_mode='Markdown')
                except Exception:
                    pass

    order_id = "ORD-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=5))

    config.setdefault('active_orders', {})[order_id] = {
        'user_id': update.effective_user.id,
        'username': update.effective_user.username,
        'total': order_data['grand_total'],
        'status': '🟡 Pending',
        'timestamp': time.time(),
        'receipt': order_data['receipt']
    }
    await save_config(config)

    # FIX: Use a proper function to prevent file handle leak in memory
    def write_stat():
        with open(STATS_FILE, 'a') as f:
            f.write(json.dumps({'order_id': order_id, 'total': order_data['grand_total'], 'timestamp': time.time()}) + '\n')
            
    await asyncio.to_thread(write_stat)

    # Notify admins
    admin_receipt = f"🔔 *New Order: {order_id}*\nUser: @{update.effective_user.username}\n\n{order_data['receipt']}"
    admin_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Accept", callback_data=f'ord_accept_{order_id}'),
            InlineKeyboardButton("❌ Reject", callback_data=f'ord_reject_{order_id}')
        ],
        [
            InlineKeyboardButton("🚚 Dispatch", callback_data=f'ord_dispatch_{order_id}'),
            InlineKeyboardButton("✅ Delivered", callback_data=f'ord_deliver_{order_id}')
        ]
    ])

    for admin_id in set(config.get('admins', []) + [SUPER_ADMIN_ID]):
        try:
            await context.bot.send_message(
                chat_id=admin_id, text=admin_receipt, parse_mode='Markdown', reply_markup=admin_kb
            )
        except Exception:
            pass

    final_receipt = (
        order_data['receipt'] +
        f"\n\n*✅ Order Confirmed*\n*Order ID:* `{order_id}`\nTrack using /orders\n_{config.get('receipt_footer', '')}_"
    )
    await query.edit_message_text(final_receipt, parse_mode='Markdown')

    context.user_data.pop('cart', None)
    context.user_data.pop('pending_order', None)
    return ConversationHandler.END

# ==========================================
# FLOW 6: ORDER TRACKING
# ==========================================
async def cmd_track_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays all active orders for the requesting user."""
    user_id = update.effective_user.id
    
    # FIX: Limit to most recent 15 to prevent Telegram 4096 char limit crash
    my_orders_list = sorted(
        [o for o in config.get('active_orders', {}).items() if o[1]['user_id'] == user_id],
        key=lambda x: x[1]['timestamp'], reverse=True
    )[:15]

    if not my_orders_list:
        return await update.message.reply_text("You have no active orders at this time.")

    text = "📦 *Your Active Orders (Recent)*\n━━━━━━━━━━━━━━━━━━━━\n"
    for oid, o in my_orders_list:
        date_str = time.strftime('%Y-%m-%d %H:%M', time.localtime(o['timestamp']))
        text += f"🔖 *{oid}* ({date_str})\nStatus: {o['status']}\nTotal: Rs. {o['total']:,.2f}\n\n"

    await update.message.reply_text(text, parse_mode='Markdown')

# ==========================================
# UTILS: CANCEL
# ==========================================
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # FIX: Clean up ALL dangling temporary memory variables
    for key in ['cart', 'pending_order', 'temp_coords', 'new_item', 'temp_branch_coords', 'current_cat', 'selected_item', 'temp_qty']:
        context.user_data.pop(key, None)
        
    text = "Session cancelled. / ක්‍රියාවලිය අවලංගු කරන ලදී."
    kb = [[InlineKeyboardButton("🛍️ Start Again", callback_data='start_bot')]]
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        m = await update.message.reply_text("...", reply_markup=ReplyKeyboardRemove())
        await m.delete()
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return ConversationHandler.END