from __future__ import annotations
import uuid, logging
from datetime import datetime
from typing import Tuple, List, Optional, Dict
from .models import (InboundSignal, ManagerDecision, PortfolioState, RiskState,
                     BotStats, OpenPosition, DecisionReason, SetupGrade, get_correlation_groups)

logger = logging.getLogger("engine")

SESSION_ROUTING = {
    "bot_01_gbpusd":["London","NY"],"bot_02_xauusd":["London","NY"],
    "bot_03_usdjpy":["NY","Asian"],"bot_04_nas100":["NY"],"bot_05_us500":["NY"],
    "bot_06_asian_usdjpy":["Asian","Sunday"],"bot_07_asian_gbpjpy":["Asian","Sunday"],
    "bot_08_asian_eurjpy":["Asian","Sunday"],"bot_09_asian_gbpsgd":["Asian","Sunday"],
}
MAX_CORRELATED = {"usd_pairs":2,"jpy_pairs":2,"gold_usd":1,"us_indices":2,"gbp_pairs":2}

class ApprovalEngine:
    def __init__(self): self._seen = set()
    def approve(self, signal, portfolio, risk, bots) -> ManagerDecision:
        sid = str(uuid.uuid4()); notes=[]
        for check in [self._dup,self._paused,self._news,self._session,
                      self._daily,self._max_trades,self._max_risk,
                      self._sym,self._opposing,self._corr,self._grade]:
            r = check(signal, portfolio, risk, bots, notes)
            if r: return ManagerDecision(signal_id=sid,approved=False,reason=r,notes=notes)
        rp, sz, en = self._size(signal, portfolio, risk, bots)
        notes.extend(en)
        self._seen.add(self._fp(signal))
        return ManagerDecision(signal_id=sid,approved=True,reason=DecisionReason.APPROVED,
                               adjusted_risk_pct=rp,adjusted_size=sz,priority=signal.priority_score,notes=notes)
    def _fp(self, s): return f"{s.bot_id}:{s.symbol}:{s.side}:{s.timestamp.strftime('%Y%m%d%H%M')}"
    def _dup(self,s,p,r,b,n):
        if self._fp(s) in self._seen: n.append("Duplicate"); return DecisionReason.DUPLICATE_SIGNAL
    def _paused(self,s,p,r,b,n):
        st=b.get(s.bot_id)
        if st and st.paused: n.append(f"Bot paused: {st.pause_reason}"); return DecisionReason.BOT_PAUSED
        if st and st.consecutive_losses>=5: n.append("5 consecutive losses"); return DecisionReason.BOT_IN_DRAWDOWN
    def _news(self,s,p,r,b,n):
        if r.news_lock_active: n.append(f"News lock: {r.news_lock_reason}"); return DecisionReason.NEWS_LOCK
    def _session(self,s,p,r,b,n):
        allowed=SESSION_ROUTING.get(s.bot_id)
        if allowed and s.session.value not in allowed: n.append(f"Wrong session"); return DecisionReason.WRONG_SESSION
    def _daily(self,s,p,r,b,n):
        if r.daily_loss_limit_hit: n.append("Daily limit"); return DecisionReason.DAILY_LOSS_HIT
    def _max_trades(self,s,p,r,b,n):
        if p.open_count>=r.max_open_trades: n.append("Max trades"); return DecisionReason.MAX_TRADES_OPEN
    def _max_risk(self,s,p,r,b,n):
        if p.total_open_risk_pct>=r.max_open_risk_pct: n.append("Max risk"); return DecisionReason.MAX_OPEN_RISK
    def _sym(self,s,p,r,b,n):
        if p.has_symbol(s.symbol): n.append("Symbol open"); return DecisionReason.SYMBOL_CONFLICT
    def _opposing(self,s,p,r,b,n):
        for pd in p.pending_signals:
            if pd.symbol==s.symbol and pd.side!=s.side and pd.bot_id!=s.bot_id:
                if pd.priority_score>=s.priority_score: n.append("Opposing signal"); return DecisionReason.OPPOSING_SIGNAL
    def _corr(self,s,p,r,b,n):
        for g in get_correlation_groups(s.symbol):
            lim=MAX_CORRELATED.get(g,2)
            if p.same_direction_correlated(s.symbol,s.side)>=lim:
                n.append(f"Correlation {g}"); return DecisionReason.CORRELATION_LIMIT
    def _grade(self,s,p,r,b,n):
        if s.setup_grade not in r.allowed_grades: n.append("Grade too low"); return DecisionReason.GRADE_TOO_LOW
    def _size(self,s,p,r,b):
        notes=[]
        base=r.risk_per_grade[s.setup_grade.value]
        bm=1.0
        st=b.get(s.bot_id)
        if st: bm=min(st.size_multiplier,1.5) if st.live_score>=75 else (0.5 if st.live_score<40 else st.size_multiplier)
        if s.session.value in ("Asian","Sunday"): bm*=0.5; notes.append("Asian 50% size")
        remaining=r.max_open_risk_pct-p.total_open_risk_pct
        rp=min(base*bm,remaining); rp=max(rp,0.1)
        pip=abs(s.entry_price-s.stop_loss)
        sz=(r.account_equity*(rp/100))/pip if pip>0 else 0.01
        notes.append(f"Risk:{rp:.2f}% size:{sz:.4f}")
        return round(rp,3),round(sz,4),notes

class BotRankingEngine:
    @staticmethod
    def rank_and_update(bots):
        if not bots: return bots
        scores={bid:s.live_score for bid,s in bots.items()}
        mx=max(scores.values()); mn=min(scores.values()); rng=mx-mn or 1
        for bid,s in bots.items():
            s.size_multiplier=round(0.5+(scores[bid]-mn)/rng*1.0,2)
            if s.consecutive_losses>=5 and not s.paused:
                s.paused=True; s.pause_reason=f"Auto: {s.consecutive_losses} losses"
            if s.paused and "Auto:" in (s.pause_reason or "") and s.consecutive_losses==0:
                s.paused=False; s.pause_reason=None
        return bots
    @staticmethod
    def record_trade_result(bot_id,won,pnl,bots):
        s=bots.setdefault(bot_id,BotStats(bot_id=bot_id,symbol="",strategy=""))
        s.total_trades+=1
        if won: s.wins+=1;s.gross_profit+=pnl;s.consecutive_losses=0;s.current_streak=max(s.current_streak+1,1)
        else: s.losses+=1;s.gross_loss+=abs(pnl);s.consecutive_losses+=1;s.current_streak=min(s.current_streak-1,-1)
        s.last_30_win_rate=s.wins/s.total_trades if s.total_trades else 0
        s.last_updated=datetime.utcnow()
        return s
