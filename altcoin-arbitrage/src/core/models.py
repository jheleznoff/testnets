"""
Core data models for the arbitrage trading system.
"""
from pydantic import BaseModel, Field
from decimal import Decimal
from typing import Optional
from datetime import datetime
from enum import Enum


class NetworkStatus(str, Enum):
    """Status of a blockchain network for deposits/withdrawals."""
    ACTIVE = "active"
    SUSPENDED = "suspended"
    MAINTENANCE = "maintenance"
    UNKNOWN = "unknown"


class NetworkInfo(BaseModel):
    """Information about a blockchain network for a specific asset."""
    network_id: str  # e.g., "ERC20", "BEP20", "TRC20"
    network_name: str  # Human-readable name
    chain: Optional[str] = None  # Blockchain name

    # Withdrawal info
    withdraw_enabled: bool = False
    withdraw_fee: Decimal = Decimal("0")
    withdraw_min: Decimal = Decimal("0")
    withdraw_max: Optional[Decimal] = None

    # Deposit info
    deposit_enabled: bool = False
    deposit_min: Optional[Decimal] = None

    # Confirmation info
    confirmations_required: int = 0
    estimated_arrival_minutes: int = 0

    # Contract address for tokens
    contract_address: Optional[str] = None

    # Network status
    status: NetworkStatus = NetworkStatus.UNKNOWN


class AssetInfo(BaseModel):
    """Information about an asset on an exchange."""
    symbol: str  # e.g., "ETH"
    name: str  # e.g., "Ethereum"
    exchange_id: str

    # Available networks for this asset
    networks: list[NetworkInfo] = Field(default_factory=list)

    # Trading status
    trading_enabled: bool = True
    is_active: bool = True

    # Last update time
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def get_common_networks(self, other: "AssetInfo") -> list[tuple[NetworkInfo, NetworkInfo]]:
        """Find networks that both exchanges support for this asset."""
        common = []
        for net1 in self.networks:
            for net2 in other.networks:
                # Normalize network names for comparison
                net1_normalized = self._normalize_network(net1.network_id)
                net2_normalized = self._normalize_network(net2.network_id)

                if net1_normalized == net2_normalized:
                    if net1.withdraw_enabled and net2.deposit_enabled:
                        common.append((net1, net2))
        return common

    @staticmethod
    def _normalize_network(network_id: str) -> str:
        """Normalize network identifiers for comparison."""
        network_id = network_id.upper()
        mappings = {
            "ERC20": "ETH",
            "ETH": "ETH",
            "ETHEREUM": "ETH",
            "BEP20": "BSC",
            "BSC": "BSC",
            "BINANCE SMART CHAIN": "BSC",
            "TRC20": "TRX",
            "TRX": "TRX",
            "TRON": "TRX",
            "POLYGON": "MATIC",
            "MATIC": "MATIC",
            "ARBITRUM ONE": "ARB",
            "ARBITRUM": "ARB",
            "ARB": "ARB",
            "OPTIMISM": "OP",
            "OP": "OP",
            "AVAX C-CHAIN": "AVAX",
            "AVAXC": "AVAX",
            "AVAX": "AVAX",
            "SOL": "SOL",
            "SOLANA": "SOL",
            "BASE": "BASE",
        }
        return mappings.get(network_id, network_id)


class OrderBookEntry(BaseModel):
    """Single entry in an order book."""
    price: Decimal
    amount: Decimal

    @property
    def total(self) -> Decimal:
        return self.price * self.amount


class OrderBook(BaseModel):
    """Order book snapshot."""
    exchange_id: str
    symbol: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    bids: list[OrderBookEntry] = Field(default_factory=list)  # Buy orders (sorted high to low)
    asks: list[OrderBookEntry] = Field(default_factory=list)  # Sell orders (sorted low to high)

    def get_best_bid(self) -> Optional[OrderBookEntry]:
        return self.bids[0] if self.bids else None

    def get_best_ask(self) -> Optional[OrderBookEntry]:
        return self.asks[0] if self.asks else None

    def get_executable_buy_volume(self, max_price: Decimal) -> Decimal:
        """Get total volume available to buy up to max_price."""
        total = Decimal("0")
        for ask in self.asks:
            if ask.price <= max_price:
                total += ask.amount
            else:
                break
        return total

    def get_executable_sell_volume(self, min_price: Decimal) -> Decimal:
        """Get total volume available to sell down to min_price."""
        total = Decimal("0")
        for bid in self.bids:
            if bid.price >= min_price:
                total += bid.amount
            else:
                break
        return total

    def calculate_average_buy_price(self, amount: Decimal) -> Optional[Decimal]:
        """Calculate average price to buy a specific amount."""
        if amount <= 0:
            return None

        remaining = amount
        total_cost = Decimal("0")

        for ask in self.asks:
            if remaining <= 0:
                break
            fill_amount = min(remaining, ask.amount)
            total_cost += fill_amount * ask.price
            remaining -= fill_amount

        if remaining > 0:
            return None  # Not enough liquidity

        return total_cost / amount

    def calculate_average_sell_price(self, amount: Decimal) -> Optional[Decimal]:
        """Calculate average price to sell a specific amount."""
        if amount <= 0:
            return None

        remaining = amount
        total_received = Decimal("0")

        for bid in self.bids:
            if remaining <= 0:
                break
            fill_amount = min(remaining, bid.amount)
            total_received += fill_amount * bid.price
            remaining -= fill_amount

        if remaining > 0:
            return None  # Not enough liquidity

        return total_received / amount


