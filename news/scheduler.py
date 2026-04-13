"""
ICT v9 Manager Bot — Auto News Scheduler
Watches the economic calendar and automatically:
  - Locks all bots 30 minutes BEFORE every high-impact event
  - Unlocks all bots 60 minutes AFTER the event
  - Sends Telegram notifications before, during and after
  - Sends Sunday weekly briefing of all upcoming events
"""

from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Set
from zoneinfo import ZoneInfo

from .calendar import CalendarManager, EconEvent

logger = logging.getLogger("news_scheduler")
NY_TZ = ZoneInfo("America/New_York")


class NewsScheduler:
    """
    Runs as a background asyncio task.
    Checks the calendar every 60 seconds.
    Fires PRE-event lock, POST-event unlock, and notifications.
    """

    PRE_LOCK_MINS  = 30   # lock bots this many minutes BEFORE event
    POST_LOCK_MINS = 60   # keep locked this many minutes AFTER event

    def __init__(self, risk_state, notifier, calendar: CalendarManager):
        self.risk       = risk_state
        self.notifier   = notifier
        self.calendar   = calendar
        self._locked_for: Set[str] = set()   # event names currently locked
        self._alerted_for: Set[str] = set()  # events we've sent pre-alerts for
        self._running   = False

    async def start(self):
        """Start the background scheduler loop"""
        self._running = True
        logger.info("News scheduler started")

        # Initial calendar refresh
        await self.calendar.refresh()

        # Schedule Sunday briefing
        asyncio.create_task(self._sunday_briefing_loop())

        # Main loop
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error("Scheduler tick error: %s", e)
            await asyncio.sleep(60)  # check every 60 seconds

    async def stop(self):
        self._running = False

    async def _tick(self):
        now = datetime.now(NY_TZ)

        # Refresh calendar if stale
        if self.calendar.needs_refresh():
            await self.calendar.refresh()

        upcoming = self.calendar.get_upcoming(hours_ahead=4)

        for event in upcoming:
            event_key = f"{event.name}_{event.scheduled_dt.isoformat()}"
            mins_until = event.minutes_until

            # ── PRE-EVENT: send alert 30 minutes before
            if 28 <= mins_until <= 32 and event_key not in self._alerted_for:
                self._alerted_for.add(event_key)
                await self._send_pre_alert(event)

            # ── LOCK: 30 minutes before event
            if mins_until <= self.PRE_LOCK_MINS and event_key not in self._locked_for:
                self._locked_for.add(event_key)
                await self._lock_for_event(event)

            # ── UNLOCK: after blackout window ends
            if event_key in self._locked_for and not event.is_active:
                if mins_until < -self.POST_LOCK_MINS:
                    self._locked_for.discard(event_key)
                    await self._unlock_after_event(event)

        # Auto-unlock if no events are active and we're still locked
        active = self.calendar.get_active()
        if not active and self.risk.news_lock_active:
            all_locked_expired = not any(
                k.split("_")[0] in [e.name for e in upcoming]
                for k in self._locked_for
            )
            if all_locked_expired:
                self._apply_unlock("Auto-cleared: no active events")

    async def _lock_for_event(self, event: EconEvent):
        """Activate news lock for an event"""
        reason = f"{event.event_type} — {event.name}"
        unlock_time = event.blackout_end + timedelta(minutes=self.POST_LOCK_MINS)

        # Apply to risk state
        from datetime import timezone
        self.risk.news_lock_active = True
        self.risk.news_lock_reason = reason
        self.risk.news_lock_until  = unlock_time

        logger.warning("🔴 NEWS LOCK: %s | Unlock: %s",
                       reason, unlock_time.strftime("%H:%M NY"))

        # Notify
        msg = (
            f"🔴 <b>NEWS LOCK ACTIVATED</b>\n\n"
            f"📌 <b>{event.name}</b>\n"
            f"⏰ Event time: <b>{event.scheduled_dt.strftime('%I:%M %p')} NY</b>\n"
            f"⏳ Blackout: <b>±{event.blackout_mins // 2} min</b>\n"
            f"🔓 Auto-unlock: <b>{unlock_time.strftime('%I:%M %p')} NY</b>\n\n"
            f"🚫 All bot entries blocked until unlock"
        )
        await self.notifier.send(msg)

    async def _unlock_after_event(self, event: EconEvent):
        """Deactivate news lock after event passes"""
        self._apply_unlock(f"{event.event_type} cleared")

        msg = (
            f"🟢 <b>NEWS LOCK CLEARED</b>\n\n"
            f"✅ {event.name} window has passed\n"
            f"▶️ Bots are now accepting signals again"
        )
        await self.notifier.send(msg)

    def _apply_unlock(self, reason: str):
        self.risk.news_lock_active = False
        self.risk.news_lock_reason = None
        self.risk.news_lock_until  = None
        logger.info("🟢 NEWS UNLOCK: %s", reason)

    async def _send_pre_alert(self, event: EconEvent):
        """Send 30-minute warning before event"""
        msg = (
            f"⚠️ <b>HIGH-IMPACT EVENT IN 30 MINUTES</b>\n\n"
            f"📌 <b>{event.name}</b>\n"
            f"⏰ <b>{event.scheduled_dt.strftime('%I:%M %p')} NY</b>\n"
            f"💱 Currency: <b>{event.currency}</b>\n"
            f"🔴 Lock will activate in <b>~0 minutes</b>\n\n"
            f"Bot will auto-lock entries. No action needed."
        )
        await self.notifier.send(msg)

    async def _sunday_briefing_loop(self):
        """Send Sunday evening weekly calendar briefing"""
        while self._running:
            now = datetime.now(NY_TZ)
            # Sunday between 4:00 PM and 4:05 PM NY
            if now.weekday() == 6 and 16 <= now.hour < 17:
                await self._send_weekly_briefing()
                await asyncio.sleep(3600)  # sleep 1 hour to avoid duplicate
            await asyncio.sleep(300)  # check every 5 minutes

    async def _send_weekly_briefing(self):
        """Send full week ahead calendar via Telegram"""
        await self.calendar.refresh()
        events = self.calendar.get_this_week()

        if not events:
            await self.notifier.send(
                "📅 <b>WEEKLY CALENDAR</b>\n\nNo high-impact events detected this week.\n"
                "All bots running unrestricted."
            )
            return

        lines = ["📅 <b>WEEKLY HIGH-IMPACT EVENTS</b>\n", "All events auto-locked/unlocked.\n"]

        current_day = None
        for e in events:
            day = e.scheduled_dt.strftime("%A %b %d")
            if day != current_day:
                lines.append(f"\n<b>{day}</b>")
                current_day = day

            time_str    = e.scheduled_dt.strftime("%I:%M %p")
            lock_start  = e.blackout_start.strftime("%I:%M %p")
            lock_end    = e.blackout_end.strftime("%I:%M %p")

            impact_icon = "🔴" if e.impact == "HIGH" else "🟡"
            lines.append(
                f"  {impact_icon} {time_str} — {e.event_type} | {e.name[:40]}\n"
                f"    └ Lock: {lock_start} → {lock_end}"
            )

        lines.append(
            "\n\n✅ <b>All locks are automatic — no manual action needed</b>\n"
            "🤖 Bots will resume trading automatically after each event"
        )

        await self.notifier.send("\n".join(lines))
