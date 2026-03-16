diff --git a/app/bot/routers/__init__.py b/app/bot/routers/__init__.py
index 5f4d4526c60abcab841ca2202b38f2a7dd319a5d..961aee562c2f943c3a6d0db59c09f8c03ebd5b1b 100644
--- a/app/bot/routers/__init__.py
+++ b/app/bot/routers/__init__.py
@@ -1,42 +1,43 @@
 from aiogram import Dispatcher
 from aiohttp.web import Application
 
from app.bot.utils.constants import CONNECTION_WEBHOOK, MULTISERVER_SUBSCRIPTION_WEBHOOK
 
 from . import (
     admin_tools,
     download,
     main_menu,
     misc,
     profile,
     referral,
     subscription,
     support,
 )
 
 
 def include(app: Application, dispatcher: Dispatcher) -> None:
     app.router.add_get(CONNECTION_WEBHOOK, download.handler.redirect_to_connection)
     app.router.add_get(MULTISERVER_SUBSCRIPTION_WEBHOOK, download.handler.multiserver_subscription)
     dispatcher.include_routers(
         misc.error_handler.router,
         misc.notification_handler.router,
         main_menu.handler.router,
         profile.handler.router,
         referral.handler.router,
         support.handler.router,
         download.handler.router,
         subscription.subscription_handler.router,
         subscription.payment_handler.router,
         subscription.promocode_handler.router,
         subscription.trial_handler.router,
         admin_tools.admin_tools_handler.router,
         admin_tools.backup_handler.router,
         admin_tools.invites_handler.router,
         admin_tools.maintenance_handler.router,
         admin_tools.notification_handler.router,
         admin_tools.promocode_handler.router,
         admin_tools.restart_handler.router,
         admin_tools.server_handler.router,
         admin_tools.statistics_handler.router,
         admin_tools.user_handler.router,
     )
