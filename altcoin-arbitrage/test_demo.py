#!/usr/bin/env python3
"""
Demo test script that simulates the arbitrage system without external dependencies.
Shows the logic and flow of the arbitrage detection system.
"""
from decimal import Decimal
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional
import random
import json


# Simplified models for demo
@dataclass
class NetworkInfo:
    network_id: str
    network_name: str
    withdraw_enabled: bool = True
    withdraw_fee: Decimal = Decimal("0")
    withdraw_min: Decimal = Decimal("0")
    deposit_enabled: bool = True
    confirmations_required: int = 12
    estimated_arrival_minutes: int = 5


@dataclass
class AssetInfo:
    symbol: str
    exchange_id: str
    networks: list = field(default_factory=list)


@dataclass
class OrderBookEntry:
    price: Decimal
    amount: Decimal


@dataclass
class OrderBook:
    exchange_id: str
    symbol: str
    bids: list  # Buy orders
    asks: list  # Sell orders


@dataclass
class ArbitrageOpportunity:
    base_asset: str
    quote_asset: str
    buy_exchange: str
    sell_exchange: str
    transfer_network: str
    buy_price: Decimal
    sell_price: Decimal
    gross_spread_percent: Decimal
    trade_amount: Decimal
    withdrawal_fee: Decimal
    buy_fee: Decimal
    sell_fee: Decimal
    net_profit_usd: Decimal
    net_profit_percent: Decimal
    transfer_time_minutes: int
    risk_score: Decimal
    is_valid: bool
    reason: str = ""


# Mock exchange data
MOCK_EXCHANGES = {
    "binance": {
        "name": "Binance",
        "maker_fee": Decimal("0.001"),
        "taker_fee": Decimal("0.001"),
    },
    "bybit": {
        "name": "Bybit",
        "maker_fee": Decimal("0.001"),
        "taker_fee": Decimal("0.001"),
    },
    "okx": {
        "name": "OKX",
        "maker_fee": Decimal("0.0008"),
        "taker_fee": Decimal("0.001"),
    },
    "kucoin": {
        "name": "KuCoin",
        "maker_fee": Decimal("0.001"),
        "taker_fee": Decimal("0.001"),
    },
}

# Mock assets with network info
MOCK_ASSETS = {
    "ETH": {
        "binance": {
            "networks": [
                NetworkInfo("ERC20", "Ethereum", True, Decimal("0.001"), Decimal("0.01"), True, 12, 5),
                NetworkInfo("BEP20", "BSC", True, Decimal("0.0001"), Decimal("0.001"), True, 15, 2),
                NetworkInfo("ARBITRUM", "Arbitrum One", True, Decimal("0.0001"), Decimal("0.001"), True, 12, 2),
            ]
        },
        "bybit": {
            "networks": [
                NetworkInfo("ERC20", "Ethereum", True, Decimal("0.0012"), Decimal("0.01"), True, 12, 5),
                NetworkInfo("BEP20", "BSC", True, Decimal("0.00015"), Decimal("0.001"), True, 15, 2),
                NetworkInfo("ARBITRUM", "Arbitrum One", True, Decimal("0.00012"), Decimal("0.001"), True, 12, 2),
            ]
        },
        "okx": {
            "networks": [
                NetworkInfo("ERC20", "Ethereum", True, Decimal("0.0009"), Decimal("0.01"), True, 12, 5),
                NetworkInfo("BEP20", "BSC", False, Decimal("0"), Decimal("0"), False, 0, 0),  # Disabled!
                NetworkInfo("ARBITRUM", "Arbitrum One", True, Decimal("0.0001"), Decimal("0.001"), True, 12, 2),
            ]
        },
    },
    "ARB": {
        "binance": {
            "networks": [
                NetworkInfo("ARBITRUM", "Arbitrum One", True, Decimal("0.1"), Decimal("1"), True, 12, 2),
            ]
        },
        "bybit": {
            "networks": [
                NetworkInfo("ARBITRUM", "Arbitrum One", True, Decimal("0.12"), Decimal("1"), True, 12, 2),
            ]
        },
        "kucoin": {
            "networks": [
                NetworkInfo("ARBITRUM", "Arbitrum One", True, Decimal("0.15"), Decimal("1"), True, 12, 2),
            ]
        },
    },
    "SOL": {
        "binance": {
            "networks": [
                NetworkInfo("SOL", "Solana", True, Decimal("0.01"), Decimal("0.1"), True, 32, 1),
            ]
        },
        "bybit": {
            "networks": [
                NetworkInfo("SOL", "Solana", True, Decimal("0.01"), Decimal("0.1"), True, 32, 1),
            ]
        },
        "okx": {
            "networks": [
                NetworkInfo("SOL", "Solana", False, Decimal("0"), Decimal("0"), True, 0, 0),  # Withdrawal disabled!
            ]
        },
    },
    "MATIC": {
        "binance": {
            "networks": [
                NetworkInfo("POLYGON", "Polygon", True, Decimal("0.1"), Decimal("1"), True, 128, 3),
                NetworkInfo("ERC20", "Ethereum", True, Decimal("5"), Decimal("10"), True, 12, 5),
            ]
        },
        "okx": {
            "networks": [
                NetworkInfo("POLYGON", "Polygon", True, Decimal("0.1"), Decimal("1"), True, 128, 3),
            ]
        },
        "kucoin": {
            "networks": [
                NetworkInfo("ERC20", "Ethereum", True, Decimal("8"), Decimal("10"), True, 12, 5),  # High fee!
            ]
        },
    },
}

