"""
Configuration settings for the arbitrage trading system.
"""
from pydantic import BaseModel, Field
from typing import Optional
from decimal import Decimal
import os
from dotenv import load_dotenv

load_dotenv()


class ExchangeCredentials(BaseModel):
    """API credentials for an exchange."""
    api_key: str = ""
    api_secret: str = ""
    password: Optional[str] = None  # For exchanges like OKX
    sandbox: bool = False


class RiskSettings(BaseModel):
    """Risk management settings."""
    max_position_size_usd: Decimal = Field(default=Decimal("1000"))
    min_profit_threshold_percent: Decimal = Field(default=Decimal("0.5"))
    max_transfer_time_minutes: int = Field(default=30)
    max_price_slippage_percent: Decimal = Field(default=Decimal("0.3"))
    min_liquidity_multiplier: Decimal = Field(default=Decimal("3"))  # Order book depth >= 3x trade size
    max_network_congestion_multiplier: Decimal = Field(default=Decimal("2"))  # Max gas price multiplier


class NetworkSettings(BaseModel):
    """Blockchain network settings."""
    # Average confirmation times in minutes
    confirmation_times: dict[str, int] = Field(default_factory=lambda: {
        "ETH": 5,
        "ERC20": 5,
        "BSC": 1,
        "BEP20": 1,
        "TRX": 1,
        "TRC20": 1,
        "SOL": 1,
        "MATIC": 2,
        "POLYGON": 2,
        "AVAX": 1,
        "AVAXC": 1,
        "ARB": 2,
        "ARBITRUM": 2,
        "OP": 2,
        "OPTIMISM": 2,
        "BASE": 2,
        "FTM": 1,
        "FANTOM": 1,
        "ALGO": 1,
        "XRP": 1,
        "XLM": 1,
        "LTC": 15,
        "DOGE": 20,
        "BTC": 60,
    })

    # Required confirmations by network (approximate)
    required_confirmations: dict[str, int] = Field(default_factory=lambda: {
        "ETH": 12,
        "ERC20": 12,
        "BSC": 15,
        "BEP20": 15,
        "TRX": 20,
        "TRC20": 20,
        "SOL": 32,
        "MATIC": 128,
        "POLYGON": 128,
        "AVAX": 20,
        "AVAXC": 20,
        "ARB": 12,
        "ARBITRUM": 12,
        "OP": 12,
        "OPTIMISM": 12,
        "BASE": 12,
        "LTC": 6,
        "BTC": 3,
    })


class TradingSettings(BaseModel):
    """Trading execution settings."""
    use_market_orders: bool = True
    order_timeout_seconds: int = 30
    max_retries: int = 3
    retry_delay_seconds: int = 5
    dry_run: bool = True  # Simulate trades without execution


class AppSettings(BaseModel):
    """Main application settings."""
    # Supported exchanges (auto-discovered, not configured)
    supported_exchanges: list[str] = Field(default_factory=lambda: [
        "binance",
        "bybit",
        "okx",
        "kucoin",
        "gate",
        "htx",
        "mexc",
        "bitget",
    ])

    # Scan interval in seconds
    scan_interval_seconds: int = 10

    # Quote currencies to consider for pairs
    quote_currencies: list[str] = Field(default_factory=lambda: ["USDT", "USDC", "BUSD"])

    # Minimum 24h volume in USD to consider a pair
    min_volume_24h_usd: Decimal = Field(default=Decimal("100000"))

    # Blacklisted tokens (known scam or problematic tokens)
    blacklisted_tokens: list[str] = Field(default_factory=lambda: [
        "LUNA", "UST", "FTT"
    ])

    risk: RiskSettings = Field(default_factory=RiskSettings)
    network: NetworkSettings = Field(default_factory=NetworkSettings)
    trading: TradingSettings = Field(default_factory=TradingSettings)


def load_exchange_credentials(exchange_id: str) -> ExchangeCredentials:
    """Load credentials for a specific exchange from environment variables."""
    prefix = exchange_id.upper()
    return ExchangeCredentials(
        api_key=os.getenv(f"{prefix}_API_KEY", ""),
        api_secret=os.getenv(f"{prefix}_API_SECRET", ""),
        password=os.getenv(f"{prefix}_PASSWORD"),
        sandbox=os.getenv(f"{prefix}_SANDBOX", "false").lower() == "true"
    )


# Global settings instance
settings = AppSettings()
