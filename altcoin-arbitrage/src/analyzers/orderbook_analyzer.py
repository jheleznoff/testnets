"""
Order book analyzer for liquidity and slippage analysis.
"""
from decimal import Decimal
from typing import Optional
from dataclasses import dataclass
import structlog

from ..core.models import OrderBook, OrderBookEntry

logger = structlog.get_logger()


@dataclass
class LiquidityAnalysis:
    """Result of liquidity analysis for an order book."""
    exchange_id: str
    symbol: str

    # Best prices
    best_bid: Optional[Decimal] = None
    best_ask: Optional[Decimal] = None
    spread: Optional[Decimal] = None
    spread_percent: Optional[Decimal] = None

    # Depth analysis
    bid_depth_1_percent: Decimal = Decimal("0")  # Volume within 1% of best bid
    ask_depth_1_percent: Decimal = Decimal("0")  # Volume within 1% of best ask
    bid_depth_5_percent: Decimal = Decimal("0")  # Volume within 5% of best bid
    ask_depth_5_percent: Decimal = Decimal("0")  # Volume within 5% of best ask

    # Total liquidity
    total_bid_volume: Decimal = Decimal("0")
    total_ask_volume: Decimal = Decimal("0")

    # Executable amounts at target prices
    max_buy_amount_at_market: Decimal = Decimal("0")
    max_sell_amount_at_market: Decimal = Decimal("0")

    # Is liquidity sufficient for a given trade size
    is_sufficient: bool = False
    required_amount: Decimal = Decimal("0")


@dataclass
class SlippageEstimate:
    """Estimated slippage for a trade."""
    exchange_id: str
    symbol: str
    side: str  # "buy" or "sell"
    amount: Decimal

    # Slippage results
    start_price: Decimal
    average_price: Decimal
    end_price: Decimal
    slippage_percent: Decimal
    total_cost: Decimal

    # Whether the order book has enough liquidity
    is_executable: bool = True
    partial_fill_amount: Optional[Decimal] = None


