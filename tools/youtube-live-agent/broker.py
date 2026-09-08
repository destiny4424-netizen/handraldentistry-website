"""Alpaca paper-trading broker wrapper.

SAFETY: `paper=True` is hard-coded below and is not exposed as a CLI
option anywhere in this tool. This agent must never be able to place a
real-money order -- if you want to eventually trade live, do that
deliberately and separately, outside of this experimental pipeline.
"""
from __future__ import annotations


class PaperBroker:
    def __init__(self, api_key: str, secret_key: str):
        try:
            from alpaca.trading.client import TradingClient
        except ImportError as e:
            raise SystemExit(
                "alpaca-py is not installed. Run: pip install -r requirements.txt"
            ) from e
        self.client = TradingClient(api_key, secret_key, paper=True)

    def place_paper_order(
        self, symbol: str, side: str, asset_class: str, notional: float
    ) -> dict:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        order_symbol = f"{symbol}/USD" if asset_class == "crypto" else symbol
        order_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
        time_in_force = TimeInForce.GTC if asset_class == "crypto" else TimeInForce.DAY

        request = MarketOrderRequest(
            symbol=order_symbol,
            notional=notional,
            side=order_side,
            time_in_force=time_in_force,
        )
        order = self.client.submit_order(request)
        return {
            "id": str(order.id),
            "symbol": order_symbol,
            "side": side,
            "notional": notional,
            "status": str(order.status),
        }
