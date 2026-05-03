from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class Holding(BaseModel):
    ticker: str
    name: str
    isin: Optional[str] = None
    shares: float
    avg_cost: float                  # average cost per share (in position currency)
    latest_price: float              # most recent price per share
    currency: str
    cost_basis: float                # total amount invested
    current_value: float             # shares × latest_price
    gain_loss: float                 # current_value - cost_basis
    gain_loss_pct: float
    last_transaction: datetime


class PortfolioSummary(BaseModel):
    total_cost_basis: float
    total_current_value: float           # invested value + cash balance
    cash_balance: float                  # uninvested cash (deposits - withdrawals - cost_basis)
    total_gain_loss: float
    total_gain_loss_pct: float
    net_deposited: float                 # total deposits minus withdrawals
    cash_on_cash_gl: float               # total_current_value - net_deposited
    cash_on_cash_pct: float              # return on actual cash put in
    total_dividends: float
    total_interest: float
    currency: str                        # display currency (all values are in this)
    day_change: Optional[float] = None
    day_change_pct: Optional[float] = None
    holdings: list[Holding] = Field(default_factory=list)
    market_open: bool = False
    generated_at: datetime
    as_of: datetime                      # timestamp of most recent transaction in DB
