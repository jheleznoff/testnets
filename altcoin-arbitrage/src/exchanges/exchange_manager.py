"""
Multi-exchange manager for coordinating operations across exchanges.
"""
import asyncio
from decimal import Decimal
from typing import Optional
from datetime import datetime
import structlog

from .exchange_clients import create_exchange_client, BaseExchangeClient
from ..core.models import AssetInfo, TradingPair, OrderBook
from config.settings import settings, load_exchange_credentials

logger = structlog.get_logger()


class ExchangeManager:
    """Manages connections and operations across multiple exchanges."""

    def __init__(self, exchange_ids: Optional[list[str]] = None):
        self.exchange_ids = exchange_ids or settings.supported_exchanges
        self._clients: dict[str, BaseExchangeClient] = {}
        self._initialized = False

    async def initialize(self) -> bool:
        """Initialize all exchange connections."""
        logger.info("Initializing exchange connections", exchanges=self.exchange_ids)

        init_tasks = []
        for exchange_id in self.exchange_ids:
            creds = load_exchange_credentials(exchange_id)
            client = create_exchange_client(
                exchange_id=exchange_id,
                api_key=creds.api_key,
                api_secret=creds.api_secret,
                password=creds.password,
                sandbox=creds.sandbox
            )
            self._clients[exchange_id] = client
            init_tasks.append(client.initialize())

        results = await asyncio.gather(*init_tasks, return_exceptions=True)

        successful = 0
        for exchange_id, result in zip(self.exchange_ids, results):
            if isinstance(result, Exception):
                logger.error(
                    "Failed to initialize exchange",
                    exchange=exchange_id,
                    error=str(result)
                )
            elif result:
                successful += 1

        self._initialized = successful > 0
        logger.info(
            "Exchange initialization complete",
            successful=successful,
            total=len(self.exchange_ids)
        )

        return self._initialized

    async def close(self):
        """Close all exchange connections."""
        close_tasks = [client.close() for client in self._clients.values()]
        await asyncio.gather(*close_tasks, return_exceptions=True)
        self._clients.clear()
        self._initialized = False

    def get_client(self, exchange_id: str) -> Optional[BaseExchangeClient]:
        """Get a specific exchange client."""
        return self._clients.get(exchange_id)

    def get_active_exchanges(self) -> list[str]:
        """Get list of successfully connected exchanges."""
        return [
            exchange_id
            for exchange_id, client in self._clients.items()
            if client.is_connected
        ]

    async def get_all_trading_pairs(self) -> dict[str, list[TradingPair]]:
        """Get trading pairs from all exchanges."""
        result = {}

        async def fetch_pairs(exchange_id: str):
            client = self._clients.get(exchange_id)
            if client and client.is_connected:
                try:
                    pairs = await client.get_trading_pairs(settings.quote_currencies)
                    return exchange_id, pairs
                except Exception as e:
                    logger.error(f"Failed to get pairs from {exchange_id}: {e}")
            return exchange_id, []

        tasks = [fetch_pairs(ex) for ex in self.get_active_exchanges()]
        results = await asyncio.gather(*tasks)

        for exchange_id, pairs in results:
            if pairs:
                result[exchange_id] = pairs

        return result

    async def get_common_pairs(self) -> dict[str, list[str]]:
        """Find pairs that exist on multiple exchanges.

        Returns:
            Dict mapping base asset to list of exchanges that support it
        """
        all_pairs = await self.get_all_trading_pairs()

        # Map: base_asset -> {quote_asset -> [exchanges]}
        pair_map: dict[str, dict[str, list[str]]] = {}

        for exchange_id, pairs in all_pairs.items():
            for pair in pairs:
                if pair.base not in pair_map:
                    pair_map[pair.base] = {}
                if pair.quote not in pair_map[pair.base]:
                    pair_map[pair.base][pair.quote] = []
                pair_map[pair.base][pair.quote].append(exchange_id)

        # Filter to pairs on at least 2 exchanges
        common = {}
        for base, quotes in pair_map.items():
            for quote, exchanges in quotes.items():
                if len(exchanges) >= 2:
                    key = f"{base}/{quote}"
                    common[key] = exchanges

        return common

    async def get_asset_info_all_exchanges(self, symbol: str) -> dict[str, AssetInfo]:
        """Get asset info from all exchanges that support it."""
        result = {}

        async def fetch_info(exchange_id: str):
            client = self._clients.get(exchange_id)
            if client and client.is_connected:
                try:
                    info = await client.get_asset_info(symbol)
                    return exchange_id, info
                except Exception as e:
                    logger.warning(f"Failed to get asset info from {exchange_id}: {e}")
            return exchange_id, None

        tasks = [fetch_info(ex) for ex in self.get_active_exchanges()]
        results = await asyncio.gather(*tasks)

        for exchange_id, info in results:
            if info and info.networks:
                result[exchange_id] = info

        return result

    async def get_order_books(
        self,
        symbol: str,
        exchanges: Optional[list[str]] = None,
        limit: int = 50
    ) -> dict[str, OrderBook]:
        """Fetch order books from multiple exchanges in parallel."""
        target_exchanges = exchanges or self.get_active_exchanges()
        result = {}

        async def fetch_ob(exchange_id: str):
            client = self._clients.get(exchange_id)
            if client and client.is_connected:
                try:
                    ob = await client.get_order_book(symbol, limit)
                    return exchange_id, ob
                except Exception as e:
                    logger.warning(f"Failed to get order book from {exchange_id}: {e}")
            return exchange_id, None

        tasks = [fetch_ob(ex) for ex in target_exchanges]
        results = await asyncio.gather(*tasks)

        for exchange_id, ob in results:
            if ob:
                result[exchange_id] = ob

        return result

    async def get_tickers(
        self,
        symbol: str,
        exchanges: Optional[list[str]] = None
    ) -> dict[str, dict]:
        """Fetch tickers from multiple exchanges."""
        target_exchanges = exchanges or self.get_active_exchanges()
        result = {}

        async def fetch_ticker(exchange_id: str):
            client = self._clients.get(exchange_id)
            if client and client.is_connected:
                try:
                    ticker = await client.get_ticker(symbol)
                    return exchange_id, ticker
                except Exception:
                    pass
            return exchange_id, None

        tasks = [fetch_ticker(ex) for ex in target_exchanges]
        results = await asyncio.gather(*tasks)

        for exchange_id, ticker in results:
            if ticker:
                result[exchange_id] = ticker

        return result

    async def find_arbitrageable_assets(self) -> dict[str, dict]:
        """Find all assets that can be arbitraged between exchanges.

        Returns dict with:
        - base_asset: str
        - quote_asset: str
        - exchanges: list of exchange IDs
        - network_routes: list of possible transfer networks
        """
        logger.info("Scanning for arbitrageable assets...")

        # Get all common pairs
        common_pairs = await self.get_common_pairs()

        # Filter by minimum volume and blacklist
        filtered_pairs = {}
        for pair, exchanges in common_pairs.items():
            base, quote = pair.split("/")

            if base in settings.blacklisted_tokens:
                continue

            filtered_pairs[pair] = exchanges

        logger.info(f"Found {len(filtered_pairs)} pairs on multiple exchanges")

        # For each pair, check deposit/withdrawal capabilities
        arbitrageable = {}

        for pair, exchanges in filtered_pairs.items():
            base, quote = pair.split("/")

            # Get asset info from all supporting exchanges
            asset_info_map = await self.get_asset_info_all_exchanges(base)

            if len(asset_info_map) < 2:
                continue

            # Find network routes between exchange pairs
            network_routes = []

            exchange_list = list(asset_info_map.keys())
            for i, ex1 in enumerate(exchange_list):
                for ex2 in exchange_list[i + 1:]:
                    info1 = asset_info_map[ex1]
                    info2 = asset_info_map[ex2]

                    # Find common networks
                    common_networks = info1.get_common_networks(info2)

                    for net1, net2 in common_networks:
                        network_routes.append({
                            "from_exchange": ex1,
                            "to_exchange": ex2,
                            "network": net1.network_id,
                            "withdraw_fee": net1.withdraw_fee,
                            "confirmations": max(net1.confirmations_required, net2.confirmations_required),
                        })

                    # Also check reverse direction
                    common_networks_reverse = info2.get_common_networks(info1)
                    for net2, net1 in common_networks_reverse:
                        network_routes.append({
                            "from_exchange": ex2,
                            "to_exchange": ex1,
                            "network": net2.network_id,
                            "withdraw_fee": net2.withdraw_fee,
                            "confirmations": max(net1.confirmations_required, net2.confirmations_required),
                        })

            if network_routes:
                arbitrageable[pair] = {
                    "base": base,
                    "quote": quote,
                    "exchanges": exchanges,
                    "asset_info": asset_info_map,
                    "network_routes": network_routes
                }

        logger.info(f"Found {len(arbitrageable)} arbitrageable assets with valid transfer routes")

        return arbitrageable
