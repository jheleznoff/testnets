#!/usr/bin/env python3
"""
Altcoin Arbitrage Trading System

Main entry point for the automated arbitrage trading bot.
"""
import asyncio
import argparse
import signal
import sys
from decimal import Decimal
from datetime import datetime
from typing import Optional

import structlog
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text

from src.exchanges import ExchangeManager
from src.core import ArbitrageFinder, ArbitrageExecutor, ArbitrageOpportunity
from config.settings import settings

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()
console = Console()


class ArbitrageBot:
    """Main arbitrage trading bot."""

    def __init__(
        self,
        exchanges: Optional[list[str]] = None,
        min_profit_percent: Decimal = Decimal("0.5"),
        max_position_usd: Decimal = Decimal("1000"),
        dry_run: bool = True
    ):
        self.exchange_manager = ExchangeManager(exchange_ids=exchanges)
        self.min_profit_percent = min_profit_percent
        self.max_position_usd = max_position_usd
        self.dry_run = dry_run

        self.finder: Optional[ArbitrageFinder] = None
        self.executor: Optional[ArbitrageExecutor] = None

        self._running = False
        self._opportunities: list[ArbitrageOpportunity] = []
        self._stats = {
            "scans": 0,
            "opportunities_found": 0,
            "trades_executed": 0,
            "total_profit": Decimal("0"),
            "start_time": None
        }

    async def initialize(self) -> bool:
        """Initialize the bot and all connections."""
        console.print("[bold blue]Initializing Arbitrage Bot...[/bold blue]")

        success = await self.exchange_manager.initialize()

        if not success:
            console.print("[bold red]Failed to initialize exchanges[/bold red]")
            return False

        active_exchanges = self.exchange_manager.get_active_exchanges()
        console.print(f"[green]Connected to {len(active_exchanges)} exchanges: {', '.join(active_exchanges)}[/green]")

        self.finder = ArbitrageFinder(
            exchange_manager=self.exchange_manager,
            min_profit_percent=self.min_profit_percent,
            max_position_usd=self.max_position_usd,
            max_transfer_time_minutes=settings.risk.max_transfer_time_minutes
        )

        self.executor = ArbitrageExecutor(
            exchange_manager=self.exchange_manager,
            dry_run=self.dry_run
        )

        self._stats["start_time"] = datetime.utcnow()

        return True

    async def shutdown(self):
        """Graceful shutdown."""
        console.print("\n[yellow]Shutting down...[/yellow]")
        self._running = False
        await self.exchange_manager.close()
        console.print("[green]Shutdown complete[/green]")

    async def scan_once(self) -> list[ArbitrageOpportunity]:
        """Perform a single scan for opportunities."""
        if not self.finder:
            return []

        self._stats["scans"] += 1
        opportunities = await self.finder.discover_opportunities()
        self._stats["opportunities_found"] += len([o for o in opportunities if o.is_valid])

        self._opportunities = opportunities
        return opportunities

    async def run_continuous(self, interval_seconds: int = 10):
        """Run continuous scanning loop."""
        self._running = True

        console.print(f"\n[bold green]Starting continuous scan (interval: {interval_seconds}s)[/bold green]")
        console.print("[dim]Press Ctrl+C to stop[/dim]\n")

        while self._running:
            try:
                await self.scan_once()
                self._display_opportunities()

                # Auto-execute best opportunity if found
                valid_opps = [o for o in self._opportunities if o.is_valid]
                if valid_opps and not self.dry_run:
                    best = valid_opps[0]
                    console.print(f"\n[bold]Executing opportunity: {best.base_asset}/{best.quote_asset}[/bold]")
                    result = await self.executor.execute(best)

                    if result.status == "completed":
                        self._stats["trades_executed"] += 1
                        self._stats["total_profit"] += result.realized_profit_usd or Decimal("0")

                await asyncio.sleep(interval_seconds)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scan error: {e}")
                await asyncio.sleep(interval_seconds)

    def _display_opportunities(self):
        """Display current opportunities in a nice table."""
        table = Table(
            title=f"Arbitrage Opportunities ({datetime.utcnow().strftime('%H:%M:%S')})",
            show_header=True,
            header_style="bold cyan"
        )

        table.add_column("Asset", style="bold")
        table.add_column("Buy", style="green")
        table.add_column("Sell", style="red")
        table.add_column("Network")
        table.add_column("Spread %", justify="right")
        table.add_column("Net Profit %", justify="right")
        table.add_column("Profit USD", justify="right")
        table.add_column("Time (min)", justify="right")
        table.add_column("Risk", justify="right")
        table.add_column("Valid", justify="center")

        for opp in self._opportunities[:20]:  # Top 20
            risk_style = "green" if opp.risk_score < 30 else "yellow" if opp.risk_score < 60 else "red"
            valid_style = "green" if opp.is_valid else "red"
            profit_style = "green" if opp.net_profit_percent > 0 else "red"

            table.add_row(
                f"{opp.base_asset}/{opp.quote_asset}",
                opp.buy_exchange,
                opp.sell_exchange,
                opp.transfer_network,
                f"{opp.gross_spread_percent:.3f}%",
                Text(f"{opp.net_profit_percent:.3f}%", style=profit_style),
                Text(f"${opp.net_profit_usd:.2f}", style=profit_style),
                str(opp.estimated_transfer_minutes),
                Text(f"{opp.risk_score:.0f}", style=risk_style),
                Text("✓" if opp.is_valid else "✗", style=valid_style)
            )

        console.clear()
        console.print(self._get_stats_panel())
        console.print(table)

        if not self._opportunities:
            console.print("[dim]No opportunities found[/dim]")

    def _get_stats_panel(self) -> Panel:
        """Create stats panel."""
        uptime = ""
        if self._stats["start_time"]:
            delta = datetime.utcnow() - self._stats["start_time"]
            hours, remainder = divmod(delta.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        mode = "[yellow]DRY RUN[/yellow]" if self.dry_run else "[green]LIVE[/green]"

        stats_text = (
            f"Mode: {mode}  |  "
            f"Uptime: {uptime}  |  "
            f"Scans: {self._stats['scans']}  |  "
            f"Opportunities: {self._stats['opportunities_found']}  |  "
            f"Trades: {self._stats['trades_executed']}  |  "
            f"Profit: ${self._stats['total_profit']:.2f}"
        )

        return Panel(stats_text, title="[bold]Arbitrage Bot Stats[/bold]")


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Altcoin Arbitrage Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scan once with default settings
  python main.py scan

  # Run continuous monitoring (dry run)
  python main.py monitor --dry-run

  # Run with specific exchanges
  python main.py monitor --exchanges binance,bybit,okx

  # Run with custom profit threshold
  python main.py monitor --min-profit 1.0 --max-position 500
        """
    )

    parser.add_argument(
        "command",
        choices=["scan", "monitor", "discover"],
        help="Command to execute"
    )

    parser.add_argument(
        "--exchanges",
        type=str,
        default=None,
        help="Comma-separated list of exchanges (default: all supported)"
    )

    parser.add_argument(
        "--min-profit",
        type=float,
        default=0.5,
        help="Minimum profit percentage threshold (default: 0.5)"
    )

    parser.add_argument(
        "--max-position",
        type=float,
        default=1000,
        help="Maximum position size in USD (default: 1000)"
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=10,
        help="Scan interval in seconds (default: 10)"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Run in simulation mode (default: True)"
    )

    parser.add_argument(
        "--live",
        action="store_true",
        help="Run in live trading mode (use with caution!)"
    )

    args = parser.parse_args()

    # Parse exchanges
    exchanges = None
    if args.exchanges:
        exchanges = [e.strip() for e in args.exchanges.split(",")]

    # Determine mode
    dry_run = not args.live

    if not dry_run:
        console.print("[bold red]WARNING: Running in LIVE mode![/bold red]")
        console.print("Real trades will be executed. Press Ctrl+C within 5 seconds to abort.")
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            console.print("Aborted.")
            return

    bot = ArbitrageBot(
        exchanges=exchanges,
        min_profit_percent=Decimal(str(args.min_profit)),
        max_position_usd=Decimal(str(args.max_position)),
        dry_run=dry_run
    )

    # Setup signal handlers
    loop = asyncio.get_event_loop()

    def signal_handler():
        asyncio.create_task(bot.shutdown())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    try:
        if not await bot.initialize():
            sys.exit(1)

        if args.command == "scan":
            opportunities = await bot.scan_once()
            bot._display_opportunities()

        elif args.command == "discover":
            # Just discover arbitrageable assets without price check
            console.print("[bold]Discovering arbitrageable assets...[/bold]")
            assets = await bot.exchange_manager.find_arbitrageable_assets()

            table = Table(title="Arbitrageable Assets", show_header=True)
            table.add_column("Pair")
            table.add_column("Exchanges")
            table.add_column("Network Routes")

            for pair, data in list(assets.items())[:50]:
                exchanges_str = ", ".join(data["exchanges"])
                routes = [f"{r['from_exchange']}->{r['to_exchange']} ({r['network']})" for r in data["network_routes"][:3]]
                table.add_row(pair, exchanges_str, "\n".join(routes))

            console.print(table)
            console.print(f"\nTotal: {len(assets)} arbitrageable pairs")

        elif args.command == "monitor":
            await bot.run_continuous(interval_seconds=args.interval)

    finally:
        await bot.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
