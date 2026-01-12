"""
Risk management for arbitrage trading.
Evaluates and scores risks associated with arbitrage opportunities.
"""
from decimal import Decimal
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime
import structlog

from ..core.models import (
    ArbitrageOpportunity, NetworkInfo, OrderBook, AssetInfo
)
from .orderbook_analyzer import LiquidityAnalysis
from .profit_calculator import ProfitCalculation
from config.settings import settings

logger = structlog.get_logger()


@dataclass
class RiskFactor:
    """Individual risk factor."""
    name: str
    description: str
    score: Decimal  # 0-100, higher is riskier
    weight: Decimal  # Importance weight
    is_blocking: bool = False  # If True, blocks the trade entirely


@dataclass
class RiskAssessment:
    """Complete risk assessment for an arbitrage opportunity."""
    opportunity_id: str
    assessed_at: datetime = field(default_factory=datetime.utcnow)

    # Individual risk factors
    factors: list[RiskFactor] = field(default_factory=list)

    # Overall score (weighted average)
    overall_score: Decimal = Decimal("0")

    # Is the opportunity acceptable based on risk
    is_acceptable: bool = True

    # Blocking factors (if any)
    blocking_factors: list[str] = field(default_factory=list)

    # Recommendations
    recommendations: list[str] = field(default_factory=list)

    # Suggested adjustments
    suggested_amount_reduction: Optional[Decimal] = None


