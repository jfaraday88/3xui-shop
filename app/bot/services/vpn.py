from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .server_pool import Connection, ServerPoolService

import logging
from urllib.parse import urljoin

from py3xui import AsyncApi, Client, Inbound
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.bot.models import ClientData
from app.bot.utils.constants import MULTISERVER_SUBSCRIPTION_WEBHOOK
from app.bot.utils.time import (
    add_days_to_timestamp,
    days_to_timestamp,
    get_current_timestamp,
)
from app.config import Config
from app.db.models import Promocode, User

logger = logging.getLogger(__name__)


class VPNService:
    def __init__(
        self,
        config: Config,
        session: async_sessionmaker,
        server_pool_service: ServerPoolService,
    ) -> None:
        self.config = config
        self.session = session
        self.server_pool_service = server_pool_service
        logger.info("VPN Service initialized.")

    async def is_client_exists(self, user: User) -> Client | None:
        connection = await self.server_pool_service.get_connection(user)

        if not connection:
            return None

        client = await connection.api.client.get_by_email(str(user.tg_id))

        if client:
            logger.debug(f"Client {user.tg_id} exists on server {connection.server.name}.")
        else:
            logger.critical(f"Client {user.tg_id} not found on server {connection.server.name}.")

        return client

    async def get_limit_ip(self, user: User, client: Client) -> int | None:
        connection = await self.server_pool_service.get_connection(user)

        if not connection:
            return None

        return await self._get_limit_ip_from_api(api=connection.api, client=client)

    async def get_client_data(self, user: User) -> ClientData | None:
        logger.debug(f"Starting to retrieve client data for {user.tg_id}.")

        connection = await self.server_pool_service.get_connection(user)

        if not connection:
            return None

        try:
            client = await connection.api.client.get_by_email(str(user.tg_id))

            if not client:
                logger.critical(
                    f"Client {user.tg_id} not found on server {connection.server.name}."
                )
                return None

            limit_ip = await self.get_limit_ip(user=user, client=client)
            max_devices = -1 if limit_ip == 0 else limit_ip
            traffic_total = client.total
            expiry_time = -1 if client.expiry_time == 0 else client.expiry_time

            if traffic_total <= 0:
                traffic_remaining = -1
                traffic_total = -1
            else:
                traffic_remaining = client.total - (client.up + client.down)

            traffic_used = client.up + client.down
            client_data = ClientData(
                max_devices=max_devices,
                traffic_total=traffic_total,
                traffic_remaining=traffic_remaining,
                traffic_used=traffic_used,
                traffic_up=client.up,
                traffic_down=client.down,
                expiry_time=expiry_time,
            )
            logger.debug(f"Successfully retrieved client data for {user.tg_id}: {client_data}.")
            return client_data
        except Exception as exception:
            logger.error(f"Error retrieving client data for {user.tg_id}: {exception}")
            return None

    async def get_key(self, user: User) -> str | None:
        async with self.session() as session:
            user = await User.get(session=session, tg_id=user.tg_id)

        if not user:
            return None

        if not user.vpn_id:
            logger.debug(f"VPN ID for user {user.tg_id} not found.")
            return None

        key = urljoin(self.config.bot.DOMAIN, MULTISERVER_SUBSCRIPTION_WEBHOOK).replace(
            "{vpn_id}", user.vpn_id
        )
        logger.debug(f"Fetched key for {user.tg_id}: {key}.")
        return key

    async def _get_client_from_api(self, api: AsyncApi, user: User) -> Client | None:
        try:
            return await api.client.get_by_email(str(user.tg_id))
        except Exception as exception:
            logger.error(f"Error getting client {user.tg_id}: {exception}")
            return None

    async def _get_limit_ip_from_api(self, api: AsyncApi, client: Client) -> int | None:
        try:
            inbounds: list[Inbound] = await api.inbound.get_list()
        except Exception as exception:
            logger.error(f"Failed to fetch inbounds: {exception}")
            return None

        for inbound in inbounds:
            for inbound_client in inbound.settings.clients:
                if inbound_client.email == client.email:
                    logger.debug(f"Client {client.email} limit ip: {inbound_client.limit_ip}")
                    return inbound_client.limit_ip

        logger.critical(f"Client {client.email} not found in inbounds.")
        return None

    async def _create_client_on_api(
        self,
        api: AsyncApi,
        user: User,
        devices: int,
        duration: int,
        enable: bool = True,
        flow: str = "xtls-rprx-vision",
        total_gb: int = 0,
    ) -> bool:
        inbound_id = await self.server_pool_service.get_inbound_id(api)
        if inbound_id is None:
            logger.error("Failed to get inbound id for client creation.")
            return False

        new_client = Client(
            email=str(user.tg_id),
            enable=enable,
            id=user.vpn_id,
            expiry_time=days_to_timestamp(duration),
            flow=flow,
            limit_ip=devices,
            sub_id=user.vpn_id,
            total_gb=total_gb,
        )

        try:
            await api.client.add(inbound_id=inbound_id, clients=[new_client])
            return True
        except Exception as exception:
            logger.error(f"Error creating client for {user.tg_id}: {exception}")
            return False

    async def _update_client_on_api(
        self,
        api: AsyncApi,
        user: User,
        devices: int,
        duration: int,
        replace_devices: bool = False,
        replace_duration: bool = False,
        enable: bool = True,
        flow: str = "xtls-rprx-vision",
        total_gb: int = 0,
    ) -> bool:
        try:
            client = await self._get_client_from_api(api=api, user=user)

            if client is None:
                logger.critical(f"Client {user.tg_id} not found for update.")
                return False

            if not replace_devices:
                current_device_limit = await self._get_limit_ip_from_api(api=api, client=client)
                if current_device_limit is None:
                    logger.error(f"Could not resolve current device limit for {user.tg_id}.")
                    return False
                devices = current_device_limit + devices

            current_time = get_current_timestamp()

            if not replace_duration:
                expiry_time_to_use = max(client.expiry_time, current_time)
            else:
                expiry_time_to_use = current_time

            expiry_time = add_days_to_timestamp(timestamp=expiry_time_to_use, days=duration)

            client.enable = enable
            client.id = user.vpn_id
            client.expiry_time = expiry_time
            client.flow = flow
            client.limit_ip = devices
            client.sub_id = user.vpn_id
            client.total_gb = total_gb

            await api.client.update(client_uuid=client.id, client=client)
            return True
        except Exception as exception:
            logger.error(f"Error updating client {user.tg_id}: {exception}")
            return False

    async def create_client(
        self,
        user: User,
        devices: int,
        duration: int,
        enable: bool = True,
        flow: str = "xtls-rprx-vision",
        total_gb: int = 0,
        inbound_id: int = 1,
    ) -> bool:
        logger.info(f"Creating new client {user.tg_id} | {devices} devices {duration} days.")

        await self.server_pool_service.assign_server_to_user(user)
        connection = await self.server_pool_service.get_connection(user)

        if not connection:
            return False

        created = await self._create_client_on_api(
            api=connection.api,
            user=user,
            devices=devices,
            duration=duration,
            enable=enable,
            flow=flow,
            total_gb=total_gb,
        )

        if created:
            logger.info(f"Successfully created client for {user.tg_id}")

        return created

    async def create_clients_on_all_servers(
        self,
        user: User,
        devices: int,
        duration: int,
        enable: bool = True,
        flow: str = "xtls-rprx-vision",
        total_gb: int = 0,
    ) -> bool:
        connections = await self.server_pool_service.get_online_connections()

        if not connections:
            logger.error("No online servers found to create clients.")
            return False

        success_count = 0
        for connection in connections:
            exists = await self._get_client_from_api(api=connection.api, user=user)
            if exists:
                logger.debug(
                    f"Client {user.tg_id} already exists on server {connection.server.name}, skipping create."
                )
                success_count += 1
                continue

            created = await self._create_client_on_api(
                api=connection.api,
                user=user,
                devices=devices,
                duration=duration,
                enable=enable,
                flow=flow,
                total_gb=total_gb,
            )
            if created:
                success_count += 1

        logger.info(
            f"Created or found client {user.tg_id} on {success_count}/{len(connections)} servers."
        )
        return success_count > 0

    async def _upsert_client_on_connections(
        self,
        user: User,
        devices: int,
        duration: int,
        replace_devices: bool,
        replace_duration: bool,
        enable: bool = True,
        flow: str = "xtls-rprx-vision",
        total_gb: int = 0,
    ) -> bool:
        connections = await self.server_pool_service.get_online_connections()

        if not connections:
            logger.error("No online servers found to upsert clients.")
            return False

        success_count = 0
        for connection in connections:
            existing_client = await self._get_client_from_api(api=connection.api, user=user)
            if existing_client:
                updated = await self._update_client_on_api(
                    api=connection.api,
                    user=user,
                    devices=devices,
                    duration=duration,
                    replace_devices=replace_devices,
                    replace_duration=replace_duration,
                    enable=enable,
                    flow=flow,
                    total_gb=total_gb,
                )
                if updated:
                    success_count += 1
                continue

            created = await self._create_client_on_api(
                api=connection.api,
                user=user,
                devices=devices,
                duration=duration,
                enable=enable,
                flow=flow,
                total_gb=total_gb,
            )
            if created:
                success_count += 1

        logger.info(f"Upserted client {user.tg_id} on {success_count}/{len(connections)} servers.")
        return success_count > 0

    async def update_client(
        self,
        user: User,
        devices: int,
        duration: int,
        replace_devices: bool = False,
        replace_duration: bool = False,
        enable: bool = True,
        flow: str = "xtls-rprx-vision",
        total_gb: int = 0,
    ) -> bool:
        logger.info(f"Updating client {user.tg_id} | {devices} devices {duration} days.")
        connection = await self.server_pool_service.get_connection(user)

        if not connection:
            return False

        updated = await self._update_client_on_api(
            api=connection.api,
            user=user,
            devices=devices,
            duration=duration,
            replace_devices=replace_devices,
            replace_duration=replace_duration,
            enable=enable,
            flow=flow,
            total_gb=total_gb,
        )

        if updated:
            logger.info(f"Client {user.tg_id} updated successfully.")

        return updated

    async def update_client_on_all_servers(
        self,
        user: User,
        devices: int,
        duration: int,
        replace_devices: bool = False,
        replace_duration: bool = False,
        enable: bool = True,
        flow: str = "xtls-rprx-vision",
        total_gb: int = 0,
    ) -> bool:
        return await self._upsert_client_on_connections(
            user=user,
            devices=devices,
            duration=duration,
            replace_devices=replace_devices,
            replace_duration=replace_duration,
            enable=enable,
            flow=flow,
            total_gb=total_gb,
        )

    async def create_subscription(self, user: User, devices: int, duration: int) -> bool:
        created = await self.create_clients_on_all_servers(
            user=user,
            devices=devices,
            duration=duration,
        )

        if created and not user.server_id:
            await self.server_pool_service.assign_server_to_user(user)

        return created

    async def extend_subscription(self, user: User, devices: int, duration: int) -> bool:
        return await self.update_client_on_all_servers(
            user=user,
            devices=devices,
            duration=duration,
            replace_devices=True,
        )

    async def change_subscription(self, user: User, devices: int, duration: int) -> bool:
        return await self.update_client_on_all_servers(
            user=user,
            devices=devices,
            duration=duration,
            replace_devices=True,
            replace_duration=True,
        )

    async def process_bonus_days(self, user: User, duration: int, devices: int) -> bool:
        return await self._upsert_client_on_connections(
            user=user,
            devices=devices,
            duration=duration,
            replace_devices=False,
            replace_duration=False,
        )

    async def activate_promocode(self, user: User, promocode: Promocode) -> bool:
        # TODO: consider moving to some 'promocode module services' with usage of vpn-service methods.

        async with self.session() as session:
            activated = await Promocode.set_activated(
                session=session,
                code=promocode.code,
                user_id=user.tg_id,
            )

        if not activated:
            logger.critical(f"Failed to activate promocode {promocode.code} for user {user.tg_id}.")
            return False

        logger.info(f"Begun applying promocode ({promocode.code}) to a client {user.tg_id}.")
        success = await self.process_bonus_days(
            user,
            duration=promocode.duration,
            devices=self.config.shop.BONUS_DEVICES_COUNT,
        )

        if success:
            return True

        async with self.session() as session:
            await Promocode.set_deactivated(session=session, code=promocode.code)

        logger.warning(f"Promocode {promocode.code} not activated due to failure.")
        return False
