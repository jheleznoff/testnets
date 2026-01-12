"""
Profit calculator for arbitrage opportunities.
Considers all fees: trading, withdrawal, network, and deposit fees.
"""
from decimal import Decimal
from typing import Optional
from dataclasses import dataclass
import structlog

from ..core.models import TradingPair, NetworkInfo, ArbitrageOpportunity
from .orderbook_analyzer import SlippageEstimate

logger = structlog.get_logger()


@dataclass
class FeeBreakdown:
    """Detailed breakdown of all fees in an arbitrage trade."""

    # Trading fees (in quote currency, e.g., USDT)
    buy_trading_fee: Decimal = Decimal("0")
    sell_trading_fee: Decimal = Decimal("0")

    # Withdrawal fee (in base asset, e.g., ETH)
    withdrawal_fee: Decimal = Decimal("0")
    withdrawal_fee_in_quote: Decimal = Decimal("0")

    # Network/gas fee estimate (in base asset)
    network_fee: Decimal = Decimal("0")
    network_fee_in_quote: Decimal = Decimal("0")

    # Deposit fee (rare, but some exchanges charge it)
    deposit_fee: Decimal = Decimal("0")
    deposit_fee_in_quote: Decimal = Decimal("0")

    # Totals
    total_fees_in_quote: Decimal = Decimal("0")

    def calculate_total(self, base_price: Decimal) -> Decimal:
        """Calculate total fees in quote currency."""
        self.withdrawal_fee_in_quote = self.withdrawal_fee * base_price
        self.network_fee_in_quote = self.network_fee * base_price
        self.deposit_fee_in_quote = self.deposit_fee * base_price

        self.total_fees_in_quote = (
            self.buy_trading_fee +
            self.sell_trading_fee +
            self.withdrawal_fee_in_quote +
            self.network_fee_in_quote +
            self.deposit_fee_in_quote
        )

        return self.total_fees_in_quote


@dataclass
class ProfitCalculation:
    """Result of profit calculation for an arbitrage opportunity."""

    # Trade parameters
    trade_amount: Decimal  # Amount of base asset to trade
    buy_price: Decimal  # Effective buy price (after slippage)
    sell_price: Decimal  # Effective sell price (after slippage)

    # Gross values
    buy_cost: Decimal  # Total cost to buy (in quote)
    sell_proceeds: Decimal  # Total proceeds from sell (in quote)
    gross_profit: Decimal  # Before fees
    gross_profit_percent: Decimal

    # Fee breakdown
    fees: FeeBreakdown

    # Net values
    net_profit: Decimal  # After all fees
    net_profit_percent: Decimal

    # Amount received after withdrawal fee
    amount_after_withdrawal: Decimal

    # Is profitable
    is_profitable: bool = False

    # Breakeven analysis
    breakeven_spread_percent: Decimal = Decimal("0")


