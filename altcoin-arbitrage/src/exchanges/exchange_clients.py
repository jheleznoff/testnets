"""
Exchange-specific client implementations.
"""
from decimal import Decimal
from typing import Optional
import structlog

from .base_client import BaseExchangeClient
from ..core.models import AssetInfo, NetworkInfo, NetworkStatus

logger = structlog.get_logger()


class BinanceClient(BaseExchangeClient):
    """Binance exchange client."""

    async def _fetch_asset_info(self, symbol: str) -> Optional[AssetInfo]:
        """Fetch asset info from Binance including network details."""
        try:
            if not self._exchange or symbol not in self._currencies:
                return None

            currency = self._currencies[symbol]
            networks = []

            # Binance stores network info in currency['networks']
            network_list = currency.get("networks", {})

            for net_id, net_data in network_list.items():
                network = NetworkInfo(
                    network_id=net_id,
                    network_name=net_data.get("name", net_id),
                    chain=net_data.get("network", net_id),
                    withdraw_enabled=net_data.get("withdrawEnable", False),
                    withdraw_fee=self._to_decimal(net_data.get("withdrawFee", 0)),
                    withdraw_min=self._to_decimal(net_data.get("withdrawMin", 0)),
                    withdraw_max=self._to_decimal(net_data.get("withdrawMax")),
                    deposit_enabled=net_data.get("depositEnable", False),
                    deposit_min=self._to_decimal(net_data.get("depositMin")),
                    confirmations_required=net_data.get("minConfirm", 0),
                    estimated_arrival_minutes=net_data.get("estimatedArrivalTime", 0) // 60 if net_data.get("estimatedArrivalTime") else 5,
                    contract_address=net_data.get("contractAddress"),
                    status=NetworkStatus.ACTIVE if net_data.get("withdrawEnable") or net_data.get("depositEnable") else NetworkStatus.SUSPENDED
                )
                networks.append(network)

            return AssetInfo(
                symbol=symbol,
                name=currency.get("name", symbol),
                exchange_id=self.exchange_id,
                networks=networks,
                trading_enabled=currency.get("trading", True),
                is_active=currency.get("active", True)
            )

        except Exception as e:
            logger.error(f"Failed to fetch asset info from Binance: {e}")
            return None


class BybitClient(BaseExchangeClient):
    """Bybit exchange client."""

    async def _fetch_asset_info(self, symbol: str) -> Optional[AssetInfo]:
        """Fetch asset info from Bybit."""
        try:
            if not self._exchange:
                return None

            # Bybit may require specific API call for coin info
            try:
                coin_info = await self._exchange.fetch_currencies()
                currency = coin_info.get(symbol, {})
            except Exception:
                currency = self._currencies.get(symbol, {})

            if not currency:
                return None

            networks = []
            network_list = currency.get("networks", {})

            for net_id, net_data in network_list.items():
                network = NetworkInfo(
                    network_id=net_id,
                    network_name=net_data.get("name", net_id),
                    chain=net_data.get("chain", net_id),
                    withdraw_enabled=net_data.get("withdraw", False),
                    withdraw_fee=self._to_decimal(net_data.get("fee", 0)),
                    withdraw_min=self._to_decimal(net_data.get("withdrawMin", 0)),
                    deposit_enabled=net_data.get("deposit", False),
                    confirmations_required=net_data.get("minConfirm", 1),
                    estimated_arrival_minutes=5,
                    status=NetworkStatus.ACTIVE if net_data.get("withdraw") or net_data.get("deposit") else NetworkStatus.SUSPENDED
                )
                networks.append(network)

            return AssetInfo(
                symbol=symbol,
                name=currency.get("name", symbol),
                exchange_id=self.exchange_id,
                networks=networks,
                trading_enabled=True,
                is_active=currency.get("active", True)
            )

        except Exception as e:
            logger.error(f"Failed to fetch asset info from Bybit: {e}")
            return None