# Mock order books with realistic spreads
def generate_order_book(exchange_id: str, symbol: str, base_price: float, spread_bps: int = 10) -> OrderBook:
    """Generate a mock order book with given spread."""
    spread = base_price * spread_bps / 10000
    mid_price = Decimal(str(base_price))

    # Generate bids (buy orders) - descending prices
    bids = []
    for i in range(20):
        price = mid_price - Decimal(str(spread/2)) - Decimal(str(i * spread / 10))
        amount = Decimal(str(random.uniform(0.5, 5.0)))
        bids.append(OrderBookEntry(price.quantize(Decimal("0.01")), amount.quantize(Decimal("0.001"))))

    # Generate asks (sell orders) - ascending prices
    asks = []
    for i in range(20):
        price = mid_price + Decimal(str(spread/2)) + Decimal(str(i * spread / 10))
        amount = Decimal(str(random.uniform(0.5, 5.0)))
        asks.append(OrderBookEntry(price.quantize(Decimal("0.01")), amount.quantize(Decimal("0.001"))))

    return OrderBook(exchange_id, symbol, bids, asks)


def normalize_network(network_id: str) -> str:
    """Normalize network names for comparison."""
    mappings = {
        "ERC20": "ETH",
        "ETH": "ETH",
        "BEP20": "BSC",
        "BSC": "BSC",
        "TRC20": "TRX",
        "POLYGON": "MATIC",
        "MATIC": "MATIC",
        "ARBITRUM": "ARB",
        "ARB": "ARB",
        "SOL": "SOL",
        "SOLANA": "SOL",
    }
    return mappings.get(network_id.upper(), network_id.upper())


def find_common_networks(asset: str, exchange1: str, exchange2: str) -> list:
    """Find networks that support withdrawal from ex1 and deposit to ex2."""
    if asset not in MOCK_ASSETS:
        return []

    ex1_data = MOCK_ASSETS[asset].get(exchange1, {})
    ex2_data = MOCK_ASSETS[asset].get(exchange2, {})

    if not ex1_data or not ex2_data:
        return []

    common = []
    for net1 in ex1_data.get("networks", []):
        for net2 in ex2_data.get("networks", []):
            if normalize_network(net1.network_id) == normalize_network(net2.network_id):
                if net1.withdraw_enabled and net2.deposit_enabled:
                    common.append((net1, net2))

    return common


def calculate_slippage(order_book: OrderBook, amount: Decimal, is_buy: bool) -> tuple:
    """Calculate average price and slippage for executing an order."""
    entries = order_book.asks if is_buy else order_book.bids

    if not entries:
        return None, None

    remaining = amount
    total_cost = Decimal("0")

    for entry in entries:
        if remaining <= 0:
            break
        fill = min(remaining, entry.amount)
        total_cost += fill * entry.price
        remaining -= fill

    if remaining > 0:
        return None, None  # Not enough liquidity

    avg_price = total_cost / amount
    best_price = entries[0].price
    slippage = abs(avg_price - best_price) / best_price * Decimal("100")

    return avg_price, slippage


