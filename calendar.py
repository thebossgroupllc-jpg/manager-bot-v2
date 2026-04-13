"""
ICT v9 Manager Bot — Telegram Notifier
Sends real-time alerts to your phone for every important event.

SETUP (2 minutes):
  1. Open Telegram → search @BotFather → send /newbot
  2. Follow prompts → copy your BOT_TOKEN
  3. Message your new bot once, then open:
     https://api.telegram.org/bot<TOKEN>/getUpdates
  4. Find "chat":{"id": 123456789} — that's your CHAT_ID
  5. Add to Railway env vars:
       TELEGRAM_BOT_TOKEN=1234567890:ABCdef...
       TELEGRAM_CHAT_ID=123456789
"""

from __future__ import annotations
import logging
import os
from typing import Optional
import httpx

logger = logging.getLogger("telegram")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_BASE      = "https://api.telegram.org/bot"


class TelegramNotifier:
    def __init__(self):
        self.token   = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self.enabled = bool(self.token and self.chat_id)
        if not self.enabled:
            logger.info("Telegram not configured — set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID")

    async def send(self, message: str, parse_mode: str = "HTML") -> bool:
        if not self.enabled:
            logger.debug("Telegram skip: %s", message[:60])
            return False
        try:
            url = f"{TELEGRAM_BASE}{self.token}/sendMessage"
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(url, json={"chat_id": self.chat_id, "text": message, "parse_mode": parse_mode})
                return r.status_code == 200
        except Exception as e:
            logger.warning("Telegram failed: %s", e)
            return False

    async def send_news_lock(self, event_name: str, event_time: str, unlock_time: str):
        await self.send(
            f"🔴 <b>NEWS LOCK — AUTO ACTIVATED</b>\n\n"
            f"📌 <b>{event_name}</b>\n"
            f"⏰ Event: <b>{event_time} NY</b>\n"
            f"🔓 Auto-unlock: <b>{unlock_time} NY</b>\n\n"
            f"🚫 All bot entries blocked automatically"
        )

    async def send_news_unlock(self, event_name: str):
        await self.send(
            f"🟢 <b>NEWS LOCK CLEARED — AUTO</b>\n\n"
            f"✅ {event_name} window passed\n"
            f"▶️ All bots accepting signals again"
        )

    async def send_pre_alert(self, event_name: str, mins_until: int, event_time: str):
        await self.send(
            f"⚠️ <b>UPCOMING NEWS — {mins_until} MIN WARNING</b>\n\n"
            f"📌 <b>{event_name}</b>\n"
            f"⏰ <b>{event_time} NY</b>\n\n"
            f"Lock will auto-activate. No action needed."
        )

    async def send_weekly_briefing(self, events_text: str):
        await self.send(f"📅 <b>WEEKLY CALENDAR — AUTO NEWS SCHEDULE</b>\n\n{events_text}")

    async def send_trade(self, symbol: str, side: str, grade: str, score: int,
                          approved: bool, reason: str, risk_pct: float = 0):
        icon = "✅" if approved else "❌"
        d_icon = "📈" if side == "buy" else "📉"
        msg = (f"{icon} <b>{'APPROVED' if approved else 'BLOCKED'}</b> — "
               f"{d_icon} <b>{symbol} {side.upper()}</b>\n"
               f"Grade: <b>{grade}</b> Score: <b>{score}</b>\n")
        if approved:
            msg += f"Risk: <b>{risk_pct:.2f}%</b>"
        else:
            msg += f"Reason: <b>{reason}</b>"
        await self.send(msg)

    async def send_daily_summary(self, equity: float, pnl: float, pnl_pct: float,
                                  wins: int, losses: int, open_pos: int):
        icon = "🟢" if pnl >= 0 else "🔴"
        await self.send(
            f"{icon} <b>DAILY SUMMARY</b>\n\n"
            f"P&L: <b>{'+'if pnl>=0 else ''}${pnl:,.0f} ({pnl_pct:+.2f}%)</b>\n"
            f"Account: <b>${equity:,.0f}</b>\n"
            f"✅ {wins}W  ❌ {losses}L  📊 {open_pos} open"
        )

    async def send_emergency(self, reason: str, equity: float, dd_pct: float):
        await self.send(
            f"🚨 <b>EMERGENCY — {reason}</b>\n\n"
            f"Account: <b>${equity:,.0f}</b>\n"
            f"Drawdown: <b>{dd_pct:.2f}%</b>\n\n"
            f"All positions closed. Entries halted."
        )

    async def send_startup(self, equity: float, mode: str):
        await self.send(
            f"🚀 <b>ICT v9 MANAGER BOT ONLINE</b>\n\n"
            f"Account: <b>${equity:,.0f}</b>\n"
            f"Mode: <b>{mode.upper()}</b>\n"
            f"News: <b>AUTO CALENDAR ✓</b>\n"
            f"Telegram: <b>CONNECTED ✓</b>\n\n"
            f"Ready for signals."
        )