class OrderBookAnalyzer:
    """Analyzes order books for liquidity and slippage."""

    def __init__(self, min_liquidity_multiplier: Decimal = Decimal("3")):
        """
        Args:
            min_liquidity_multiplier: Required order book depth as multiple of trade size
        """
        self.min_liquidity_multiplier = min_liquidity_multiplier

    def analyze_liquidity(
        self,
        order_book: OrderBook,
        required_amount: Decimal = Decimal("0")
    ) -> LiquidityAnalysis:
        """Analyze liquidity of an order book."""
        analysis = LiquidityAnalysis(
            exchange_id=order_book.exchange_id,
            symbol=order_book.symbol,
            required_amount=required_amount
        )

        best_bid = order_book.get_best_bid()
        best_ask = order_book.get_best_ask()

        if not best_bid or not best_ask:
            return analysis

        analysis.best_bid = best_bid.price
        analysis.best_ask = best_ask.price
        analysis.spread = best_ask.price - best_bid.price
        analysis.spread_percent = (analysis.spread / best_bid.price) * Decimal("100")

        # Calculate depth at various price levels
        analysis.bid_depth_1_percent = self._calculate_depth(
            order_book.bids,
            best_bid.price,
            Decimal("0.01"),
            is_bid=True
        )
        analysis.ask_depth_1_percent = self._calculate_depth(
            order_book.asks,
            best_ask.price,
            Decimal("0.01"),
            is_bid=False
        )
        analysis.bid_depth_5_percent = self._calculate_depth(
            order_book.bids,
            best_bid.price,
            Decimal("0.05"),
            is_bid=True
        )
        analysis.ask_depth_5_percent = self._calculate_depth(
            order_book.asks,
            best_ask.price,
            Decimal("0.05"),
            is_bid=False
        )

        # Total volumes
        analysis.total_bid_volume = sum(entry.amount for entry in order_book.bids)
        analysis.total_ask_volume = sum(entry.amount for entry in order_book.asks)

        analysis.max_buy_amount_at_market = analysis.total_ask_volume
        analysis.max_sell_amount_at_market = analysis.total_bid_volume

        # Check if liquidity is sufficient
        if required_amount > 0:
            min_required = required_amount * self.min_liquidity_multiplier
            analysis.is_sufficient = (
                analysis.total_ask_volume >= min_required and
                analysis.total_bid_volume >= min_required
            )

        return analysis

    def estimate_buy_slippage(
        self,
        order_book: OrderBook,
        amount: Decimal
    ) -> SlippageEstimate:
        """Estimate slippage for buying a specific amount."""
        best_ask = order_book.get_best_ask()

        if not best_ask:
            return SlippageEstimate(
                exchange_id=order_book.exchange_id,
                symbol=order_book.symbol,
                side="buy",
                amount=amount,
                start_price=Decimal("0"),
                average_price=Decimal("0"),
                end_price=Decimal("0"),
                slippage_percent=Decimal("100"),
                total_cost=Decimal("0"),
                is_executable=False
            )

        remaining = amount
        total_cost = Decimal("0")
        last_price = best_ask.price

        for ask in order_book.asks:
            if remaining <= 0:
                break

            fill_amount = min(remaining, ask.amount)
            total_cost += fill_amount * ask.price
            remaining -= fill_amount
            last_price = ask.price

        if remaining > 0:
            # Not enough liquidity
            filled_amount = amount - remaining
            return SlippageEstimate(
                exchange_id=order_book.exchange_id,
                symbol=order_book.symbol,
                side="buy",
                amount=amount,
                start_price=best_ask.price,
                average_price=total_cost / filled_amount if filled_amount > 0 else Decimal("0"),
                end_price=last_price,
                slippage_percent=Decimal("100"),
                total_cost=total_cost,
                is_executable=False,
                partial_fill_amount=filled_amount
            )

        average_price = total_cost / amount
        slippage = ((average_price - best_ask.price) / best_ask.price) * Decimal("100")

        return SlippageEstimate(
            exchange_id=order_book.exchange_id,
            symbol=order_book.symbol,
            side="buy",
            amount=amount,
            start_price=best_ask.price,
            average_price=average_price,
            end_price=last_price,
            slippage_percent=slippage,
            total_cost=total_cost,
            is_executable=True
        )

    def estimate_sell_slippage(
        self,
        order_book: OrderBook,
        amount: Decimal
    ) -> SlippageEstimate:
        """Estimate slippage for selling a specific amount."""
        best_bid = order_book.get_best_bid()

        if not best_bid:
            return SlippageEstimate(
                exchange_id=order_book.exchange_id,
                symbol=order_book.symbol,
                side="sell",
                amount=amount,
                start_price=Decimal("0"),
                average_price=Decimal("0"),
                end_price=Decimal("0"),
                slippage_percent=Decimal("100"),
                total_cost=Decimal("0"),
                is_executable=False
            )

        remaining = amount
        total_received = Decimal("0")
        last_price = best_bid.price

        for bid in order_book.bids:
            if remaining <= 0:
                break

            fill_amount = min(remaining, bid.amount)
            total_received += fill_amount * bid.price
            remaining -= fill_amount
            last_price = bid.price

        if remaining > 0:
            # Not enough liquidity
            filled_amount = amount - remaining
            return SlippageEstimate(
                exchange_id=order_book.exchange_id,
                symbol=order_book.symbol,
                side="sell",
                amount=amount,
                start_price=best_bid.price,
                average_price=total_received / filled_amount if filled_amount > 0 else Decimal("0"),
                end_price=last_price,
                slippage_percent=Decimal("100"),
                total_cost=total_received,
                is_executable=False,
                partial_fill_amount=filled_amount
            )

        average_price = total_received / amount
        slippage = ((best_bid.price - average_price) / best_bid.price) * Decimal("100")

        return SlippageEstimate(
            exchange_id=order_book.exchange_id,
            symbol=order_book.symbol,
            side="sell",
            amount=amount,
            start_price=best_bid.price,
            average_price=average_price,
            end_price=last_price,
            slippage_percent=slippage,
            total_cost=total_received,
            is_executable=True
        )

    def calculate_max_executable_amount(
        self,
        buy_order_book: OrderBook,
        sell_order_book: OrderBook,
        max_slippage_percent: Decimal = Decimal("0.5")
    ) -> Decimal:
        """Calculate maximum amount that can be executed with acceptable slippage.

        Args:
            buy_order_book: Order book to buy from (use asks)
            sell_order_book: Order book to sell to (use bids)
            max_slippage_percent: Maximum acceptable slippage percentage

        Returns:
            Maximum executable amount
        """
        # Binary search for maximum amount
        # Start with total available liquidity as upper bound
        min_amount = Decimal("0")
        max_amount = min(
            sum(ask.amount for ask in buy_order_book.asks),
            sum(bid.amount for bid in sell_order_book.bids)
        )

        if max_amount <= 0:
            return Decimal("0")

        # Binary search with precision
        precision = max_amount / Decimal("1000")
        best_amount = Decimal("0")

        while max_amount - min_amount > precision:
            mid_amount = (min_amount + max_amount) / Decimal("2")

            buy_slippage = self.estimate_buy_slippage(buy_order_book, mid_amount)
            sell_slippage = self.estimate_sell_slippage(sell_order_book, mid_amount)

            if (buy_slippage.is_executable and
                sell_slippage.is_executable and
                buy_slippage.slippage_percent <= max_slippage_percent and
                sell_slippage.slippage_percent <= max_slippage_percent):
                best_amount = mid_amount
                min_amount = mid_amount
            else:
                max_amount = mid_amount

        return best_amount

    def _calculate_depth(
        self,
        entries: list[OrderBookEntry],
        reference_price: Decimal,
        percent_range: Decimal,
        is_bid: bool
    ) -> Decimal:
        """Calculate order book depth within a percentage range of reference price."""
        if is_bid:
            limit_price = reference_price * (Decimal("1") - percent_range)
            return sum(
                entry.amount for entry in entries
                if entry.price >= limit_price
            )
        else:
            limit_price = reference_price * (Decimal("1") + percent_range)
            return sum(
                entry.amount for entry in entries
                if entry.price <= limit_price
            )

    def compare_order_books(
        self,
        order_books: dict[str, OrderBook],
        trade_amount: Decimal
    ) -> dict:
        """Compare order books across exchanges for arbitrage potential.

        Returns dict with:
        - best_buy_exchange: Exchange with lowest ask
        - best_sell_exchange: Exchange with highest bid
        - spread_percent: Potential profit before fees
        - buy_slippage: Slippage estimate for buying
        - sell_slippage: Slippage estimate for selling
        - is_viable: Whether arbitrage looks viable based on liquidity
        """
        if len(order_books) < 2:
            return {"is_viable": False, "reason": "Not enough exchanges"}

        # Find best prices across exchanges
        best_buy_exchange = None
        lowest_ask = None

        best_sell_exchange = None
        highest_bid = None

        for exchange_id, ob in order_books.items():
            best_ask = ob.get_best_ask()
            best_bid = ob.get_best_bid()

            if best_ask and (lowest_ask is None or best_ask.price < lowest_ask):
                lowest_ask = best_ask.price
                best_buy_exchange = exchange_id

            if best_bid and (highest_bid is None or best_bid.price > highest_bid):
                highest_bid = best_bid.price
                best_sell_exchange = exchange_id

        if not lowest_ask or not highest_bid:
            return {"is_viable": False, "reason": "Missing price data"}

        if best_buy_exchange == best_sell_exchange:
            # Both best prices on same exchange - no arbitrage
            return {"is_viable": False, "reason": "Best prices on same exchange"}

        spread = highest_bid - lowest_ask
        spread_percent = (spread / lowest_ask) * Decimal("100")

        if spread <= 0:
            return {
                "is_viable": False,
                "reason": "Negative spread",
                "spread_percent": spread_percent
            }

        # Analyze slippage for the trade
        buy_slippage = self.estimate_buy_slippage(
            order_books[best_buy_exchange],
            trade_amount
        )
        sell_slippage = self.estimate_sell_slippage(
            order_books[best_sell_exchange],
            trade_amount
        )

        is_viable = (
            buy_slippage.is_executable and
            sell_slippage.is_executable and
            spread_percent > (buy_slippage.slippage_percent + sell_slippage.slippage_percent)
        )

        return {
            "is_viable": is_viable,
            "best_buy_exchange": best_buy_exchange,
            "best_sell_exchange": best_sell_exchange,
            "lowest_ask": lowest_ask,
            "highest_bid": highest_bid,
            "spread": spread,
            "spread_percent": spread_percent,
            "buy_slippage": buy_slippage,
            "sell_slippage": sell_slippage,
            "effective_buy_price": buy_slippage.average_price,
            "effective_sell_price": sell_slippage.average_price,
            "effective_spread_percent": (
                (sell_slippage.average_price - buy_slippage.average_price) /
                buy_slippage.average_price * Decimal("100")
            ) if buy_slippage.average_price > 0 else Decimal("0")
        }
