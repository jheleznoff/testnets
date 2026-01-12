"""
Base exchange client with common functionality.
"""
import asyncio
import ccxt.async_support as ccxt
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Optional
from datetime import datetime
import structlog

from ..core.models import (
    AssetInfo, NetworkInfo, NetworkStatus, OrderBook, OrderBookEntry,
    TradingPair, TradeExecution, TransferExecution
)

logger = structlog.get_logger()


class BaseExchangeClient(ABC):
    """Base class for exchange clients."""

    def __init__(
        self,
        exchange_id: str,
        api_key: str = "",
        api_secret: str = "",
        password: Optional[str] = None,
        sandbox: bool = False
    ):
        self.exchange_id = exchange_id
        self.api_key = api_key
        self.api_secret = api_secret
        self.password = password
        self.sandbox = sandbox

        self._exchange: Optional[ccxt.Exchange] = None
        self._markets: dict = {}
        self._currencies: dict = {}
        self._assets_cache: dict[str, AssetInfo] = {}
        self._pairs_cache: dict[str, TradingPair] = {}
        self._last_cache_update: Optional[datetime] = None
        self._cache_ttl_seconds = 300  # 5 minutes

    async def initialize(self) -> bool:
        """Initialize connection to the exchange."""
        try:
            exchange_class = getattr(ccxt, self.exchange_id)

            config = {
                "enableRateLimit": True,
                "options": {
                    "defaultType": "spot",
                    "adjustForTimeDifference": True,
                }
            }

            if self.api_key:
                config["apiKey"] = self.api_key
            if self.api_secret:
                config["secret"] = self.api_secret
            if self.password:
                config["password"] = self.password

            self._exchange = exchange_class(config)

            if self.sandbox and hasattr(self._exchange, "set_sandbox_mode"):
                self._exchange.set_sandbox_mode(True)

            # Load markets
            self._markets = await self._exchange.load_markets()

            # Load currencies if available
            if hasattr(self._exchange, "currencies"):
                self._currencies = self._exchange.currencies

            logger.info(
                "Exchange initialized",
                exchange=self.exchange_id,
                markets_count=len(self._markets)
            )
            return True

        except Exception as e:
            logger.error(
                "Failed to initialize exchange",
                exchange=self.exchange_id,
                error=str(e)
            )
            return False

    async def close(self):
        """Close the exchange connection."""
        if self._exchange:
            await self._exchange.close()
            self._exchange = None

    @property
    def is_connected(self) -> bool:
        return self._exchange is not None

    async def get_trading_pairs(self, quote_currencies: list[str]) -> list[TradingPair]:
        """Get all active trading pairs for given quote currencies."""
        pairs = []

        for symbol, market in self._markets.items():
            if not market.get("active", True):
                continue

            quote = market.get("quote", "")
            if quote not in quote_currencies:
                continue

            base = market.get("base", "")

            pair = TradingPair(
                symbol=symbol,
                base=base,
                quote=quote,
                exchange_id=self.exchange_id,
                min_amount=self._to_decimal(market.get("limits", {}).get("amount", {}).get("min")),
                max_amount=self._to_decimal(market.get("limits", {}).get("amount", {}).get("max")),
                min_cost=self._to_decimal(market.get("limits", {}).get("cost", {}).get("min")),
                amount_precision=market.get("precision", {}).get("amount", 8) or 8,
                price_precision=market.get("precision", {}).get("price", 8) or 8,
                maker_fee=self._to_decimal(market.get("maker", 0.001)),
                taker_fee=self._to_decimal(market.get("taker", 0.001)),
                is_active=market.get("active", True),
            )

            pairs.append(pair)

        return pairs

    async def get_asset_info(self, symbol: str) -> Optional[AssetInfo]:
        """Get detailed asset information including networks."""
        # Check cache first
        if symbol in self._assets_cache:
            cached = self._assets_cache[symbol]
            if self._last_cache_update and \
               (datetime.utcnow() - self._last_cache_update).seconds < self._cache_ttl_seconds:
                return cached

        # Fetch fresh data
        asset_info = await self._fetch_asset_info(symbol)
        if asset_info:
            self._assets_cache[symbol] = asset_info
            self._last_cache_update = datetime.utcnow()

        return asset_info

    @abstractmethod
    async def _fetch_asset_info(self, symbol: str) -> Optional[AssetInfo]:
        """Exchange-specific implementation to fetch asset info."""
        pass

    async def get_all_assets_info(self) -> dict[str, AssetInfo]:
        """Get info for all available assets."""
        assets = {}

        for symbol in self._currencies.keys():
            info = await self.get_asset_info(symbol)
            if info:
                assets[symbol] = info

        return assets

    async def get_order_book(self, symbol: str, limit: int = 50) -> Optional[OrderBook]:
        """Fetch current order book for a trading pair."""
        try:
            if not self._exchange:
                return None

            ob_data = await self._exchange.fetch_order_book(symbol, limit)

            bids = [
                OrderBookEntry(price=Decimal(str(price)), amount=Decimal(str(amount)))
                for price, amount in ob_data.get("bids", [])
            ]
            asks = [
                OrderBookEntry(price=Decimal(str(price)), amount=Decimal(str(amount)))
                for price, amount in ob_data.get("asks", [])
            ]

            return OrderBook(
                exchange_id=self.exchange_id,
                symbol=symbol,
                bids=bids,
                asks=asks,
                timestamp=datetime.utcnow()
            )

        except Exception as e:
            logger.error(
                "Failed to fetch order book",
                exchange=self.exchange_id,
                symbol=symbol,
                error=str(e)
            )
            return None

    async def get_ticker(self, symbol: str) -> Optional[dict]:
        """Get ticker data for a symbol."""
        try:
            if not self._exchange:
                return None
            return await self._exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.warning(
                "Failed to fetch ticker",
                exchange=self.exchange_id,
                symbol=symbol,
                error=str(e)
            )
            return None

    async def get_balance(self, asset: str) -> Decimal:
        """Get available balance for an asset."""
        try:
            if not self._exchange:
                return Decimal("0")

            balance = await self._exchange.fetch_balance()
            asset_balance = balance.get(asset, {})
            return self._to_decimal(asset_balance.get("free", 0))

        except Exception as e:
            logger.error(
                "Failed to fetch balance",
                exchange=self.exchange_id,
                asset=asset,
                error=str(e)
            )
            return Decimal("0")

    async def place_market_order(
        self,
        symbol: str,
        side: str,
        amount: Decimal,
        opportunity_id: str = ""
    ) -> Optional[TradeExecution]:
        """Place a market order."""
        try:
            if not self._exchange:
                return None

            order = await self._exchange.create_order(
                symbol=symbol,
                type="market",
                side=side,
                amount=float(amount)
            )

            return TradeExecution(
                id=order["id"],
                opportunity_id=opportunity_id,
                exchange_id=self.exchange_id,
                side=side,
                symbol=symbol,
                amount=self._to_decimal(order.get("filled", amount)),
                price=self._to_decimal(order.get("average", order.get("price", 0))),
                cost=self._to_decimal(order.get("cost", 0)),
                fee=self._to_decimal(order.get("fee", {}).get("cost", 0)),
                order_id=order["id"],
                order_type="market",
                status=order.get("status", "filled"),
                expected_price=Decimal("0"),  # Will be set by caller
                actual_slippage_percent=Decimal("0")  # Will be calculated
            )

        except Exception as e:
            logger.error(
                "Failed to place market order",
                exchange=self.exchange_id,
                symbol=symbol,
                side=side,
                amount=str(amount),
                error=str(e)
            )
            return None

    async def withdraw(
        self,
        asset: str,
        amount: Decimal,
        address: str,
        network: str,
        opportunity_id: str = ""
    ) -> Optional[TransferExecution]:
        """Initiate a withdrawal."""
        try:
            if not self._exchange:
                return None

            params = {"network": network}

            withdrawal = await self._exchange.withdraw(
                asset,
                float(amount),
                address,
                params=params
            )

            return TransferExecution(
                id=withdrawal.get("id", ""),
                opportunity_id=opportunity_id,
                from_exchange=self.exchange_id,
                to_exchange="",  # Will be set by caller
                asset=asset,
                network=network,
                amount=self._to_decimal(withdrawal.get("amount", amount)),
                fee=self._to_decimal(withdrawal.get("fee", {}).get("cost", 0)),
                tx_hash=withdrawal.get("txid"),
                deposit_address=address,
                status="pending"
            )

        except Exception as e:
            logger.error(
                "Failed to withdraw",
                exchange=self.exchange_id,
                asset=asset,
                amount=str(amount),
                network=network,
                error=str(e)
            )
            return None

    async def get_deposit_address(self, asset: str, network: str) -> Optional[str]:
        """Get deposit address for an asset on a specific network."""
        try:
            if not self._exchange:
                return None

            address_info = await self._exchange.fetch_deposit_address(
                asset,
                params={"network": network}
            )

            return address_info.get("address")

        except Exception as e:
            logger.error(
                "Failed to get deposit address",
                exchange=self.exchange_id,
                asset=asset,
                network=network,
                error=str(e)
            )
            return None

    @staticmethod
    def _to_decimal(value) -> Decimal:
        """Convert a value to Decimal safely."""
        if value is None:
            return Decimal("0")
        try:
            return Decimal(str(value))
        except Exception:
            return Decimal("0")