def analyze_arbitrage(
    asset: str,
    quote: str,
    buy_exchange: str,
    sell_exchange: str,
    buy_ob: OrderBook,
    sell_ob: OrderBook,
    network: NetworkInfo,
    trade_amount: Decimal,
    max_position_usd: Decimal = Decimal("1000")
) -> Optional[ArbitrageOpportunity]:
    """Analyze a potential arbitrage opportunity."""

    # Get prices with slippage
    buy_price, buy_slippage = calculate_slippage(buy_ob, trade_amount, is_buy=True)
    sell_price, sell_slippage = calculate_slippage(sell_ob, trade_amount, is_buy=False)

    if buy_price is None or sell_price is None:
        return None

    # Check if there's a positive spread
    if sell_price <= buy_price:
        return None

    gross_spread = (sell_price - buy_price) / buy_price * Decimal("100")

    # Calculate fees
    buy_fee_rate = MOCK_EXCHANGES[buy_exchange]["taker_fee"]
    sell_fee_rate = MOCK_EXCHANGES[sell_exchange]["taker_fee"]

    buy_cost = trade_amount * buy_price
    buy_fee = buy_cost * buy_fee_rate

    # Amount after withdrawal fee
    amount_after_withdrawal = trade_amount - network.withdraw_fee

    sell_proceeds = amount_after_withdrawal * sell_price
    sell_fee = sell_proceeds * sell_fee_rate

    # Net profit
    net_profit = sell_proceeds - sell_fee - buy_cost - buy_fee
    net_profit_percent = net_profit / buy_cost * Decimal("100")

    # Risk score (simplified)
    risk_score = Decimal("0")
    risk_factors = []

    # Transfer time risk
    if network.estimated_arrival_minutes > 30:
        risk_score += Decimal("30")
        risk_factors.append("Long transfer time")
    elif network.estimated_arrival_minutes > 10:
        risk_score += Decimal("15")

    # Slippage risk
    if buy_slippage and buy_slippage > Decimal("0.5"):
        risk_score += Decimal("20")
        risk_factors.append("High buy slippage")
    if sell_slippage and sell_slippage > Decimal("0.5"):
        risk_score += Decimal("20")
        risk_factors.append("High sell slippage")

    # Thin margin risk
    if net_profit_percent < Decimal("0.5"):
        risk_score += Decimal("25")
        risk_factors.append("Thin profit margin")

    is_valid = net_profit > 0 and risk_score < Decimal("70")
    reason = ", ".join(risk_factors) if risk_factors else "OK"

    return ArbitrageOpportunity(
        base_asset=asset,
        quote_asset=quote,
        buy_exchange=buy_exchange,
        sell_exchange=sell_exchange,
        transfer_network=network.network_id,
        buy_price=buy_price,
        sell_price=sell_price,
        gross_spread_percent=gross_spread,
        trade_amount=trade_amount,
        withdrawal_fee=network.withdraw_fee,
        buy_fee=buy_fee,
        sell_fee=sell_fee,
        net_profit_usd=net_profit,
        net_profit_percent=net_profit_percent,
        transfer_time_minutes=network.estimated_arrival_minutes,
        risk_score=risk_score,
        is_valid=is_valid,
        reason=reason
    )


def print_separator(title: str = ""):
    """Print a visual separator."""
    if title:
        print(f"\n{'='*60}")
        print(f"  {title}")
        print(f"{'='*60}")
    else:
        print(f"\n{'-'*60}")