class OKXClient(BaseExchangeClient):
    """OKX exchange client."""

    async def _fetch_asset_info(self, symbol: str) -> Optional[AssetInfo]:
        """Fetch asset info from OKX."""
        try:
            if not self._exchange:
                return None

            currency = self._currencies.get(symbol, {})
            if not currency:
                return None

            networks = []
            network_list = currency.get("networks", {})

            for net_id, net_data in network_list.items():
                network = NetworkInfo(
                    network_id=net_id,
                    network_name=net_data.get("name", net_id),
                    chain=net_data.get("chain", net_id),
                    withdraw_enabled=net_data.get("withdraw", False),
                    withdraw_fee=self._to_decimal(net_data.get("fee", 0)),
                    withdraw_min=self._to_decimal(net_data.get("withdrawMin", 0)),
                    withdraw_max=self._to_decimal(net_data.get("withdrawMax")),
                    deposit_enabled=net_data.get("deposit", False),
                    deposit_min=self._to_decimal(net_data.get("depositMin")),
                    confirmations_required=net_data.get("minConfirm", 1),
                    estimated_arrival_minutes=net_data.get("estArrivalTime", 5),
                    contract_address=net_data.get("contractAddr"),
                    status=NetworkStatus.ACTIVE if net_data.get("withdraw") or net_data.get("deposit") else NetworkStatus.SUSPENDED
                )
                networks.append(network)

            return AssetInfo(
                symbol=symbol,
                name=currency.get("name", symbol),
                exchange_id=self.exchange_id,
                networks=networks,
                trading_enabled=True,
                is_active=currency.get("active", True)
            )

        except Exception as e:
            logger.error(f"Failed to fetch asset info from OKX: {e}")
            return None


class KuCoinClient(BaseExchangeClient):
    """KuCoin exchange client."""

    async def _fetch_asset_info(self, symbol: str) -> Optional[AssetInfo]:
        """Fetch asset info from KuCoin."""
        try:
            if not self._exchange:
                return None

            currency = self._currencies.get(symbol, {})
            if not currency:
                return None

            networks = []
            network_list = currency.get("networks", currency.get("chains", {}))

            if isinstance(network_list, dict):
                for net_id, net_data in network_list.items():
                    network = NetworkInfo(
                        network_id=net_id,
                        network_name=net_data.get("name", net_id),
                        chain=net_data.get("chainName", net_id),
                        withdraw_enabled=net_data.get("isWithdrawEnabled", False),
                        withdraw_fee=self._to_decimal(net_data.get("withdrawalMinFee", 0)),
                        withdraw_min=self._to_decimal(net_data.get("withdrawalMinSize", 0)),
                        deposit_enabled=net_data.get("isDepositEnabled", False),
                        confirmations_required=net_data.get("confirms", 1),
                        estimated_arrival_minutes=5,
                        contract_address=net_data.get("contractAddress"),
                        status=NetworkStatus.ACTIVE if net_data.get("isWithdrawEnabled") or net_data.get("isDepositEnabled") else NetworkStatus.SUSPENDED
                    )
                    networks.append(network)

            return AssetInfo(
                symbol=symbol,
                name=currency.get("name", symbol),
                exchange_id=self.exchange_id,
                networks=networks,
                trading_enabled=currency.get("isTrading", True),
                is_active=currency.get("active", True)
            )

        except Exception as e:
            logger.error(f"Failed to fetch asset info from KuCoin: {e}")
            return None


