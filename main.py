import logging
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ConversationHandler, PicklePersistence, CallbackQueryHandler
)

from config_manager import (
    TELEGRAM_TOKEN, PERSISTENCE_FILE,
    STATE_BROWSE_CATALOG, STATE_CONFIRM_LOCATION, STATE_CONFIRM_ORDER,
    ADMIN_WAIT_BRANCH_PIN, ADMIN_CONFIRM_BRANCH_PIN,
    ADMIN_ADD_PHOTO, ADMIN_ADD_NAME, ADMIN_ADD_PRICE,
    ADMIN_ADD_VOL, ADMIN_ADD_CAT, ADMIN_ADD_STOCK,
    ADMIN_EDIT_PRICE
)

import user_handlers as uh
import admin_handlers as ah

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

if __name__ == '__main__':
    # ==========================================
    # 1. LICENCE GATE — must run before bot connects
    # ==========================================
    import license
    license.init_license()

    # ==========================================
    # 2. SYSTEM INITIALISATION
    # ==========================================
    persistence = PicklePersistence(filepath=PERSISTENCE_FILE)
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).persistence(persistence).build()

    cancel_fallbacks = [
        CommandHandler("cancel", uh.cancel),
        CommandHandler("start", uh.start_bot),
        CallbackQueryHandler(uh.cancel, pattern='^cmd_cancel$'),
        MessageHandler(filters.Regex('^❌ Cancel$'), uh.cancel)
    ]

    # ==========================================
    # 3. CUSTOMER SHOPPING CONVERSATION
    # ==========================================
    shopping_conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", uh.start_bot),
            CommandHandler("cart", uh.cmd_cart_view),
            CallbackQueryHandler(uh.start_bot, pattern='^start_bot$'),
            CallbackQueryHandler(uh.show_categories, pattern='^cmd_shop$')
        ],
        states={
            STATE_BROWSE_CATALOG: [
                CallbackQueryHandler(uh.show_categories, pattern='^cmd_shop$'),
                CallbackQueryHandler(uh.show_categories, pattern='^page_cat_'),
                CallbackQueryHandler(uh.show_items, pattern='^cat_'),
                CallbackQueryHandler(uh.show_items, pattern='^page_itm_'),
                CallbackQueryHandler(uh.interactive_quantity, pattern='^itm_'),
                CallbackQueryHandler(uh.adjust_quantity, pattern='^qty_inc_'),
                CallbackQueryHandler(uh.adjust_quantity, pattern='^qty_dec_'),
                CallbackQueryHandler(uh.confirm_add_to_cart, pattern='^qty_add_'),
                CallbackQueryHandler(uh.view_cart, pattern='^cmd_cart$'),
                CallbackQueryHandler(uh.remove_cart_item, pattern='^rm_itm_'),
                CallbackQueryHandler(uh.start_checkout, pattern='^cmd_checkout$')
            ],
            STATE_CONFIRM_LOCATION: [
                MessageHandler(filters.LOCATION, uh.receive_and_verify_location),
                CallbackQueryHandler(uh.generate_hybrid_quote, pattern='^loc_confirm$'),
                CallbackQueryHandler(uh.start_checkout, pattern='^loc_retry$')
            ],
            STATE_CONFIRM_ORDER: [
                CallbackQueryHandler(uh.confirm_order, pattern='^cmd_confirm_order$')
            ]
        },
        fallbacks=cancel_fallbacks,
        name="shopping_conv",
        persistent=True
    )

    # ==========================================
    # 4. ADMIN WIZARD CONVERSATIONS
    # ==========================================
    branch_conv = ConversationHandler(
        entry_points=[CommandHandler("addbranch", ah.cmd_addbranch)],
        states={
            ADMIN_WAIT_BRANCH_PIN: [MessageHandler(filters.LOCATION, ah.receive_branch_pin)],
            ADMIN_CONFIRM_BRANCH_PIN: [
                CallbackQueryHandler(ah.save_branch_pin, pattern='^admin_loc_confirm$'),
                CallbackQueryHandler(ah.retry_branch_pin, pattern='^admin_loc_retry$')
            ]
        },
        fallbacks=cancel_fallbacks,
        name="branch_conv",
        persistent=True
    )

    admin_item_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ah.wizard_start_add_item, pattern='^admin_add_item$')],
        states={
            ADMIN_ADD_PHOTO: [MessageHandler(filters.PHOTO | filters.TEXT, ah.wizard_receive_photo)],
            ADMIN_ADD_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, ah.wizard_receive_name)],
            ADMIN_ADD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ah.wizard_receive_price)],
            ADMIN_ADD_VOL:   [MessageHandler(filters.TEXT & ~filters.COMMAND, ah.wizard_receive_vol)],
            ADMIN_ADD_CAT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, ah.wizard_receive_cat)],
            ADMIN_ADD_STOCK: [MessageHandler(filters.TEXT & ~filters.COMMAND, ah.wizard_receive_stock)]
        },
        fallbacks=cancel_fallbacks,
        name="admin_item_conv",
        persistent=True
    )

    admin_price_edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ah.start_price_edit, pattern='^edit_price_')],
        states={
            ADMIN_EDIT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ah.save_new_price)]
        },
        fallbacks=cancel_fallbacks,
        name="admin_price_edit_conv",
        persistent=True
    )

    # ==========================================
    # 5. HANDLER REGISTRATION
    # ==========================================
    app.add_handler(shopping_conv)
    app.add_handler(branch_conv)
    app.add_handler(admin_item_conv)
    app.add_handler(admin_price_edit_conv)

    app.add_handler(CommandHandler("orders", uh.cmd_track_orders))

    # Admin dashboard
    app.add_handler(CommandHandler("admin", ah.admin_dashboard))
    app.add_handler(CallbackQueryHandler(ah.admin_dashboard_callback, pattern='^admin_dash$'))
    app.add_handler(CallbackQueryHandler(ah.toggle_maintenance, pattern='^admin_toggle_maint$'))
    app.add_handler(CallbackQueryHandler(ah.view_paginated_catalog, pattern='^admin_view_cat$'))
    app.add_handler(CallbackQueryHandler(ah.view_paginated_catalog, pattern='^admin_cat_page_'))
    app.add_handler(CallbackQueryHandler(ah.remove_item_callback, pattern='^rm_admin_itm_'))
    app.add_handler(CallbackQueryHandler(ah.manage_order_status, pattern='^ord_accept_'))
    app.add_handler(CallbackQueryHandler(ah.manage_order_status, pattern='^ord_reject_'))
    app.add_handler(CallbackQueryHandler(ah.manage_order_status, pattern='^ord_dispatch_'))
    app.add_handler(CallbackQueryHandler(ah.manage_order_status, pattern='^ord_deliver_'))

    # System administration
    app.add_handler(CommandHandler("sysadmin", ah.sysadmin_panel))
    app.add_handler(CallbackQueryHandler(ah.sysadmin_panel, pattern='^sysadmin_refresh$'))
    app.add_handler(CallbackQueryHandler(ah.sysadmin_backup, pattern='^sysadmin_backup$'))
    app.add_handler(CallbackQueryHandler(ah.sysadmin_uninstall_confirm, pattern='^sysadmin_uninstall$'))
    app.add_handler(CallbackQueryHandler(ah.sysadmin_uninstall_execute, pattern='^sysadmin_uninstall_yes$'))

    # Configuration setters
    admin_setters = [
        ("setbasefare", ah.cmd_setbasefare),
        ("setfreekm", ah.cmd_setfreekm),
        ("setfuelprice", ah.cmd_setfuelprice),
        ("setsurge", ah.cmd_setsurge),
        ("setdiscount", ah.cmd_setdiscount),
        ("settaxrate", ah.cmd_settaxrate),
        ("setfuelmargin", ah.cmd_setfuelmargin),
        ("setdriverthreshold", ah.cmd_setdriverthreshold),
        ("ban", ah.cmd_ban),
        ("unban", ah.cmd_unban)
    ]
    for cmd_name, cmd_func in admin_setters:
        app.add_handler(CommandHandler(cmd_name, cmd_func))

    print("Logistics Engine bot is running.")
    app.run_polling()