"""
Main arbitrage opportunity finder.
Coordinates all analyzers to find and validate arbitrage opportunities.
"""
import asyncio
from decimal import Decimal
from typing import Optional
from datetime import datetime
from uuid import uuid4
import structlog

from .models import (
    ArbitrageOpportunity, AssetInfo, TradingPair, OrderBook, NetworkInfo
)
from ..exchanges.exchange_manager import ExchangeManager
from ..analyzers.orderbook_analyzer import OrderBookAnalyzer, LiquidityAnalysis
from ..analyzers.profit_calculator import ProfitCalculator, ProfitCalculation
from ..analyzers.risk_manager import RiskManager, RiskAssessment
from config.settings import settings

logger = structlog.get_logger()


class ArbitrageFinder:
    """
    Finds and validates arbitrage opportunities across exchanges.

    This class orchestrates:
    1. Discovery of tradeable pairs across exchanges
    2. Network route analysis for asset transfers
    3. Order book analysis for liquidity and slippage
    4. Profit calculation including all fees
    5. Risk assessment
    """

    def __init__(
        self,
        exchange_manager: ExchangeManager,
        min_profit_percent: Decimal = Decimal("0.5"),
        max_position_usd: Decimal = Decimal("1000"),
        max_transfer_time_minutes: int = 30
    ):
        self.exchange_manager = exchange_manager
        self.min_profit_percent = min_profit_percent
        self.max_position_usd = max_position_usd
        self.max_transfer_time_minutes = max_transfer_time_minutes

        self.orderbook_analyzer = OrderBookAnalyzer(
            min_liquidity_multiplier=settings.risk.min_liquidity_multiplier
        )
        self.profit_calculator = ProfitCalculator()
        self.risk_manager = RiskManager(
            max_transfer_time_minutes=max_transfer_time_minutes,
            max_price_slippage_percent=settings.risk.max_price_slippage_percent,
            min_liquidity_multiplier=settings.risk.min_liquidity_multiplier
        )

        # Cache for discovered arbitrageable assets
        self._arbitrageable_cache: dict = {}
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl_seconds = 300  # 5 minutes

    async def discover_opportunities(self) -> list[ArbitrageOpportunity]:
        """
        Main entry point: Discover all arbitrage opportunities.

        This performs a full scan of:
        1. All pairs across all connected exchanges
        2. All possible network routes
        3. Current order book states
        4. Fee calculations
        5. Risk assessment
        """
        logger.info("Starting arbitrage opportunity scan...")

        # Refresh arbitrageable assets cache if needed
        await self._refresh_arbitrageable_cache()

        if not self._arbitrageable_cache:
            logger.warning("No arbitrageable assets found")
            return []

        # Analyze each potential arbitrage
        opportunities = []
        tasks = []

        for pair, data in self._arbitrageable_cache.items():
            task = self._analyze_pair(pair, data)
            tasks.append(task)

        # Run analyses in parallel with some concurrency limit
        batch_size = 10
        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i + batch_size]
            results = await asyncio.gather(*batch, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Analysis failed: {result}")
                elif result:
                    opportunities.extend(result)

        # Sort by net profit
        opportunities.sort(key=lambda x: x.net_profit_percent, reverse=True)

        logger.info(
            f"Found {len(opportunities)} opportunities",
            profitable=len([o for o in opportunities if o.is_valid])
        )

        return opportunities

    async def _refresh_arbitrageable_cache(self):
        """Refresh the cache of arbitrageable assets if needed."""
        now = datetime.utcnow()

        if (self._cache_timestamp and
            (now - self._cache_timestamp).seconds < self._cache_ttl_seconds):
            return

        logger.info("Refreshing arbitrageable assets cache...")
        self._arbitrageable_cache = await self.exchange_manager.find_arbitrageable_assets()
        self._cache_timestamp = now

    async def _analyze_pair(
        self,
        pair: str,
        data: dict
    ) -> list[ArbitrageOpportunity]:
        """Analyze a single pair for arbitrage opportunities."""
        opportunities = []

        base = data["base"]
        quote = data["quote"]
        exchanges = data["exchanges"]
        network_routes = data["network_routes"]
        asset_info_map = data["asset_info"]

        # Get order books from all exchanges
        order_books = await self.exchange_manager.get_order_books(
            pair,
            exchanges=exchanges
        )

        if len(order_books) < 2:
            return []

        # Get trading pair info for fee calculations
        all_pairs = await self.exchange_manager.get_all_trading_pairs()

        pair_info = {}
        for exchange_id, pairs in all_pairs.items():
            for p in pairs:
                if p.symbol == pair:
                    pair_info[exchange_id] = p
                    break

        # Check each network route
        for route in network_routes:
            from_exchange = route["from_exchange"]
            to_exchange = route["to_exchange"]
            network_id = route["network"]

            if from_exchange not in order_books or to_exchange not in order_books:
                continue

            buy_ob = order_books[from_exchange]
            sell_ob = order_books[to_exchange]

            # Get best prices
            best_ask = buy_ob.get_best_ask()
            best_bid = sell_ob.get_best_bid()

            if not best_ask or not best_bid:
                continue

            # Quick check: is there any potential spread?
            if best_bid.price <= best_ask.price:
                continue

            # Get network info for withdrawal and deposit
            withdrawal_network = self._get_network_info(
                asset_info_map.get(from_exchange),
                network_id
            )
            deposit_network = self._get_network_info(
                asset_info_map.get(to_exchange),
                network_id
            )

            if not withdrawal_network or not deposit_network:
                continue

            # Check if transfers are enabled
            if not withdrawal_network.withdraw_enabled:
                continue
            if not deposit_network.deposit_enabled:
                continue

            # Get trading pair info
            buy_pair = pair_info.get(from_exchange)
            sell_pair = pair_info.get(to_exchange)

            if not buy_pair or not sell_pair:
                continue

            # Calculate optimal trade amount
            opportunity = await self._calculate_opportunity(
                base=base,
                quote=quote,
                buy_exchange=from_exchange,
                sell_exchange=to_exchange,
                buy_ob=buy_ob,
                sell_ob=sell_ob,
                buy_pair=buy_pair,
                sell_pair=sell_pair,
                withdrawal_network=withdrawal_network,
                deposit_network=deposit_network,
                network_id=network_id,
                buy_asset_info=asset_info_map.get(from_exchange),
                sell_asset_info=asset_info_map.get(to_exchange)
            )

            if opportunity:
                opportunities.append(opportunity)

        return opportunities

    def _get_network_info(
        self,
        asset_info: Optional[AssetInfo],
        network_id: str
    ) -> Optional[NetworkInfo]:
        """Get network info for a specific network ID."""
        if not asset_info:
            return None

        # Normalize network ID for matching
        normalized = AssetInfo._normalize_network(network_id)

        for network in asset_info.networks:
            if AssetInfo._normalize_network(network.network_id) == normalized:
                return network

        return None

    async def _calculate_opportunity(
        self,
        base: str,
        quote: str,
        buy_exchange: str,
        sell_exchange: str,
        buy_ob: OrderBook,
        sell_ob: OrderBook,
        buy_pair: TradingPair,
        sell_pair: TradingPair,
        withdrawal_network: NetworkInfo,
        deposit_network: NetworkInfo,
        network_id: str,
        buy_asset_info: Optional[AssetInfo],
        sell_asset_info: Optional[AssetInfo]
    ) -> Optional[ArbitrageOpportunity]:
        """Calculate a complete arbitrage opportunity."""

        # Get best prices
        best_ask = buy_ob.get_best_ask()
        best_bid = sell_ob.get_best_bid()

        if not best_ask or not best_bid:
            return None

        # Calculate maximum executable amount based on liquidity and position limit
        max_amount_by_position = self.max_position_usd / best_ask.price

        max_executable = self.orderbook_analyzer.calculate_max_executable_amount(
            buy_ob,
            sell_ob,
            max_slippage_percent=settings.risk.max_price_slippage_percent
        )

        if max_executable <= 0:
            return None

        # Use smaller of position limit and executable amount
        trade_amount = min(max_amount_by_position, max_executable)

        # Check minimum amounts
        min_amount = max(
            buy_pair.min_amount or Decimal("0"),
            sell_pair.min_amount or Decimal("0"),
            withdrawal_network.withdraw_min
        )

        if trade_amount < min_amount:
            return None

        # Calculate slippage for this amount
        buy_slippage = self.orderbook_analyzer.estimate_buy_slippage(buy_ob, trade_amount)
        sell_slippage = self.orderbook_analyzer.estimate_sell_slippage(sell_ob, trade_amount)

        if not buy_slippage.is_executable or not sell_slippage.is_executable:
            return None

        # Calculate profit
        profit_calc = self.profit_calculator.calculate_with_slippage(
            trade_amount=trade_amount,
            buy_slippage=buy_slippage,
            sell_slippage=sell_slippage,
            buy_pair=buy_pair,
            sell_pair=sell_pair,
            withdrawal_network=withdrawal_network,
            deposit_network=deposit_network
        )

        # Check minimum profit threshold
        if profit_calc.net_profit_percent < self.min_profit_percent:
            return None

        # Perform risk assessment
        buy_liquidity = self.orderbook_analyzer.analyze_liquidity(buy_ob, trade_amount)
        sell_liquidity = self.orderbook_analyzer.analyze_liquidity(sell_ob, trade_amount)

        risk_assessment = self.risk_manager.assess_opportunity(
            profit_calc=profit_calc,
            withdrawal_network=withdrawal_network,
            deposit_network=deposit_network,
            buy_liquidity=buy_liquidity,
            sell_liquidity=sell_liquidity,
            buy_asset_info=buy_asset_info,
            sell_asset_info=sell_asset_info
        )

        # Determine validity
        should_execute, reason = self.risk_manager.should_execute(risk_assessment)

        # Build opportunity object
        gross_spread = (
            (best_bid.price - best_ask.price) / best_ask.price * Decimal("100")
        )

        opportunity = ArbitrageOpportunity(
            id=str(uuid4()),
            detected_at=datetime.utcnow(),
            base_asset=base,
            quote_asset=quote,
            buy_exchange=buy_exchange,
            sell_exchange=sell_exchange,
            transfer_network=network_id,
            transfer_network_name=withdrawal_network.network_name,
            buy_price=buy_slippage.average_price,
            sell_price=sell_slippage.average_price,
            gross_spread_percent=gross_spread,
            max_executable_amount=max_executable,
            trade_amount=trade_amount,
            trade_value_usd=profit_calc.buy_cost,
            buy_exchange_fee=profit_calc.fees.buy_trading_fee,
            sell_exchange_fee=profit_calc.fees.sell_trading_fee,
            withdrawal_fee=withdrawal_network.withdraw_fee,
            withdrawal_fee_usd=profit_calc.fees.withdrawal_fee_in_quote,
            total_fees_usd=profit_calc.fees.total_fees_in_quote,
            net_profit_usd=profit_calc.net_profit,
            net_profit_percent=profit_calc.net_profit_percent,
            estimated_transfer_minutes=max(
                withdrawal_network.estimated_arrival_minutes,
                deposit_network.estimated_arrival_minutes
            ),
            confirmations_required=max(
                withdrawal_network.confirmations_required,
                deposit_network.confirmations_required
            ),
            risk_score=risk_assessment.overall_score,
            risk_factors=[f.name for f in risk_assessment.factors if f.score >= Decimal("50")],
            is_valid=should_execute,
            invalidation_reason=None if should_execute else reason
        )

        return opportunity

    async def find_best_opportunity(self) -> Optional[ArbitrageOpportunity]:
        """Find the single best opportunity based on risk-adjusted profit."""
        opportunities = await self.discover_opportunities()

        valid = [o for o in opportunities if o.is_valid]

        if not valid:
            return None

        # Sort by risk-adjusted profit (profit / risk_score)
        def risk_adjusted_score(opp: ArbitrageOpportunity) -> Decimal:
            if opp.risk_score == 0:
                return opp.net_profit_percent * Decimal("10")
            return opp.net_profit_percent / opp.risk_score * Decimal("100")

        valid.sort(key=risk_adjusted_score, reverse=True)

        return valid[0]

    async def monitor_opportunity(
        self,
        opportunity: ArbitrageOpportunity,
        revalidate: bool = True
    ) -> ArbitrageOpportunity:
        """
        Re-check an opportunity to see if it's still valid.

        Used before execution to ensure conditions haven't changed.
        """
        if not revalidate:
            return opportunity

        pair = f"{opportunity.base_asset}/{opportunity.quote_asset}"

        # Re-fetch order books
        order_books = await self.exchange_manager.get_order_books(
            pair,
            exchanges=[opportunity.buy_exchange, opportunity.sell_exchange]
        )

        if len(order_books) < 2:
            opportunity.is_valid = False
            opportunity.invalidation_reason = "Failed to fetch order books"
            return opportunity

        buy_ob = order_books.get(opportunity.buy_exchange)
        sell_ob = order_books.get(opportunity.sell_exchange)

        if not buy_ob or not sell_ob:
            opportunity.is_valid = False
            opportunity.invalidation_reason = "Missing order book"
            return opportunity

        # Re-calculate slippage and check if still profitable
        buy_slippage = self.orderbook_analyzer.estimate_buy_slippage(
            buy_ob,
            opportunity.trade_amount
        )
        sell_slippage = self.orderbook_analyzer.estimate_sell_slippage(
            sell_ob,
            opportunity.trade_amount
        )

        if not buy_slippage.is_executable or not sell_slippage.is_executable:
            opportunity.is_valid = False
            opportunity.invalidation_reason = "Insufficient liquidity"
            return opportunity

        # Quick profit check
        new_spread = (sell_slippage.average_price - buy_slippage.average_price) / buy_slippage.average_price * Decimal("100")

        if new_spread < opportunity.gross_spread_percent * Decimal("0.5"):
            opportunity.is_valid = False
            opportunity.invalidation_reason = f"Spread reduced from {opportunity.gross_spread_percent:.2f}% to {new_spread:.2f}%"
            return opportunity

        # Update prices
        opportunity.buy_price = buy_slippage.average_price
        opportunity.sell_price = sell_slippage.average_price
        opportunity.gross_spread_percent = new_spread

        return opportunity