class ProfitCalculator:
    """Calculates profit for arbitrage opportunities including all fees."""

    def __init__(self):
        pass

    def calculate_profit(
        self,
        trade_amount: Decimal,
        buy_price: Decimal,
        sell_price: Decimal,
        buy_pair: TradingPair,
        sell_pair: TradingPair,
        withdrawal_network: NetworkInfo,
        deposit_network: Optional[NetworkInfo] = None,
        network_congestion_multiplier: Decimal = Decimal("1")
    ) -> ProfitCalculation:
        """
        Calculate net profit for an arbitrage trade.

        Args:
            trade_amount: Amount of base asset to trade
            buy_price: Price to buy at (effective price after slippage)
            sell_price: Price to sell at (effective price after slippage)
            buy_pair: Trading pair info on buy exchange (for fees)
            sell_pair: Trading pair info on sell exchange (for fees)
            withdrawal_network: Network info for withdrawal
            deposit_network: Network info for deposit (if different fees)
            network_congestion_multiplier: Multiplier for network fees during congestion

        Returns:
            ProfitCalculation with detailed breakdown
        """
        fees = FeeBreakdown()

        # Calculate buy cost
        buy_cost = trade_amount * buy_price

        # Buy trading fee (taker fee assumed for market orders)
        fees.buy_trading_fee = buy_cost * buy_pair.taker_fee

        # Withdrawal fee (in base asset)
        fees.withdrawal_fee = withdrawal_network.withdraw_fee

        # Apply congestion multiplier to network fee if applicable
        # Some networks have variable fees based on congestion
        fees.network_fee = Decimal("0")  # Most exchanges include this in withdrawal fee

        # Amount that actually arrives at destination exchange
        amount_after_withdrawal = trade_amount - fees.withdrawal_fee

        # Deposit fee (if any)
        if deposit_network and hasattr(deposit_network, 'deposit_fee'):
            fees.deposit_fee = Decimal("0")  # Most exchanges don't charge deposit fees

        # Calculate sell proceeds (with reduced amount)
        sell_proceeds = amount_after_withdrawal * sell_price

        # Sell trading fee
        fees.sell_trading_fee = sell_proceeds * sell_pair.taker_fee

        # Calculate total fees
        avg_price = (buy_price + sell_price) / Decimal("2")
        fees.calculate_total(avg_price)

        # Gross profit (before fees)
        gross_profit = sell_proceeds - buy_cost
        gross_profit_percent = (gross_profit / buy_cost) * Decimal("100") if buy_cost > 0 else Decimal("0")

        # Net profit (after all fees)
        # Net = sell_proceeds - sell_fee - buy_cost - buy_fee - withdrawal_fee_value
        net_profit = (
            sell_proceeds -
            fees.sell_trading_fee -
            buy_cost -
            fees.buy_trading_fee -
            fees.withdrawal_fee_in_quote -
            fees.network_fee_in_quote -
            fees.deposit_fee_in_quote
        )

        net_profit_percent = (net_profit / buy_cost) * Decimal("100") if buy_cost > 0 else Decimal("0")

        # Calculate breakeven spread (minimum spread needed to cover fees)
        total_fee_rate = (
            buy_pair.taker_fee +
            sell_pair.taker_fee +
            (fees.withdrawal_fee / trade_amount if trade_amount > 0 else Decimal("0"))
        )
        breakeven_spread_percent = total_fee_rate * Decimal("100")

        return ProfitCalculation(
            trade_amount=trade_amount,
            buy_price=buy_price,
            sell_price=sell_price,
            buy_cost=buy_cost,
            sell_proceeds=sell_proceeds,
            gross_profit=gross_profit,
            gross_profit_percent=gross_profit_percent,
            fees=fees,
            net_profit=net_profit,
            net_profit_percent=net_profit_percent,
            amount_after_withdrawal=amount_after_withdrawal,
            is_profitable=net_profit > Decimal("0"),
            breakeven_spread_percent=breakeven_spread_percent
        )

    def calculate_with_slippage(
        self,
        trade_amount: Decimal,
        buy_slippage: SlippageEstimate,
        sell_slippage: SlippageEstimate,
        buy_pair: TradingPair,
        sell_pair: TradingPair,
        withdrawal_network: NetworkInfo,
        deposit_network: Optional[NetworkInfo] = None
    ) -> ProfitCalculation:
        """
        Calculate profit using slippage estimates from order book analysis.

        This method uses actual average prices from simulated order book execution.
        """
        return self.calculate_profit(
            trade_amount=trade_amount,
            buy_price=buy_slippage.average_price,
            sell_price=sell_slippage.average_price,
            buy_pair=buy_pair,
            sell_pair=sell_pair,
            withdrawal_network=withdrawal_network,
            deposit_network=deposit_network
        )

    def find_optimal_trade_amount(
        self,
        max_amount: Decimal,
        buy_prices: list[tuple[Decimal, Decimal]],  # List of (price, amount) from order book
        sell_prices: list[tuple[Decimal, Decimal]],
        buy_pair: TradingPair,
        sell_pair: TradingPair,
        withdrawal_network: NetworkInfo,
        min_profit_threshold: Decimal = Decimal("0")
    ) -> tuple[Decimal, ProfitCalculation]:
        """
        Find the optimal trade amount that maximizes profit.

        Uses order book depth to find the best balance between
        volume and price impact.

        Returns:
            Tuple of (optimal_amount, profit_calculation)
        """
        # Test amounts from minimum to maximum
        test_amounts = [
            max_amount * Decimal(str(pct))
            for pct in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        ]

        # Also ensure minimum viable amount
        min_amount = max(
            buy_pair.min_amount or Decimal("0"),
            sell_pair.min_amount or Decimal("0"),
            withdrawal_network.withdraw_min
        )

        if min_amount > 0 and min_amount not in test_amounts:
            test_amounts = [min_amount] + test_amounts

        best_profit = Decimal("-999999999")
        best_amount = Decimal("0")
        best_calculation = None

        for amount in test_amounts:
            if amount < min_amount or amount > max_amount:
                continue

            # Calculate average prices for this amount
            buy_avg = self._calculate_average_price(buy_prices, amount, is_buy=True)
            sell_avg = self._calculate_average_price(sell_prices, amount, is_buy=False)

            if buy_avg is None or sell_avg is None:
                continue

            calc = self.calculate_profit(
                trade_amount=amount,
                buy_price=buy_avg,
                sell_price=sell_avg,
                buy_pair=buy_pair,
                sell_pair=sell_pair,
                withdrawal_network=withdrawal_network
            )

            if calc.net_profit > best_profit and calc.net_profit >= min_profit_threshold:
                best_profit = calc.net_profit
                best_amount = amount
                best_calculation = calc

        if best_calculation is None:
            # Return calculation for minimum amount even if unprofitable
            buy_avg = self._calculate_average_price(buy_prices, min_amount, is_buy=True)
            sell_avg = self._calculate_average_price(sell_prices, min_amount, is_buy=False)

            best_calculation = self.calculate_profit(
                trade_amount=min_amount,
                buy_price=buy_avg or Decimal("0"),
                sell_price=sell_avg or Decimal("0"),
                buy_pair=buy_pair,
                sell_pair=sell_pair,
                withdrawal_network=withdrawal_network
            )
            best_amount = min_amount

        return best_amount, best_calculation

    def _calculate_average_price(
        self,
        price_levels: list[tuple[Decimal, Decimal]],
        amount: Decimal,
        is_buy: bool
    ) -> Optional[Decimal]:
        """Calculate average price to fill a given amount."""
        if not price_levels:
            return None

        remaining = amount
        total_value = Decimal("0")

        # For buying, prices should be sorted low to high (asks)
        # For selling, prices should be sorted high to low (bids)
        sorted_levels = sorted(
            price_levels,
            key=lambda x: x[0],
            reverse=not is_buy
        )

        for price, available in sorted_levels:
            if remaining <= 0:
                break

            fill = min(remaining, available)
            total_value += fill * price
            remaining -= fill

        if remaining > 0:
            return None  # Not enough liquidity

        return total_value / amount

    def estimate_minimum_profitable_spread(
        self,
        buy_pair: TradingPair,
        sell_pair: TradingPair,
        withdrawal_fee_percent: Decimal
    ) -> Decimal:
        """
        Estimate the minimum spread required for profitability.

        This is useful for filtering opportunities before detailed analysis.
        """
        total_fee_percent = (
            buy_pair.taker_fee * Decimal("100") +
            sell_pair.taker_fee * Decimal("100") +
            withdrawal_fee_percent
        )

        # Add a small buffer for safety
        return total_fee_percent * Decimal("1.2")