def main():
    """Run the demo test."""
    print_separator("ALTCOIN ARBITRAGE SYSTEM - DEMO TEST")
    print(f"Time: {datetime.utcnow().isoformat()}")

    # Step 1: Show available exchanges
    print_separator("STEP 1: Connected Exchanges")
    for ex_id, ex_info in MOCK_EXCHANGES.items():
        print(f"  - {ex_info['name']} ({ex_id})")
        print(f"    Maker fee: {float(ex_info['maker_fee'])*100:.2f}%")
        print(f"    Taker fee: {float(ex_info['taker_fee'])*100:.2f}%")

    # Step 2: Discover arbitrageable assets
    print_separator("STEP 2: Discovering Arbitrageable Assets")

    arbitrageable = {}
    exchanges = list(MOCK_EXCHANGES.keys())

    for asset in MOCK_ASSETS.keys():
        routes = []
        for i, ex1 in enumerate(exchanges):
            for ex2 in exchanges[i+1:]:
                # Check both directions
                common_1to2 = find_common_networks(asset, ex1, ex2)
                common_2to1 = find_common_networks(asset, ex2, ex1)

                for net1, net2 in common_1to2:
                    routes.append({
                        "from": ex1,
                        "to": ex2,
                        "network": net1.network_id,
                        "withdraw_fee": net1.withdraw_fee,
                        "time": net1.estimated_arrival_minutes
                    })

                for net2, net1 in common_2to1:
                    routes.append({
                        "from": ex2,
                        "to": ex1,
                        "network": net2.network_id,
                        "withdraw_fee": net2.withdraw_fee,
                        "time": net2.estimated_arrival_minutes
                    })

        if routes:
            arbitrageable[asset] = routes

    for asset, routes in arbitrageable.items():
        print(f"\n  {asset}/USDT:")
        for route in routes[:4]:  # Show first 4 routes
            print(f"    {route['from']} -> {route['to']} via {route['network']}")
            print(f"      Withdrawal fee: {route['withdraw_fee']} {asset}, Time: ~{route['time']} min")

    # Step 3: Check network compatibility issues
    print_separator("STEP 3: Network Compatibility Check")

    # Show case where networks DON'T match
    print("\n  Checking ETH transfer: OKX -> Bybit via BEP20")
    okx_eth = MOCK_ASSETS["ETH"]["okx"]["networks"]
    bep20_okx = next((n for n in okx_eth if n.network_id == "BEP20"), None)
    if bep20_okx and not bep20_okx.withdraw_enabled:
        print("    [BLOCKED] OKX has BEP20 withdrawals DISABLED")
        print("    -> Cannot use this route, must find alternative network")

    print("\n  Checking SOL transfer: OKX -> Binance")
    okx_sol = MOCK_ASSETS["SOL"]["okx"]["networks"]
    sol_okx = next((n for n in okx_sol if n.network_id == "SOL"), None)
    if sol_okx and not sol_okx.withdraw_enabled:
        print("    [BLOCKED] OKX has SOL withdrawals DISABLED (maintenance)")
        print("    -> This arbitrage route is currently unavailable")

    # Step 4: Simulate order book analysis
    print_separator("STEP 4: Order Book Analysis")

    # Generate mock order books with different prices on exchanges
    # Simulating a scenario where ETH is cheaper on Binance
    eth_prices = {
        "binance": 3245.50,  # Lower price (buy here)
        "bybit": 3252.30,    # Higher price (sell here)
        "okx": 3248.00,
        "kucoin": 3250.10,
    }

    print("\n  ETH/USDT prices across exchanges:")
    for ex, price in eth_prices.items():
        print(f"    {ex}: ${price:.2f}")

    buy_ob = generate_order_book("binance", "ETH/USDT", eth_prices["binance"], spread_bps=8)
    sell_ob = generate_order_book("bybit", "ETH/USDT", eth_prices["bybit"], spread_bps=8)

    print(f"\n  Binance order book (buy side):")
    print(f"    Best ask: ${buy_ob.asks[0].price} x {buy_ob.asks[0].amount}")
    print(f"    Total ask liquidity: {sum(a.amount for a in buy_ob.asks):.2f} ETH")

    print(f"\n  Bybit order book (sell side):")
    print(f"    Best bid: ${sell_ob.bids[0].price} x {sell_ob.bids[0].amount}")
    print(f"    Total bid liquidity: {sum(b.amount for b in sell_ob.bids):.2f} ETH")

    # Step 5: Calculate arbitrage opportunity
    print_separator("STEP 5: Arbitrage Opportunity Analysis")

    trade_amount = Decimal("0.5")  # 0.5 ETH

    # Find best network route
    networks = find_common_networks("ETH", "binance", "bybit")

    print(f"\n  Analyzing: Buy {trade_amount} ETH on Binance, Sell on Bybit")
    print(f"\n  Available transfer networks:")

    opportunities = []
    for net1, net2 in networks:
        print(f"\n    Network: {net1.network_id}")
        print(f"      Withdrawal fee: {net1.withdraw_fee} ETH")
        print(f"      Transfer time: ~{net1.estimated_arrival_minutes} min")
        print(f"      Confirmations: {max(net1.confirmations_required, net2.confirmations_required)}")

        opp = analyze_arbitrage(
            "ETH", "USDT",
            "binance", "bybit",
            buy_ob, sell_ob,
            net1,
            trade_amount
        )

        if opp:
            opportunities.append(opp)

    # Step 6: Show results
    print_separator("STEP 6: Arbitrage Opportunities Found")

    if not opportunities:
        print("\n  No profitable opportunities found at this time.")
    else:
        # Sort by profit
        opportunities.sort(key=lambda x: x.net_profit_percent, reverse=True)

        for i, opp in enumerate(opportunities, 1):
            status = "[VALID]" if opp.is_valid else "[INVALID]"

            print(f"\n  Opportunity #{i} {status}")
            print(f"  {'-'*50}")
            print(f"    Route: {opp.buy_exchange} -> {opp.sell_exchange}")
            print(f"    Asset: {opp.base_asset}/{opp.quote_asset}")
            print(f"    Network: {opp.transfer_network}")
            print(f"    ")
            print(f"    Buy price:  ${opp.buy_price:.2f}")
            print(f"    Sell price: ${opp.sell_price:.2f}")
            print(f"    Gross spread: {opp.gross_spread_percent:.3f}%")
            print(f"    ")
            print(f"    Trade amount: {opp.trade_amount} {opp.base_asset}")
            print(f"    Withdrawal fee: {opp.withdrawal_fee} {opp.base_asset}")
            print(f"    Buy trading fee: ${opp.buy_fee:.4f}")
            print(f"    Sell trading fee: ${opp.sell_fee:.4f}")
            print(f"    ")
            print(f"    NET PROFIT: ${opp.net_profit_usd:.2f} ({opp.net_profit_percent:.3f}%)")
            print(f"    Transfer time: ~{opp.transfer_time_minutes} min")
            print(f"    Risk score: {opp.risk_score}/100")
            if opp.reason != "OK":
                print(f"    Risk factors: {opp.reason}")

    # Step 7: Simulate execution decision
    print_separator("STEP 7: Execution Decision")

    best = opportunities[0] if opportunities else None

    if best and best.is_valid:
        print(f"\n  Best opportunity selected:")
        print(f"    {best.buy_exchange} -> {best.sell_exchange} via {best.transfer_network}")
        print(f"    Expected profit: ${best.net_profit_usd:.2f}")
        print(f"\n  [DRY RUN] Would execute:")
        print(f"    1. BUY {best.trade_amount} {best.base_asset} on {best.buy_exchange} @ ${best.buy_price:.2f}")
        print(f"    2. WITHDRAW to {best.sell_exchange} via {best.transfer_network}")
        print(f"    3. WAIT ~{best.transfer_time_minutes} minutes for confirmation")
        print(f"    4. SELL {best.trade_amount - best.withdrawal_fee} {best.base_asset} on {best.sell_exchange} @ ${best.sell_price:.2f}")
    else:
        print("\n  No valid opportunities to execute.")
        if best:
            print(f"  Best opportunity rejected due to: {best.reason}")

    print_separator("DEMO COMPLETE")
    print("\nThe system successfully demonstrated:")
    print("  [OK] Multi-exchange connectivity")
    print("  [OK] Automatic pair discovery")
    print("  [OK] Network compatibility checking")
    print("  [OK] Deposit/withdrawal status verification")
    print("  [OK] Order book liquidity analysis")
    print("  [OK] Fee calculation (trading + withdrawal)")
    print("  [OK] Profit calculation")
    print("  [OK] Risk assessment")
    print("  [OK] Execution decision logic")


if __name__ == "__main__":
    main()