class RiskManager:
    """Evaluates and manages risk for arbitrage opportunities."""

    def __init__(
        self,
        max_acceptable_risk_score: Decimal = Decimal("60"),
        max_transfer_time_minutes: int = 30,
        max_price_slippage_percent: Decimal = Decimal("0.5"),
        min_liquidity_multiplier: Decimal = Decimal("3")
    ):
        self.max_acceptable_risk_score = max_acceptable_risk_score
        self.max_transfer_time_minutes = max_transfer_time_minutes
        self.max_price_slippage_percent = max_price_slippage_percent
        self.min_liquidity_multiplier = min_liquidity_multiplier

    def assess_opportunity(
        self,
        profit_calc: ProfitCalculation,
        withdrawal_network: NetworkInfo,
        deposit_network: NetworkInfo,
        buy_liquidity: LiquidityAnalysis,
        sell_liquidity: LiquidityAnalysis,
        price_volatility_24h: Optional[Decimal] = None,
        buy_asset_info: Optional[AssetInfo] = None,
        sell_asset_info: Optional[AssetInfo] = None
    ) -> RiskAssessment:
        """
        Perform comprehensive risk assessment for an arbitrage opportunity.
        """
        assessment = RiskAssessment(
            opportunity_id=datetime.utcnow().isoformat()
        )

        # 1. Transfer time risk
        transfer_risk = self._assess_transfer_time_risk(
            withdrawal_network,
            deposit_network
        )
        assessment.factors.append(transfer_risk)

        # 2. Liquidity risk
        liquidity_risk = self._assess_liquidity_risk(
            buy_liquidity,
            sell_liquidity,
            profit_calc.trade_amount
        )
        assessment.factors.append(liquidity_risk)

        # 3. Slippage risk
        slippage_risk = self._assess_slippage_risk(profit_calc)
        assessment.factors.append(slippage_risk)

        # 4. Volatility risk
        volatility_risk = self._assess_volatility_risk(
            price_volatility_24h,
            transfer_risk.score  # Higher transfer time = more exposure to volatility
        )
        assessment.factors.append(volatility_risk)

        # 5. Network/withdrawal risk
        network_risk = self._assess_network_risk(
            withdrawal_network,
            deposit_network,
            buy_asset_info,
            sell_asset_info
        )
        assessment.factors.append(network_risk)

        # 6. Profit margin risk
        margin_risk = self._assess_margin_risk(profit_calc)
        assessment.factors.append(margin_risk)

        # Calculate overall score
        total_weight = sum(f.weight for f in assessment.factors)
        if total_weight > 0:
            assessment.overall_score = sum(
                f.score * f.weight for f in assessment.factors
            ) / total_weight

        # Check for blocking factors
        for factor in assessment.factors:
            if factor.is_blocking:
                assessment.blocking_factors.append(factor.name)
                assessment.is_acceptable = False

        # Check overall threshold
        if assessment.overall_score > self.max_acceptable_risk_score:
            assessment.is_acceptable = False
            assessment.recommendations.append(
                f"Overall risk score ({assessment.overall_score:.1f}) exceeds threshold ({self.max_acceptable_risk_score})"
            )

        # Generate recommendations
        self._generate_recommendations(assessment, profit_calc)

        return assessment

    def _assess_transfer_time_risk(
        self,
        withdrawal_network: NetworkInfo,
        deposit_network: NetworkInfo
    ) -> RiskFactor:
        """Assess risk based on transfer time."""
        # Use the longer of withdrawal or deposit confirmation time
        estimated_time = max(
            withdrawal_network.estimated_arrival_minutes,
            deposit_network.estimated_arrival_minutes if deposit_network else 0
        )

        # Add time for confirmations
        confirmations = max(
            withdrawal_network.confirmations_required,
            deposit_network.confirmations_required if deposit_network else 0
        )

        # Estimate total time (rough approximation)
        total_time = estimated_time + (confirmations * 2)  # ~2 min per confirmation on average

        # Score: 0 for < 5 min, 100 for > 60 min
        if total_time <= 5:
            score = Decimal("10")
        elif total_time <= 15:
            score = Decimal("25")
        elif total_time <= 30:
            score = Decimal("50")
        elif total_time <= 60:
            score = Decimal("75")
        else:
            score = Decimal("95")

        is_blocking = total_time > self.max_transfer_time_minutes

        return RiskFactor(
            name="Transfer Time",
            description=f"Estimated {total_time} minutes ({confirmations} confirmations)",
            score=score,
            weight=Decimal("2.0"),  # High weight - time exposure is critical
            is_blocking=is_blocking
        )

    def _assess_liquidity_risk(
        self,
        buy_liquidity: LiquidityAnalysis,
        sell_liquidity: LiquidityAnalysis,
        trade_amount: Decimal
    ) -> RiskFactor:
        """Assess risk based on order book liquidity."""
        # Check if there's enough liquidity on both sides
        required_liquidity = trade_amount * self.min_liquidity_multiplier

        buy_sufficient = buy_liquidity.total_ask_volume >= required_liquidity
        sell_sufficient = sell_liquidity.total_bid_volume >= required_liquidity

        if buy_sufficient and sell_sufficient:
            # Calculate how much buffer we have
            buy_buffer = buy_liquidity.total_ask_volume / required_liquidity
            sell_buffer = sell_liquidity.total_bid_volume / required_liquidity
            min_buffer = min(buy_buffer, sell_buffer)

            if min_buffer >= 5:
                score = Decimal("10")
            elif min_buffer >= 3:
                score = Decimal("25")
            elif min_buffer >= 2:
                score = Decimal("40")
            else:
                score = Decimal("60")
        else:
            score = Decimal("90")

        is_blocking = not (buy_sufficient and sell_sufficient)

        return RiskFactor(
            name="Liquidity",
            description=f"Buy depth: {buy_liquidity.total_ask_volume:.4f}, Sell depth: {sell_liquidity.total_bid_volume:.4f}",
            score=score,
            weight=Decimal("1.5"),
            is_blocking=is_blocking
        )

    def _assess_slippage_risk(
        self,
        profit_calc: ProfitCalculation
    ) -> RiskFactor:
        """Assess risk based on expected slippage."""
        # Calculate effective slippage from prices
        # This assumes buy_price and sell_price already include slippage
        gross_spread = (
            (profit_calc.sell_price - profit_calc.buy_price) /
            profit_calc.buy_price * Decimal("100")
        )

        # If spread is tight, slippage risk is higher
        if gross_spread > Decimal("2"):
            score = Decimal("15")
        elif gross_spread > Decimal("1"):
            score = Decimal("30")
        elif gross_spread > Decimal("0.5"):
            score = Decimal("50")
        elif gross_spread > Decimal("0.2"):
            score = Decimal("70")
        else:
            score = Decimal("90")

        is_blocking = gross_spread < Decimal("0.1")

        return RiskFactor(
            name="Slippage",
            description=f"Gross spread: {gross_spread:.3f}%",
            score=score,
            weight=Decimal("1.0"),
            is_blocking=is_blocking
        )

    def _assess_volatility_risk(
        self,
        volatility_24h: Optional[Decimal],
        transfer_time_score: Decimal
    ) -> RiskFactor:
        """Assess risk based on price volatility."""
        if volatility_24h is None:
            # Unknown volatility - use moderate default
            volatility_24h = Decimal("5")  # Assume 5% daily volatility

        # Higher volatility + longer transfer time = higher risk
        time_multiplier = Decimal("1") + (transfer_time_score / Decimal("100"))

        adjusted_volatility = volatility_24h * time_multiplier

        if adjusted_volatility < Decimal("2"):
            score = Decimal("15")
        elif adjusted_volatility < Decimal("5"):
            score = Decimal("30")
        elif adjusted_volatility < Decimal("10"):
            score = Decimal("50")
        elif adjusted_volatility < Decimal("20"):
            score = Decimal("75")
        else:
            score = Decimal("95")

        return RiskFactor(
            name="Volatility",
            description=f"24h volatility: {volatility_24h:.2f}% (adjusted: {adjusted_volatility:.2f}%)",
            score=score,
            weight=Decimal("1.5"),
            is_blocking=False
        )

    def _assess_network_risk(
        self,
        withdrawal_network: NetworkInfo,
        deposit_network: NetworkInfo,
        buy_asset_info: Optional[AssetInfo],
        sell_asset_info: Optional[AssetInfo]
    ) -> RiskFactor:
        """Assess risk based on network status and capabilities."""
        issues = []

        # Check withdrawal status
        if not withdrawal_network.withdraw_enabled:
            issues.append("Withdrawals disabled")

        # Check deposit status
        if not deposit_network.deposit_enabled:
            issues.append("Deposits disabled")

        # Check for suspended/maintenance status
        from ..core.models import NetworkStatus
        if withdrawal_network.status in [NetworkStatus.SUSPENDED, NetworkStatus.MAINTENANCE]:
            issues.append(f"Withdrawal network: {withdrawal_network.status.value}")
        if deposit_network.status in [NetworkStatus.SUSPENDED, NetworkStatus.MAINTENANCE]:
            issues.append(f"Deposit network: {deposit_network.status.value}")

        if len(issues) == 0:
            score = Decimal("10")
            description = "All network operations enabled"
        elif len(issues) == 1:
            score = Decimal("70")
            description = "; ".join(issues)
        else:
            score = Decimal("100")
            description = "; ".join(issues)

        is_blocking = len(issues) > 0

        return RiskFactor(
            name="Network Status",
            description=description,
            score=score,
            weight=Decimal("2.0"),  # High weight - this can block trades entirely
            is_blocking=is_blocking
        )

    def _assess_margin_risk(
        self,
        profit_calc: ProfitCalculation
    ) -> RiskFactor:
        """Assess risk based on profit margin."""
        margin = profit_calc.net_profit_percent

        if margin >= Decimal("2"):
            score = Decimal("10")
        elif margin >= Decimal("1"):
            score = Decimal("25")
        elif margin >= Decimal("0.5"):
            score = Decimal("40")
        elif margin >= Decimal("0.2"):
            score = Decimal("60")
        elif margin > Decimal("0"):
            score = Decimal("80")
        else:
            score = Decimal("100")

        is_blocking = margin < Decimal("0")

        return RiskFactor(
            name="Profit Margin",
            description=f"Net profit: {margin:.3f}%",
            score=score,
            weight=Decimal("1.0"),
            is_blocking=is_blocking
        )

    def _generate_recommendations(
        self,
        assessment: RiskAssessment,
        profit_calc: ProfitCalculation
    ):
        """Generate actionable recommendations based on risk assessment."""
        for factor in assessment.factors:
            if factor.score >= Decimal("70"):
                if factor.name == "Liquidity":
                    assessment.recommendations.append(
                        "Consider reducing trade size due to limited liquidity"
                    )
                    assessment.suggested_amount_reduction = Decimal("0.5")

                elif factor.name == "Transfer Time":
                    assessment.recommendations.append(
                        "Long transfer time increases volatility exposure - consider faster networks"
                    )

                elif factor.name == "Volatility":
                    assessment.recommendations.append(
                        "High volatility - reduce position size or wait for calmer market"
                    )
                    if assessment.suggested_amount_reduction is None:
                        assessment.suggested_amount_reduction = Decimal("0.7")
                    else:
                        assessment.suggested_amount_reduction *= Decimal("0.7")

                elif factor.name == "Slippage":
                    assessment.recommendations.append(
                        "Tight spread - consider smaller trade to reduce slippage impact"
                    )

                elif factor.name == "Profit Margin":
                    assessment.recommendations.append(
                        "Thin margin - ensure all fees are accounted for"
                    )

    def calculate_position_size(
        self,
        max_position_usd: Decimal,
        risk_assessment: RiskAssessment,
        current_price: Decimal
    ) -> Decimal:
        """Calculate recommended position size based on risk."""
        base_size = max_position_usd / current_price

        # Reduce size based on risk score
        risk_reduction = Decimal("1") - (risk_assessment.overall_score / Decimal("200"))
        risk_reduction = max(risk_reduction, Decimal("0.2"))  # Minimum 20% of max

        # Apply suggested reduction if any
        if risk_assessment.suggested_amount_reduction:
            risk_reduction *= risk_assessment.suggested_amount_reduction

        return base_size * risk_reduction

    def should_execute(self, assessment: RiskAssessment) -> tuple[bool, str]:
        """Determine if a trade should be executed based on risk assessment."""
        if assessment.blocking_factors:
            return False, f"Blocked by: {', '.join(assessment.blocking_factors)}"

        if not assessment.is_acceptable:
            return False, f"Risk score {assessment.overall_score:.1f} exceeds threshold"

        return True, "Risk acceptable"
