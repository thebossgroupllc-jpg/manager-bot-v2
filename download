"""
ICT v9 Manager Bot v2 — FastAPI Application
Upgraded with:
  - Auto economic calendar (no manual Sunday toggling)
  - Telegram notifications to your phone
  - /calendar endpoints to view upcoming events
  - /news/status to see current lock state
  - Auto daily summary at 5 PM NY
  - Sunday briefing auto-sent at 4 PM NY
"""

from __future__ import annotations
import asyncio
import json
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# Core models and engine
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from core.models import (
    InboundSignal, PortfolioState, RiskState, BotStats,
    OpenPosition, AccountMode, DecisionReason
)
from core.engine import ApprovalEngine, BotRankingEngine
from news.calendar import CalendarManager
from news.scheduler import NewsScheduler
from notifications.telegram import TelegramNotifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("manager_v2")

NY_TZ = ZoneInfo("America/New_York")

# ── Env vars ──────────────────────────────────────────────────────
STARTING_EQUITY = float(os.getenv("STARTING_EQUITY", "35000"))
ENVIRONMENT     = os.getenv("ENVIRONMENT", "development")

# ── App ───────────────────────────────────────────────────────────
app = FastAPI(title="ICT v9 Manager Bot v2", version="9.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Global state ──────────────────────────────────────────────────
engine         = ApprovalEngine()
ranking_engine = BotRankingEngine()
portfolio      = PortfolioState()
risk           = RiskState(account_equity=STARTING_EQUITY, daily_start_equity=STARTING_EQUITY)
bots: Dict[str, BotStats]          = {}
decision_log: List[Dict[str, Any]] = []
ws_clients: List[WebSocket]        = []

# ── News + notifications ──────────────────────────────────────────
telegram  = TelegramNotifier()
calendar  = CalendarManager()
scheduler = NewsScheduler(risk, telegram, calendar)

# ══════════════════════════════════════════════════════════════════
# STARTUP
# ══════════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup():
    logger.info("Starting ICT v9 Manager Bot v2...")

    # Start auto news scheduler as background task
    asyncio.create_task(scheduler.start())

    # Start daily summary scheduler
    asyncio.create_task(_daily_summary_loop())

    # Send startup notification
    await telegram.send_startup(STARTING_EQUITY, risk.account_mode.value)

    logger.info("Manager Bot v2 online. Auto news calendar active.")


async def _daily_summary_loop():
    """Send daily P&L summary at 5 PM NY every trading day"""
    while True:
        now = datetime.now(NY_TZ)
        # 5:00 PM NY, Mon-Fri only
        if now.weekday() < 5 and now.hour == 17 and now.minute < 5:
            wc = sum(b.wins + b.losses for b in bots.values())
            wins = sum(b.wins for b in bots.values())
            losses = sum(b.losses for b in bots.values())
            await telegram.send_daily_summary(
                equity=risk.account_equity,
                pnl=risk.daily_pnl,
                pnl_pct=risk.daily_pnl_pct,
                wins=wins,
                losses=losses,
                open_pos=portfolio.open_count,
            )
            await asyncio.sleep(300)  # avoid duplicate sends
        await asyncio.sleep(60)

# ══════════════════════════════════════════════════════════════════
# WEBSOCKET
# ══════════════════════════════════════════════════════════════════

@app.websocket("/ws")
async def websocket_feed(ws: WebSocket):
    await ws.accept()
    ws_clients.append(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        if ws in ws_clients:
            ws_clients.remove(ws)

async def broadcast(event: str, payload: Dict[str, Any]):
    msg = json.dumps({"event": event, "data": payload,
                      "ts": datetime.utcnow().isoformat()})
    dead = []
    for ws in ws_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in ws_clients:
            ws_clients.remove(ws)

# ══════════════════════════════════════════════════════════════════
# CORE WEBHOOK
# ══════════════════════════════════════════════════════════════════

@app.post("/webhook/signal")
async def receive_signal(signal: InboundSignal, bt: BackgroundTasks):
    portfolio.pending_signals.append(signal)
    if len(portfolio.pending_signals) > 50:
        portfolio.pending_signals.pop(0)

    decision = engine.approve(signal, portfolio, risk, bots)

    log_entry = {
        "signal_id": decision.signal_id, "bot_id": signal.bot_id,
        "symbol": signal.symbol, "side": signal.side.value,
        "grade": signal.setup_grade.value, "score": signal.score,
        "approved": decision.approved, "reason": decision.reason.value,
        "risk_pct": decision.adjusted_risk_pct, "size": decision.adjusted_size,
        "notes": decision.notes, "timestamp": decision.timestamp.isoformat(),
    }
    decision_log.insert(0, log_entry)
    if len(decision_log) > 500:
        decision_log.pop()

    bt.add_task(broadcast, "signal_decision", log_entry)

    # Telegram notification for approved trades and important blocks
    if decision.approved:
        position = OpenPosition(
            signal_id=decision.signal_id, bot_id=signal.bot_id,
            symbol=signal.symbol, side=signal.side,
            entry_price=signal.entry_price, stop_loss=signal.stop_loss,
            take_profit=signal.take_profit, size=decision.adjusted_size or 0.01,
            risk_pct=decision.adjusted_risk_pct or 0.5,
            setup_grade=signal.setup_grade, session=signal.session,
            current_price=signal.entry_price,
        )
        portfolio.positions[decision.signal_id] = position
        risk.open_positions = portfolio.open_count
        risk.open_risk_pct  = portfolio.total_open_risk_pct
        bt.add_task(broadcast, "position_opened", position.model_dump(mode="json"))
        # Only send Telegram for A+ trades (avoid spam)
        if signal.setup_grade.value == "A+":
            bt.add_task(telegram.send_trade, signal.symbol, signal.side.value,
                        signal.setup_grade.value, signal.score, True,
                        "", decision.adjusted_risk_pct or 0)
    elif decision.reason in (DecisionReason.DAILY_LOSS_HIT, DecisionReason.BOT_IN_DRAWDOWN):
        bt.add_task(telegram.send_emergency, decision.reason.value,
                    risk.account_equity, risk.daily_pnl_pct)

    return {"approved": decision.approved, "reason": decision.reason.value,
            "signal_id": decision.signal_id}

# ══════════════════════════════════════════════════════════════════
# CALENDAR ENDPOINTS
# ══════════════════════════════════════════════════════════════════

@app.get("/calendar/this-week", summary="Upcoming high-impact events this week")
async def get_calendar_week():
    if calendar.needs_refresh():
        await calendar.refresh()
    events = calendar.get_this_week()
    return {
        "events": [
            {
                "name":          e.name,
                "event_type":    e.event_type,
                "currency":      e.currency,
                "impact":        e.impact,
                "time_ny":       e.scheduled_dt.strftime("%Y-%m-%d %I:%M %p"),
                "blackout_start":e.blackout_start.strftime("%I:%M %p"),
                "blackout_end":  e.blackout_end.strftime("%I:%M %p"),
                "minutes_until": round(e.minutes_until, 0),
                "is_active":     e.is_active,
                "source":        e.source,
            }
            for e in events
        ],
        "count":        len(events),
        "last_refresh": calendar._last_refresh.isoformat() if calendar._last_refresh else None,
    }

@app.get("/calendar/active", summary="Currently active news blackouts")
async def get_active_events():
    active = calendar.get_active()
    return {
        "news_lock_active": risk.news_lock_active,
        "news_lock_reason": risk.news_lock_reason,
        "news_lock_until":  risk.news_lock_until.isoformat() if risk.news_lock_until else None,
        "active_events": [
            {"name": e.name, "type": e.event_type, "ends": e.blackout_end.strftime("%I:%M %p")}
            for e in active
        ],
    }

@app.post("/calendar/refresh", summary="Force refresh economic calendar")
async def force_refresh_calendar():
    events = await calendar.refresh()
    return {"refreshed": True, "events_loaded": len(events)}

# ══════════════════════════════════════════════════════════════════
# NEWS LOCK MANUAL OVERRIDE (still available if needed)
# ══════════════════════════════════════════════════════════════════

class NewsLockRequest(BaseModel):
    reason: str
    minutes: int = 90

@app.post("/news/lock", summary="Manual news lock override")
async def manual_news_lock(req: NewsLockRequest, bt: BackgroundTasks):
    risk.news_lock_active = True
    risk.news_lock_reason = f"MANUAL: {req.reason}"
    risk.news_lock_until  = datetime.utcnow() + timedelta(minutes=req.minutes)
    bt.add_task(broadcast, "news_lock", {"reason": req.reason, "minutes": req.minutes})
    await telegram.send(f"🔴 <b>MANUAL NEWS LOCK</b>\n{req.reason}\nDuration: {req.minutes} min")
    return {"locked": True, "until": risk.news_lock_until.isoformat()}

@app.post("/news/unlock", summary="Manual news unlock override")
async def manual_news_unlock(bt: BackgroundTasks):
    risk.news_lock_active = False
    risk.news_lock_reason = None
    risk.news_lock_until  = None
    bt.add_task(broadcast, "news_unlock", {})
    await telegram.send("🟢 <b>MANUAL NEWS UNLOCK</b>\nBots accepting signals again.")
    return {"locked": False}

# ══════════════════════════════════════════════════════════════════
# EXISTING ENDPOINTS (retained from v1)
# ══════════════════════════════════════════════════════════════════

class ClosePositionRequest(BaseModel):
    signal_id: str; close_price: float; pnl: float; reason: str = "TP hit"

@app.post("/position/close")
async def close_position(req: ClosePositionRequest, bt: BackgroundTasks):
    pos = portfolio.positions.pop(req.signal_id, None)
    if pos is None:
        raise HTTPException(404, f"Position {req.signal_id} not found")
    won = req.pnl > 0
    ranking_engine.record_trade_result(pos.bot_id, won, req.pnl, bots)
    ranking_engine.rank_and_update(bots)
    risk.daily_pnl += req.pnl
    risk.open_positions = portfolio.open_count
    risk.open_risk_pct  = portfolio.total_open_risk_pct
    if risk.daily_start_equity > 0:
        risk.daily_pnl_pct = (risk.daily_pnl / risk.daily_start_equity) * 100
    risk.account_equity += req.pnl
    bt.add_task(broadcast, "position_closed",
                {"signal_id": req.signal_id, "pnl": req.pnl, "won": won})
    return {"closed": True, "pnl": req.pnl}

@app.post("/emergency/flatten")
async def emergency_flatten(bt: BackgroundTasks):
    count = len(portfolio.positions)
    portfolio.positions.clear()
    risk.open_positions = 0; risk.open_risk_pct = 0.0
    bt.add_task(broadcast, "emergency_flatten", {"positions_closed": count})
    await telegram.send_emergency("MANUAL FLATTEN", risk.account_equity, risk.daily_pnl_pct)
    return {"flattened": count}

class ModeRequest(BaseModel):
    mode: AccountMode

@app.post("/risk/mode")
async def set_mode(req: ModeRequest, bt: BackgroundTasks):
    risk.account_mode = req.mode
    bt.add_task(broadcast, "mode_changed", {"mode": req.mode.value})
    await telegram.send(f"⚙️ Account mode changed to <b>{req.mode.value.upper()}</b>")
    return {"mode": req.mode.value}

@app.post("/risk/reset-daily")
async def reset_daily():
    risk.daily_start_equity = risk.account_equity
    risk.daily_pnl = 0.0; risk.daily_pnl_pct = 0.0
    return {"reset": True}

class BotControlRequest(BaseModel):
    bot_id: str; reason: str = ""

@app.post("/bot/pause")
async def pause_bot(req: BotControlRequest, bt: BackgroundTasks):
    s = bots.setdefault(req.bot_id, BotStats(bot_id=req.bot_id, symbol="", strategy=""))
    s.paused = True; s.pause_reason = req.reason
    bt.add_task(broadcast, "bot_paused", {"bot_id": req.bot_id})
    return {"paused": True}

@app.post("/bot/resume")
async def resume_bot(req: BotControlRequest, bt: BackgroundTasks):
    if s := bots.get(req.bot_id):
        s.paused = False; s.pause_reason = None
    bt.add_task(broadcast, "bot_resumed", {"bot_id": req.bot_id})
    return {"paused": False}

@app.get("/state")
def get_state():
    return {
        "risk": risk.model_dump(),
        "portfolio": {
            "open_count": portfolio.open_count,
            "total_open_risk_pct": portfolio.total_open_risk_pct,
            "positions": [p.model_dump(mode="json") for p in portfolio.positions.values()],
        },
        "bots": {bid: s.model_dump() for bid, s in bots.items()},
        "decision_log": decision_log[:50],
    }

@app.get("/signals/log")
def get_signal_log(limit: int = 100):
    return decision_log[:limit]

@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "9.2.0",
        "equity": risk.account_equity,
        "daily_pnl_pct": round(risk.daily_pnl_pct, 3),
        "open_positions": portfolio.open_count,
        "news_lock": risk.news_lock_active,
        "news_reason": risk.news_lock_reason,
        "mode": risk.account_mode.value,
        "auto_calendar": "active",
        "telegram": telegram.enabled,
        "environment": ENVIRONMENT,
    }

# ── Dashboard ─────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    try:
        with open("dashboard/index.html") as f:
            return f.read()
    except FileNotFoundError:
        return "<h2>ICT v9 Manager Bot v2 running.</h2><p><a href='/health'>/health</a> | <a href='/calendar/this-week'>/calendar/this-week</a></p>"
