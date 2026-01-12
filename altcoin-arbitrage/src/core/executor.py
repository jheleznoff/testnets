"""
Trade execution engine for arbitrage operations.
Handles the complete lifecycle of an arbitrage trade.
"""
import asyncio
from decimal import Decimal
from typing import Optional
from datetime import datetime
from enum import Enum
import structlog

from .models import (
    ArbitrageOpportunity, ArbitrageResult, TradeExecution, TransferExecution
)
from ..exchanges.exchange_manager import ExchangeManager
from config.settings import settings

logger = structlog.get_logger()


class ExecutionState(str, Enum):
    """States of arbitrage execution."""
    PENDING = "pending"
    BUYING = "buying"
    WITHDRAWING = "withdrawing"
    CONFIRMING = "confirming"
    SELLING = "selling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionError(Exception):
    """Exception for execution failures."""
    def __init__(self, message: str, state: ExecutionState, recoverable: bool = False):
        super().__init__(message)
        self.state = state
        self.recoverable = recoverable


class ArbitrageExecutor:
    """
    Executes arbitrage trades.

    Workflow:
    1. Buy asset on source exchange
    2. Withdraw to destination exchange
    3. Wait for confirmations
    4. Sell asset on destination exchange
    5. Record results
    """

    def __init__(
        self,
        exchange_manager: ExchangeManager,
        dry_run: bool = True,
        max_retries: int = 3,
        retry_delay_seconds: int = 5
    ):
        self.exchange_manager = exchange_manager
        self.dry_run = dry_run
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds

        self._active_executions: dict[str, ArbitrageResult] = {}

    async def execute(
        self,
        opportunity: ArbitrageOpportunity,
        revalidate_before_buy: bool = True
    ) -> ArbitrageResult:
        """
        Execute a complete arbitrage trade.

        Args:
            opportunity: The opportunity to execute
            revalidate_before_buy: Whether to re-check opportunity before buying

        Returns:
            ArbitrageResult with execution details
        """
        result = ArbitrageResult(
            opportunity=opportunity,
            expected_profit_usd=opportunity.net_profit_usd,
            status="in_progress"
        )

        self._active_executions[opportunity.id] = result

        try:
            if self.dry_run:
                return await self._simulate_execution(opportunity, result)

            # Step 1: Execute buy
            logger.info(
                "Executing buy order",
                opportunity_id=opportunity.id,
                exchange=opportunity.buy_exchange,
                asset=opportunity.base_asset,
                amount=str(opportunity.trade_amount)
            )

            buy_trade = await self._execute_buy(opportunity)
            result.buy_trade = buy_trade

            if not buy_trade or buy_trade.status == "failed":
                raise ExecutionError(
                    "Buy order failed",
                    ExecutionState.BUYING,
                    recoverable=False
                )

            # Step 2: Initiate withdrawal
            logger.info(
                "Initiating withdrawal",
                opportunity_id=opportunity.id,
                from_exchange=opportunity.buy_exchange,
                to_exchange=opportunity.sell_exchange,
                network=opportunity.transfer_network
            )

            transfer = await self._execute_withdrawal(opportunity, buy_trade.amount)
            result.transfer = transfer

            if not transfer or transfer.status == "failed":
                raise ExecutionError(
                    "Withdrawal failed",
                    ExecutionState.WITHDRAWING,
                    recoverable=False
                )

            # Step 3: Wait for confirmations
            logger.info(
                "Waiting for transfer confirmation",
                opportunity_id=opportunity.id,
                confirmations_required=opportunity.confirmations_required
            )

            await self._wait_for_confirmation(transfer, opportunity)

            # Step 4: Execute sell
            logger.info(
                "Executing sell order",
                opportunity_id=opportunity.id,
                exchange=opportunity.sell_exchange,
                asset=opportunity.base_asset
            )

            sell_trade = await self._execute_sell(opportunity, transfer.amount - transfer.fee)
            result.sell_trade = sell_trade

            if not sell_trade or sell_trade.status == "failed":
                raise ExecutionError(
                    "Sell order failed",
                    ExecutionState.SELLING,
                    recoverable=True  # We have the asset, can retry
                )

            # Calculate final P&L
            result.completed_at = datetime.utcnow()
            result.status = "completed"

            actual_profit = self._calculate_actual_profit(result)
            result.realized_profit_usd = actual_profit

            if opportunity.net_profit_usd != Decimal("0"):
                result.profit_variance_percent = (
                    (actual_profit - opportunity.net_profit_usd) /
                    opportunity.net_profit_usd * Decimal("100")
                )

            logger.info(
                "Arbitrage completed",
                opportunity_id=opportunity.id,
                expected_profit=str(opportunity.net_profit_usd),
                realized_profit=str(actual_profit),
                variance=str(result.profit_variance_percent)
            )

            return result

        except ExecutionError as e:
            result.status = "failed"
            result.failure_reason = str(e)
            logger.error(
                "Execution failed",
                opportunity_id=opportunity.id,
                state=e.state.value,
                error=str(e),
                recoverable=e.recoverable
            )
            return result

        except Exception as e:
            result.status = "failed"
            result.failure_reason = f"Unexpected error: {str(e)}"
            logger.error(
                "Unexpected execution error",
                opportunity_id=opportunity.id,
                error=str(e)
            )
            return result

        finally:
            self._active_executions.pop(opportunity.id, None)

    async def _simulate_execution(
        self,
        opportunity: ArbitrageOpportunity,
        result: ArbitrageResult
    ) -> ArbitrageResult:
        """Simulate execution in dry run mode."""
        logger.info(
            "DRY RUN: Simulating arbitrage execution",
            opportunity_id=opportunity.id
        )

        # Simulate buy
        result.buy_trade = TradeExecution(
            id="dry_run_buy",
            opportunity_id=opportunity.id,
            exchange_id=opportunity.buy_exchange,
            side="buy",
            symbol=f"{opportunity.base_asset}/{opportunity.quote_asset}",
            amount=opportunity.trade_amount,
            price=opportunity.buy_price,
            cost=opportunity.trade_amount * opportunity.buy_price,
            fee=opportunity.buy_exchange_fee,
            order_id="simulated",
            order_type="market",
            status="filled",
            expected_price=opportunity.buy_price,
            actual_slippage_percent=Decimal("0")
        )

        # Simulate transfer
        await asyncio.sleep(1)  # Brief delay for realism
        amount_after_fee = opportunity.trade_amount - opportunity.withdrawal_fee

        result.transfer = TransferExecution(
            id="dry_run_transfer",
            opportunity_id=opportunity.id,
            from_exchange=opportunity.buy_exchange,
            to_exchange=opportunity.sell_exchange,
            asset=opportunity.base_asset,
            network=opportunity.transfer_network,
            amount=opportunity.trade_amount,
            fee=opportunity.withdrawal_fee,
            deposit_address="simulated_address",
            status="completed",
            confirmations=opportunity.confirmations_required,
            confirmations_required=opportunity.confirmations_required
        )

        # Simulate sell
        result.sell_trade = TradeExecution(
            id="dry_run_sell",
            opportunity_id=opportunity.id,
            exchange_id=opportunity.sell_exchange,
            side="sell",
            symbol=f"{opportunity.base_asset}/{opportunity.quote_asset}",
            amount=amount_after_fee,
            price=opportunity.sell_price,
            cost=amount_after_fee * opportunity.sell_price,
            fee=opportunity.sell_exchange_fee,
            order_id="simulated",
            order_type="market",
            status="filled",
            expected_price=opportunity.sell_price,
            actual_slippage_percent=Decimal("0")
        )

        result.completed_at = datetime.utcnow()
        result.status = "completed"
        result.realized_profit_usd = opportunity.net_profit_usd
        result.profit_variance_percent = Decimal("0")

        logger.info(
            "DRY RUN: Simulation completed",
            opportunity_id=opportunity.id,
            simulated_profit=str(opportunity.net_profit_usd)
        )

        return result

    async def _execute_buy(
        self,
        opportunity: ArbitrageOpportunity
    ) -> Optional[TradeExecution]:
        """Execute the buy order."""
        client = self.exchange_manager.get_client(opportunity.buy_exchange)
        if not client:
            return None

        symbol = f"{opportunity.base_asset}/{opportunity.quote_asset}"

        for attempt in range(self.max_retries):
            try:
                trade = await client.place_market_order(
                    symbol=symbol,
                    side="buy",
                    amount=opportunity.trade_amount,
                    opportunity_id=opportunity.id
                )

                if trade:
                    trade.expected_price = opportunity.buy_price
                    if trade.price > 0:
                        trade.actual_slippage_percent = (
                            (trade.price - opportunity.buy_price) /
                            opportunity.buy_price * Decimal("100")
                        )
                    return trade

            except Exception as e:
                logger.warning(
                    f"Buy attempt {attempt + 1} failed: {e}",
                    opportunity_id=opportunity.id
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay_seconds)

        return None

    async def _execute_withdrawal(
        self,
        opportunity: ArbitrageOpportunity,
        amount: Decimal
    ) -> Optional[TransferExecution]:
        """Execute the withdrawal."""
        source_client = self.exchange_manager.get_client(opportunity.buy_exchange)
        dest_client = self.exchange_manager.get_client(opportunity.sell_exchange)

        if not source_client or not dest_client:
            return None

        # Get deposit address from destination exchange
        deposit_address = await dest_client.get_deposit_address(
            opportunity.base_asset,
            opportunity.transfer_network
        )

        if not deposit_address:
            logger.error(
                "Failed to get deposit address",
                exchange=opportunity.sell_exchange,
                asset=opportunity.base_asset,
                network=opportunity.transfer_network
            )
            return None

        # Initiate withdrawal
        for attempt in range(self.max_retries):
            try:
                transfer = await source_client.withdraw(
                    asset=opportunity.base_asset,
                    amount=amount,
                    address=deposit_address,
                    network=opportunity.transfer_network,
                    opportunity_id=opportunity.id
                )

                if transfer:
                    transfer.to_exchange = opportunity.sell_exchange
                    transfer.confirmations_required = opportunity.confirmations_required
                    return transfer

            except Exception as e:
                logger.warning(
                    f"Withdrawal attempt {attempt + 1} failed: {e}",
                    opportunity_id=opportunity.id
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay_seconds)

        return None

    async def _wait_for_confirmation(
        self,
        transfer: TransferExecution,
        opportunity: ArbitrageOpportunity,
        max_wait_minutes: int = 60
    ):
        """Wait for transfer to be confirmed on destination exchange."""
        dest_client = self.exchange_manager.get_client(opportunity.sell_exchange)
        if not dest_client:
            raise ExecutionError(
                "Destination exchange client not available",
                ExecutionState.CONFIRMING
            )

        start_time = datetime.utcnow()
        check_interval_seconds = 30

        while True:
            # Check if we've exceeded max wait time
            elapsed_minutes = (datetime.utcnow() - start_time).seconds / 60
            if elapsed_minutes > max_wait_minutes:
                raise ExecutionError(
                    f"Transfer confirmation timed out after {max_wait_minutes} minutes",
                    ExecutionState.CONFIRMING,
                    recoverable=True
                )

            # Check balance on destination exchange
            try:
                balance = await dest_client.get_balance(opportunity.base_asset)

                # Expected amount after withdrawal fee
                expected = transfer.amount - transfer.fee

                # Allow small margin for rounding
                if balance >= expected * Decimal("0.99"):
                    transfer.status = "completed"
                    transfer.completed_at = datetime.utcnow()
                    transfer.confirmations = transfer.confirmations_required
                    logger.info(
                        "Transfer confirmed",
                        opportunity_id=opportunity.id,
                        balance=str(balance)
                    )
                    return

            except Exception as e:
                logger.warning(
                    f"Error checking balance: {e}",
                    opportunity_id=opportunity.id
                )

            await asyncio.sleep(check_interval_seconds)

    async def _execute_sell(
        self,
        opportunity: ArbitrageOpportunity,
        amount: Decimal
    ) -> Optional[TradeExecution]:
        """Execute the sell order."""
        client = self.exchange_manager.get_client(opportunity.sell_exchange)
        if not client:
            return None

        symbol = f"{opportunity.base_asset}/{opportunity.quote_asset}"

        for attempt in range(self.max_retries):
            try:
                trade = await client.place_market_order(
                    symbol=symbol,
                    side="sell",
                    amount=amount,
                    opportunity_id=opportunity.id
                )

                if trade:
                    trade.expected_price = opportunity.sell_price
                    if trade.price > 0:
                        trade.actual_slippage_percent = (
                            (opportunity.sell_price - trade.price) /
                            opportunity.sell_price * Decimal("100")
                        )
                    return trade

            except Exception as e:
                logger.warning(
                    f"Sell attempt {attempt + 1} failed: {e}",
                    opportunity_id=opportunity.id
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay_seconds)

        return None

    def _calculate_actual_profit(self, result: ArbitrageResult) -> Decimal:
        """Calculate actual profit from execution results."""
        if not result.buy_trade or not result.sell_trade:
            return Decimal("0")

        # Total spent (buy cost + fees)
        total_spent = result.buy_trade.cost + result.buy_trade.fee

        # Withdrawal fee value
        withdrawal_fee_value = Decimal("0")
        if result.transfer:
            withdrawal_fee_value = result.transfer.fee * result.buy_trade.price

        # Total received (sell proceeds - fees)
        total_received = result.sell_trade.cost - result.sell_trade.fee

        # Net profit
        return total_received - total_spent - withdrawal_fee_value

    def get_active_executions(self) -> list[ArbitrageResult]:
        """Get list of currently active executions."""
        return list(self._active_executions.values())

    async def cancel_execution(self, opportunity_id: str) -> bool:
        """Attempt to cancel an active execution."""
        result = self._active_executions.get(opportunity_id)

        if not result:
            return False

        # Can only cancel if we haven't bought yet
        if result.buy_trade is not None:
            logger.warning(
                "Cannot cancel - buy already executed",
                opportunity_id=opportunity_id
            )
            return False

        result.status = "cancelled"
        result.failure_reason = "Cancelled by user"
        self._active_executions.pop(opportunity_id, None)

        logger.info("Execution cancelled", opportunity_id=opportunity_id)
        return True