class TradingPair(BaseModel):
    """Trading pair information."""
    symbol: str  # e.g., "ETH/USDT"
    base: str  # e.g., "ETH"
    quote: str  # e.g., "USDT"
    exchange_id: str

    # Trading limits
    min_amount: Optional[Decimal] = None
    max_amount: Optional[Decimal] = None
    min_cost: Optional[Decimal] = None
    amount_precision: int = 8
    price_precision: int = 8

    # Fees
    maker_fee: Decimal = Decimal("0.001")  # 0.1%
    taker_fee: Decimal = Decimal("0.001")  # 0.1%

    # Status
    is_active: bool = True

    # 24h volume
    volume_24h: Optional[Decimal] = None
    volume_24h_usd: Optional[Decimal] = None


class ArbitrageOpportunity(BaseModel):
    """Detected arbitrage opportunity."""
    id: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    detected_at: datetime = Field(default_factory=datetime.utcnow)

    # Asset info
    base_asset: str  # e.g., "ETH"
    quote_asset: str  # e.g., "USDT"

    # Exchange info
    buy_exchange: str
    sell_exchange: str

    # Network for transfer
    transfer_network: str
    transfer_network_name: str

    # Prices
    buy_price: Decimal  # Price to buy on buy_exchange
    sell_price: Decimal  # Price to sell on sell_exchange
    gross_spread_percent: Decimal  # (sell - buy) / buy * 100

    # Volumes
    max_executable_amount: Decimal  # Limited by order book liquidity
    trade_amount: Decimal  # Actual amount to trade
    trade_value_usd: Decimal

    # Fees breakdown
    buy_exchange_fee: Decimal  # Trading fee on buy exchange
    sell_exchange_fee: Decimal  # Trading fee on sell exchange
    withdrawal_fee: Decimal  # In base asset
    withdrawal_fee_usd: Decimal
    network_fee_estimate: Decimal = Decimal("0")  # Additional network fee if any
    deposit_fee: Decimal = Decimal("0")  # Deposit fee if any

    total_fees_usd: Decimal

    # Net profit
    net_profit_usd: Decimal
    net_profit_percent: Decimal

    # Timing
    estimated_transfer_minutes: int
    confirmations_required: int

    # Risk assessment
    risk_score: Decimal = Decimal("0")  # 0-100, higher is riskier
    risk_factors: list[str] = Field(default_factory=list)

    # Status
    is_valid: bool = True
    invalidation_reason: Optional[str] = None


class TradeExecution(BaseModel):
    """Record of an executed trade."""
    id: str
    opportunity_id: str
    executed_at: datetime = Field(default_factory=datetime.utcnow)

    # Trade details
    exchange_id: str
    side: str  # "buy" or "sell"
    symbol: str
    amount: Decimal
    price: Decimal
    cost: Decimal
    fee: Decimal

    # Order info
    order_id: str
    order_type: str  # "market" or "limit"
    status: str  # "filled", "partial", "failed"

    # Slippage
    expected_price: Decimal
    actual_slippage_percent: Decimal


class TransferExecution(BaseModel):
    """Record of an executed transfer."""
    id: str
    opportunity_id: str
    initiated_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    # Transfer details
    from_exchange: str
    to_exchange: str
    asset: str
    network: str
    amount: Decimal
    fee: Decimal

    # Transaction info
    tx_hash: Optional[str] = None
    deposit_address: str
    status: str  # "pending", "confirming", "completed", "failed"

    # Tracking
    confirmations: int = 0
    confirmations_required: int = 0


class ArbitrageResult(BaseModel):
    """Complete result of an arbitrage operation."""
    opportunity: ArbitrageOpportunity
    buy_trade: Optional[TradeExecution] = None
    transfer: Optional[TransferExecution] = None
    sell_trade: Optional[TradeExecution] = None

    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    # Final P&L
    realized_profit_usd: Optional[Decimal] = None
    expected_profit_usd: Decimal
    profit_variance_percent: Optional[Decimal] = None

    status: str = "pending"  # "pending", "in_progress", "completed", "failed"
    failure_reason: Optional[str] = None