class GateIOClient(BaseExchangeClient):
    """Gate.io exchange client."""

    async def _fetch_asset_info(self, symbol: str) -> Optional[AssetInfo]:
        """Fetch asset info from Gate.io."""
        try:
            if not self._exchange:
                return None

            currency = self._currencies.get(symbol, {})
            if not currency:
                return None

            networks = []
            network_list = currency.get("networks", {})

            for net_id, net_data in network_list.items():
                network = NetworkInfo(
                    network_id=net_id,
                    network_name=net_data.get("name", net_id),
                    chain=net_data.get("chain", net_id),
                    withdraw_enabled=net_data.get("withdraw_disabled", True) is False,
                    withdraw_fee=self._to_decimal(net_data.get("withdraw_fee", 0)),
                    withdraw_min=self._to_decimal(net_data.get("withdraw_min", 0)),
                    deposit_enabled=net_data.get("deposit_disabled", True) is False,
                    confirmations_required=net_data.get("chain_confirms", 1),
                    estimated_arrival_minutes=5,
                    status=NetworkStatus.ACTIVE
                )
                networks.append(network)

            return AssetInfo(
                symbol=symbol,
                name=currency.get("name", symbol),
                exchange_id=self.exchange_id,
                networks=networks,
                trading_enabled=not currency.get("trade_disabled", False),
                is_active=currency.get("active", True)
            )

        except Exception as e:
            logger.error(f"Failed to fetch asset info from Gate.io: {e}")
            return None


class GenericClient(BaseExchangeClient):
    """Generic client for exchanges without specific implementation."""

    async def _fetch_asset_info(self, symbol: str) -> Optional[AssetInfo]:
        """Fetch asset info using generic ccxt interface."""
        try:
            if not self._exchange:
                return None

            currency = self._currencies.get(symbol, {})
            if not currency:
                return None

            networks = []

            # Try to extract network info from generic ccxt structure
            network_list = currency.get("networks", {})

            if network_list:
                for net_id, net_data in network_list.items():
                    # Handle both dict and other formats
                    if isinstance(net_data, dict):
                        network = NetworkInfo(
                            network_id=net_id,
                            network_name=net_data.get("name", net_id),
                            chain=net_data.get("chain", net_id),
                            withdraw_enabled=net_data.get("withdraw", True),
                            withdraw_fee=self._to_decimal(net_data.get("fee", 0)),
                            withdraw_min=self._to_decimal(net_data.get("withdrawMin", 0)),
                            deposit_enabled=net_data.get("deposit", True),
                            confirmations_required=net_data.get("confirms", 1),
                            estimated_arrival_minutes=5,
                            status=NetworkStatus.ACTIVE
                        )
                        networks.append(network)
            else:
                # Create a single default network based on the currency
                fee = self._to_decimal(currency.get("fee", 0))
                network = NetworkInfo(
                    network_id=symbol,
                    network_name=symbol,
                    chain=symbol,
                    withdraw_enabled=currency.get("withdraw", True),
                    withdraw_fee=fee,
                    withdraw_min=self._to_decimal(currency.get("withdrawMin", 0)),
                    deposit_enabled=currency.get("deposit", True),
                    status=NetworkStatus.ACTIVE
                )
                networks.append(network)

            return AssetInfo(
                symbol=symbol,
                name=currency.get("name", symbol),
                exchange_id=self.exchange_id,
                networks=networks,
                trading_enabled=currency.get("active", True),
                is_active=currency.get("active", True)
            )

        except Exception as e:
            logger.error(f"Failed to fetch asset info: {e}")
            return None


def create_exchange_client(
    exchange_id: str,
    api_key: str = "",
    api_secret: str = "",
    password: Optional[str] = None,
    sandbox: bool = False
) -> BaseExchangeClient:
    """Factory function to create appropriate exchange client."""

    client_map = {
        "binance": BinanceClient,
        "bybit": BybitClient,
        "okx": OKXClient,
        "kucoin": KuCoinClient,
        "gate": GateIOClient,
        "gateio": GateIOClient,
    }

    client_class = client_map.get(exchange_id.lower(), GenericClient)

    return client_class(
        exchange_id=exchange_id,
        api_key=api_key,
        api_secret=api_secret,
        password=password,
        sandbox=sandbox
    )
