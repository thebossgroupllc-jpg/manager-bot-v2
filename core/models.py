from __future__ import annotations
from enum import Enum
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, field_validator

class SetupGrade(str, Enum):
    A_PLUS = "A+"; A = "A"; B = "B"

class Side(str, Enum):
    BUY = "buy"; SELL = "sell"

class Session(str, Enum):
    LONDON = "London"; NY = "NY"; ASIAN = "Asian"; SUNDAY = "Sunday"

class AccountMode(str, Enum):
    CONSERVATIVE = "conservative"; STANDARD = "standard"; AGGRESSIVE = "aggressive"

class DecisionReason(str, Enum):
    APPROVED = "approved"; DAILY_LOSS_HIT = "daily_loss_limit_hit"
    MAX_OPEN_RISK = "max_open_risk_reached"; SYMBOL_CONFLICT = "symbol_conflict"
    CORRELATION_LIMIT = "correlation_limit"; GRADE_TOO_LOW = "setup_grade_too_low"
    WRONG_SESSION = "wrong_session"; NEWS_LOCK = "news_lock_active"
    BOT_PAUSED = "bot_paused"; BOT_IN_DRAWDOWN = "bot_in_drawdown_throttle"
    MAX_TRADES_OPEN = "max_open_trades_reached"; OPPOSING_SIGNAL = "opposing_signal_conflict"
    INVALID_SCHEMA = "invalid_signal_schema"; DUPLICATE_SIGNAL = "duplicate_signal"

CORRELATION_GROUPS: Dict[str, List[str]] = {
    "usd_pairs": ["EURUSD","GBPUSD","USDCHF","USDCAD"],
    "jpy_pairs": ["USDJPY","GBPJPY","EURJPY","CADJPY"],
    "gold_usd":  ["XAUUSD","XAGUSD","EURUSD","GBPUSD"],
    "us_indices":["NAS100","US500","US30","GER40","UK100"],
    "gbp_pairs": ["GBPUSD","GBPJPY","GBPCAD","GBPCHF","GBPSGD","GBPNZD"],
}

def get_correlation_groups(symbol: str) -> List[str]:
    s = symbol.upper()
    return [g for g, syms in CORRELATION_GROUPS.items() if s in syms]

class InboundSignal(BaseModel):
    bot_id: str; strategy: str = "ict_v9"; symbol: str
    side: Side; entry_price: float = Field(..., gt=0)
    stop_loss: float = Field(..., gt=0); take_profit: float = Field(..., gt=0)
    setup_grade: SetupGrade; confidence: int = Field(default=80, ge=0, le=100)
    session: Session; score: int = Field(default=5, ge=0, le=12)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def norm(cls, v): return v.upper().replace("/","").replace("-","").replace("_","")

    @property
    def risk_pips(self): return abs(self.entry_price - self.stop_loss)
    @property
    def reward_pips(self): return abs(self.take_profit - self.entry_price)
    @property
    def rr_ratio(self): return self.reward_pips / self.risk_pips if self.risk_pips > 0 else 0
    @property
    def priority_score(self):
        g = {"A+":100,"A":85,"B":65}[self.setup_grade]
        s = {"NY":10,"London":8,"Asian":5,"Sunday":4}[self.session]
        return g + s + min(self.rr_ratio*5,20) + self.confidence*0.1

class ManagerDecision(BaseModel):
    signal_id: str; approved: bool; reason: DecisionReason
    adjusted_risk_pct: Optional[float] = None; adjusted_size: Optional[float] = None
    priority: Optional[float] = None; notes: List[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class OpenPosition(BaseModel):
    signal_id: str; bot_id: str; symbol: str; side: Side
    entry_price: float; stop_loss: float; take_profit: float
    size: float; risk_pct: float; setup_grade: SetupGrade; session: Session
    opened_at: datetime = Field(default_factory=datetime.utcnow)
    unrealised_pnl: float = 0.0; current_price: float = 0.0
    @property
    def correlation_groups(self): return get_correlation_groups(self.symbol)

class BotStats(BaseModel):
    bot_id: str; symbol: str; strategy: str
    total_trades: int = 0; wins: int = 0; losses: int = 0
    consecutive_losses: int = 0; gross_profit: float = 0.0; gross_loss: float = 0.0
    max_drawdown_pct: float = 0.0; current_streak: int = 0
    last_30_win_rate: float = 0.0; last_updated: datetime = Field(default_factory=datetime.utcnow)
    paused: bool = False; pause_reason: Optional[str] = None; size_multiplier: float = 1.0
    @property
    def win_rate(self): return self.wins/self.total_trades if self.total_trades else 0.0
    @property
    def profit_factor(self):
        if self.gross_loss == 0: return float("inf") if self.gross_profit > 0 else 0.0
        return self.gross_profit / abs(self.gross_loss)
    @property
    def live_score(self):
        wr = self.last_30_win_rate*30
        pf = min(self.profit_factor,5.0)/5.0*25
        dd = max(0, 20 - self.max_drawdown_pct*2)
        st = min(max(self.current_streak*2,-10),10)+10
        return wr+pf+dd+st

class RiskState(BaseModel):
    account_mode: AccountMode = AccountMode.STANDARD
    account_equity: float = 35000.0; daily_start_equity: float = 35000.0
    daily_pnl: float = 0.0; daily_pnl_pct: float = 0.0
    open_risk_pct: float = 0.0; open_positions: int = 0
    news_lock_active: bool = False; news_lock_reason: Optional[str] = None
    news_lock_until: Optional[datetime] = None
    last_updated: datetime = Field(default_factory=datetime.utcnow)
    @property
    def max_open_trades(self): return {"conservative":2,"standard":4,"aggressive":5}[self.account_mode]
    @property
    def max_open_risk_pct(self): return {"conservative":2.0,"standard":3.5,"aggressive":5.0}[self.account_mode]
    @property
    def daily_loss_limit_pct(self): return {"conservative":2.0,"standard":3.0,"aggressive":4.5}[self.account_mode]
    @property
    def daily_loss_limit_hit(self): return self.daily_pnl_pct <= -self.daily_loss_limit_pct
    @property
    def is_throttled(self): return self.daily_pnl_pct <= -(self.daily_loss_limit_pct*0.67)
    @property
    def allowed_grades(self):
        if self.daily_pnl_pct <= -(self.daily_loss_limit_pct*0.75): return [SetupGrade.A_PLUS]
        if self.is_throttled: return [SetupGrade.A_PLUS, SetupGrade.A]
        return list(SetupGrade)
    @property
    def risk_per_grade(self):
        base = {"A+":1.0,"A":0.75,"B":0.35}
        return {k:v*0.5 for k,v in base.items()} if self.is_throttled else base

class PortfolioState(BaseModel):
    positions: Dict[str, OpenPosition] = Field(default_factory=dict)
    pending_signals: List[InboundSignal] = Field(default_factory=list)
    @property
    def open_symbols(self): return [p.symbol for p in self.positions.values()]
    @property
    def total_open_risk_pct(self): return sum(p.risk_pct for p in self.positions.values())
    @property
    def open_count(self): return len(self.positions)
    def has_symbol(self, s): return s.upper() in self.open_symbols
    def get_direction_for_symbol(self, s):
        for p in self.positions.values():
            if p.symbol == s.upper(): return p.side
        return None
    def same_direction_correlated(self, symbol, side):
        my = set(get_correlation_groups(symbol)); count=0
        for p in self.positions.values():
            if set(get_correlation_groups(p.symbol)) & my and p.side==side: count+=1
        return count
