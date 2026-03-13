import logging
from base64 import b64decode, b64encode

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.i18n import gettext as _
import aiohttp
from aiohttp.web import HTTPFound, Request, Response

from app.bot.models import ServicesContainer
from app.bot.utils.constants import (
    APP_ANDROID_SCHEME,
    APP_IOS_SCHEME,
    APP_WINDOWS_SCHEME,
    MAIN_MESSAGE_ID_KEY,
    PREVIOUS_CALLBACK_KEY,
)
from app.bot.utils.network import extract_base_url
from app.bot.utils.navigation import NavDownload, NavMain
from app.bot.utils.network import parse_redirect_url
from app.config import Config
from app.db.models import User
from app.db.models import Server, User

from .keyboard import download_keyboard, platforms_keyboard

logger = logging.getLogger(__name__)
router = Router(name=__name__)


def _decode_subscription(raw_data: str) -> list[str]:
    payload = raw_data.strip()
    if not payload:
        return []

    try:
        decoded = b64decode(payload + "===", validate=False).decode("utf-8")
        source = decoded
    except Exception:
        source = payload

    return [line.strip() for line in source.splitlines() if line.strip()]


def _encode_subscription(lines: list[str]) -> str:
    return b64encode("\n".join(lines).encode("utf-8")).decode("utf-8")


async def multiserver_subscription(request: Request) -> Response:
    vpn_id = request.match_info.get("vpn_id")
    if not vpn_id:
        return Response(status=400, text="Missing vpn_id.")

    session_factory = request.app.get("db_session")
    config: Config = request.app.get("config")
    if not session_factory or not config:
        return Response(status=500, text="Application context is not initialized.")

    async with session_factory() as session:
        user = await User.get_by_vpn_id(session=session, vpn_id=vpn_id)
        if not user:
            return Response(status=404, text="Subscription not found.")
        servers = await Server.get_all(session=session)

    urls = [
        f"{extract_base_url(server.host, config.xui.SUBSCRIPTION_PORT, config.xui.SUBSCRIPTION_PATH)}{vpn_id}"
        for server in servers
        if server.online
    ]

    if not urls:
        return Response(status=503, text="No online servers available.")

    configs: list[str] = []
    async with aiohttp.ClientSession() as client:
        for url in urls:
            try:
                async with client.get(url=url, ssl=False, timeout=10) as response:
                    if response.status != 200:
                        continue
                    body = await response.text()
                    configs.extend(_decode_subscription(body))
            except Exception as exception:
                logger.warning(f"Failed to fetch subscription from {url}: {exception}")

    unique_configs = list(dict.fromkeys(configs))
    if not unique_configs:
        return Response(status=404, text="No subscription configs found.")

    return Response(
        status=200,
        text=_encode_subscription(unique_configs),
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )


async def redirect_to_connection(request: Request) -> Response:
    query_string = request.query_string

    if not query_string:
        return Response(status=400, reason="Missing query string.")

    params = parse_redirect_url(query_string)
    scheme = params.get("scheme")
    key = params.get("key")

    if not scheme or not key:
        raise Response(status=400, reason="Invalid parameters.")

    redirect_url = f"{scheme}{key}"  # TODO: #namevpn
    if scheme in {
        APP_IOS_SCHEME,
        APP_ANDROID_SCHEME,
        APP_WINDOWS_SCHEME,
    }:
        raise HTTPFound(redirect_url)

    return Response(status=400, reason="Unsupported application.")

@router.callback_query(F.data == NavDownload.MAIN)
async def callback_download(callback: CallbackQuery, user: User, state: FSMContext) -> None:
    logger.info(f"User {user.tg_id} opened download apps page.")

    main_message_id = await state.get_value(MAIN_MESSAGE_ID_KEY)
    previous_callback = await state.get_value(PREVIOUS_CALLBACK_KEY)

    logger.debug("--------------------------------")
    logger.debug(f"callback.message.message_id: {callback.message.message_id}")
    logger.debug(f"main_message_id: {main_message_id}")
    logger.debug(f"previous_callback: {previous_callback}")
    logger.debug("--------------------------------")
    if callback.message.message_id != main_message_id:
        await state.update_data({PREVIOUS_CALLBACK_KEY: NavMain.MAIN_MENU})
        previous_callback = NavMain.MAIN_MENU
        await callback.bot.edit_message_text(
            text=_("download:message:choose_platform"),
            chat_id=user.tg_id,
            message_id=main_message_id,
            reply_markup=platforms_keyboard(previous_callback),
        )
    else:
        await callback.message.edit_text(
            text=_("download:message:choose_platform"),
            reply_markup=platforms_keyboard(previous_callback),
        )


@router.callback_query(F.data.startswith(NavDownload.PLATFORM))
async def callback_platform(
    callback: CallbackQuery,
    user: User,
    services: ServicesContainer,
    config: Config,
) -> None:
    logger.info(f"User {user.tg_id} selected platform: {callback.data}")
    key = await services.vpn.get_key(user)

    match callback.data:
        case NavDownload.PLATFORM_IOS:
            platform = _("download:message:platform_ios")
        case NavDownload.PLATFORM_ANDROID:
            platform = _("download:message:platform_android")
        case _:
            platform = _("download:message:platform_windows")

    await callback.message.edit_text(
        text=_("download:message:connect_to_vpn").format(platform=platform),
        reply_markup=download_keyboard(platform=callback.data, key=key, url=config.bot.DOMAIN),
    )
