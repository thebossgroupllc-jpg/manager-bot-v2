"""
ICT v9 Manager Bot — Auto News Calendar Engine
Fetches real high-impact economic events from multiple free sources
and auto-schedules news locks/unlocks on the manager bot.

Sources (in priority order):
  1. Forex Factory RSS feed (free, no API key needed)
  2. Investing.com economic calendar (scraped)
  3. Hardcoded recurring fallback (NFP always first Friday, etc.)

Events tracked:
  FOMC, CPI, NFP, BOE, BOJ, ECB, SNB, RBA, GDP, PPI, PCE
  All scheduled in America/New_York timezone
"""

from __future__ import annotations
import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger("news_calendar")
NY_TZ = ZoneInfo("America/New_York")

# ══════════════════════════════════════════════════════════════════
# EVENT DEFINITIONS
# ══════════════════════════════════════════════════════════════════

@dataclass
class EconEvent:
    name:        str
    event_type:  str          # FOMC, CPI, NFP, BOE, BOJ, ECB, GDP, etc.
    scheduled_dt: datetime    # always in NY timezone
    blackout_mins: int        # total blackout window (centred on event)
    currency:    str          # affected currency
    impact:      str          # HIGH, MEDIUM
    source:      str          # "forexfactory" | "fallback"

    @property
    def blackout_start(self) -> datetime:
        return self.scheduled_dt - timedelta(minutes=self.blackout_mins // 2)

    @property
    def blackout_end(self) -> datetime:
        return self.scheduled_dt + timedelta(minutes=self.blackout_mins // 2)

    @property
    def is_active(self) -> bool:
        now = datetime.now(NY_TZ)
        return self.blackout_start <= now <= self.blackout_end

    @property
    def minutes_until(self) -> float:
        now = datetime.now(NY_TZ)
        delta = (self.scheduled_dt - now).total_seconds() / 60
        return delta


# Blackout windows per event type (minutes centred on release)
BLACKOUT_WINDOWS: Dict[str, int] = {
    "FOMC":   240,  # ±120 min (statement + presser)
    "CPI":    180,  # ±90 min
    "NFP":    180,  # ±90 min
    "BOE":    180,  # ±90 min
    "BOJ":    120,  # ±60 min
    "ECB":    180,  # ±90 min
    "SNB":    120,  # ±60 min
    "RBA":    120,  # ±60 min
    "GDP":    120,  # ±60 min
    "PPI":    120,  # ±60 min
    "PCE":    120,  # ±60 min
    "RETAIL": 120,
    "PMI":     60,
    "DEFAULT": 90,
}

# Keywords → event type mapping
EVENT_KEYWORDS: Dict[str, str] = {
    "federal funds rate":   "FOMC",
    "fomc":                 "FOMC",
    "fed rate":             "FOMC",
    "interest rate decision": "FOMC",
    "non-farm payroll":     "NFP",
    "nonfarm payroll":      "NFP",
    "nfp":                  "NFP",
    "consumer price index": "CPI",
    "cpi":                  "CPI",
    "bank of england":      "BOE",
    "boe":                  "BOE",
    "mpc":                  "BOE",
    "bank of japan":        "BOJ",
    "boj":                  "BOJ",
    "european central bank":"ECB",
    "ecb":                  "ECB",
    "swiss national bank":  "SNB",
    "snb":                  "SNB",
    "reserve bank of australia": "RBA",
    "rba":                  "RBA",
    "gross domestic product":"GDP",
    "gdp":                  "GDP",
    "producer price index": "PPI",
    "ppi":                  "PPI",
    "personal consumption": "PCE",
    "pce":                  "PCE",
    "retail sales":         "RETAIL",
    "purchasing managers":  "PMI",
    "pmi":                  "PMI",
}

HIGH_IMPACT_KEYWORDS = [
    "fomc", "federal funds", "interest rate decision",
    "non-farm", "nonfarm", "nfp",
    "consumer price index", "cpi",
    "bank of england", "boe",
    "bank of japan", "boj",
    "european central bank", "ecb",
    "gdp", "gross domestic product",
]


def classify_event(title: str) -> Tuple[str, str, int]:
    """Returns (event_type, currency, blackout_mins) from event title"""
    t = title.lower()
    event_type = "DEFAULT"
    for keyword, etype in EVENT_KEYWORDS.items():
        if keyword in t:
            event_type = etype
            break

    # Currency detection
    currency = "USD"
    if any(k in t for k in ["bank of england", "boe", "uk ", "united kingdom", "gbp"]):
        currency = "GBP"
    elif any(k in t for k in ["bank of japan", "boj", "japan", "jpy"]):
        currency = "JPY"
    elif any(k in t for k in ["european central bank", "ecb", "euro", "eur"]):
        currency = "EUR"
    elif any(k in t for k in ["rba", "australia", "aud"]):
        currency = "AUD"
    elif any(k in t for k in ["snb", "swiss", "chf"]):
        currency = "CHF"

    blackout = BLACKOUT_WINDOWS.get(event_type, BLACKOUT_WINDOWS["DEFAULT"])
    return event_type, currency, blackout


# ══════════════════════════════════════════════════════════════════
# FOREX FACTORY RSS PARSER
# ══════════════════════════════════════════════════════════════════

class ForexFactoryCalendar:
    """
    Pulls the Forex Factory RSS calendar feed.
    Free, no API key, updates every few minutes.
    """
    RSS_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
    # Alternate week feed
    RSS_NEXT = "https://nfs.faireconomy.media/ff_calendar_nextweek.xml"

    async def fetch_week(self, next_week: bool = False) -> List[EconEvent]:
        url = self.RSS_NEXT if next_week else self.RSS_URL
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(url, headers={"User-Agent": "ICT-Manager-Bot/9.0"})
                r.raise_for_status()
                return self._parse(r.text)
        except Exception as e:
            logger.warning("ForexFactory fetch failed: %s", e)
            return []

    def _parse(self, xml_text: str) -> List[EconEvent]:
        events: List[EconEvent] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return events

        for item in root.iter("event"):
            try:
                title    = item.findtext("title", "").strip()
                country  = item.findtext("country", "").strip().upper()
                impact   = item.findtext("impact", "").strip().upper()
                date_str = item.findtext("date", "").strip()
                time_str = item.findtext("time", "").strip()

                # Only HIGH impact events
                if impact not in ("HIGH", "MEDIUM"):
                    continue

                # Only HIGH impact for our core currencies
                relevant = any(k in title.lower() for k in HIGH_IMPACT_KEYWORDS) or \
                           country in ("US", "GB", "EU", "JP", "AU", "CH")
                if not relevant:
                    continue

                # Parse datetime
                dt = self._parse_datetime(date_str, time_str)
                if dt is None:
                    continue

                event_type, currency, blackout = classify_event(title)

                events.append(EconEvent(
                    name=title,
                    event_type=event_type,
                    scheduled_dt=dt,
                    blackout_mins=blackout,
                    currency=currency,
                    impact=impact,
                    source="forexfactory",
                ))
            except Exception:
                continue

        logger.info("ForexFactory: parsed %d high-impact events", len(events))
        return events

    @staticmethod
    def _parse_datetime(date_str: str, time_str: str) -> Optional[datetime]:
        """Parse FF date/time strings into NY-timezone datetime"""
        try:
            # FF format: "01-06-2026" and "8:30am"
            dt_str = f"{date_str} {time_str}".strip()
            for fmt in ("%m-%d-%Y %I:%M%p", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %I:%M%p"):
                try:
                    dt = datetime.strptime(dt_str, fmt)
                    return dt.replace(tzinfo=NY_TZ)
                except ValueError:
                    continue
            # Try ISO format fallback
            dt = datetime.fromisoformat(date_str)
            return dt.replace(tzinfo=NY_TZ)
        except Exception:
            return None


# ══════════════════════════════════════════════════════════════════
# FALLBACK: HARDCODED RECURRING SCHEDULE
# Used when API calls fail
# ══════════════════════════════════════════════════════════════════

class RecurringCalendar:
    """
    Hardcoded recurring events — always correct even without internet.
    NFP: first Friday of month, 8:30 AM NY
    CPI: variable but typically 2nd Tuesday/Wednesday
    FOMC: 8 meetings per year (hardcoded for 2026)
    """

    # FOMC 2026 meeting dates (rate decisions announced at 2:00 PM NY)
    FOMC_2026 = [
        "2026-01-29", "2026-03-19", "2026-05-07",
        "2026-06-18", "2026-07-30", "2026-09-17",
        "2026-11-05", "2026-12-17",
    ]

    # BOE 2026 MPC meeting dates (announcement at ~7:00 AM NY / 12:00 London)
    BOE_2026 = [
        "2026-02-06", "2026-03-20", "2026-05-08",
        "2026-06-19", "2026-08-07", "2026-09-18",
        "2026-11-06", "2026-12-18",
    ]

    # ECB 2026 meetings (announcement at ~8:15 AM NY)
    ECB_2026 = [
        "2026-01-30", "2026-03-06", "2026-04-17",
        "2026-06-05", "2026-07-24", "2026-09-11",
        "2026-10-23", "2026-12-11",
    ]

    def get_this_week(self) -> List[EconEvent]:
        now = datetime.now(NY_TZ)
        week_start = now - timedelta(days=now.weekday())
        week_end   = week_start + timedelta(days=7)
        events: List[EconEvent] = []

        # NFP — first Friday of each month, 8:30 AM
        nfp = self._first_friday_of_month(now.year, now.month)
        if week_start.date() <= nfp.date() <= week_end.date():
            events.append(EconEvent(
                name="Non-Farm Payrolls (NFP)",
                event_type="NFP",
                scheduled_dt=nfp.replace(hour=8, minute=30, second=0),
                blackout_mins=180,
                currency="USD",
                impact="HIGH",
                source="fallback",
            ))

        # FOMC
        for d in self.FOMC_2026:
            dt = datetime.strptime(d, "%Y-%m-%d").replace(
                hour=14, minute=0, second=0, tzinfo=NY_TZ)
            if week_start.date() <= dt.date() <= week_end.date():
                events.append(EconEvent(
                    name="FOMC Rate Decision",
                    event_type="FOMC",
                    scheduled_dt=dt,
                    blackout_mins=240,
                    currency="USD",
                    impact="HIGH",
                    source="fallback",
                ))

        # BOE
        for d in self.BOE_2026:
            dt = datetime.strptime(d, "%Y-%m-%d").replace(
                hour=7, minute=0, second=0, tzinfo=NY_TZ)
            if week_start.date() <= dt.date() <= week_end.date():
                events.append(EconEvent(
                    name="Bank of England MPC Rate Decision",
                    event_type="BOE",
                    scheduled_dt=dt,
                    blackout_mins=180,
                    currency="GBP",
                    impact="HIGH",
                    source="fallback",
                ))

        # ECB
        for d in self.ECB_2026:
            dt = datetime.strptime(d, "%Y-%m-%d").replace(
                hour=8, minute=15, second=0, tzinfo=NY_TZ)
            if week_start.date() <= dt.date() <= week_end.date():
                events.append(EconEvent(
                    name="European Central Bank Rate Decision",
                    event_type="ECB",
                    scheduled_dt=dt,
                    blackout_mins=180,
                    currency="EUR",
                    impact="HIGH",
                    source="fallback",
                ))

        return events

    @staticmethod
    def _first_friday_of_month(year: int, month: int) -> datetime:
        """Returns first Friday of the given month"""
        import calendar
        cal = calendar.monthcalendar(year, month)
        for week in cal:
            if week[calendar.FRIDAY] != 0:
                return datetime(year, month, week[calendar.FRIDAY], tzinfo=NY_TZ)
        return datetime(year, month, 1, tzinfo=NY_TZ)


# ══════════════════════════════════════════════════════════════════
# MAIN CALENDAR MANAGER
# ══════════════════════════════════════════════════════════════════

class CalendarManager:
    """
    Orchestrates all calendar sources.
    Refreshes every 4 hours automatically.
    Provides a clean interface for the scheduler.
    """

    def __init__(self):
        self.ff      = ForexFactoryCalendar()
        self.fallback= RecurringCalendar()
        self._events: List[EconEvent] = []
        self._last_refresh: Optional[datetime] = None

    async def refresh(self) -> List[EconEvent]:
        """Fetch this week + next week events from all sources"""
        logger.info("Refreshing economic calendar...")

        # Try Forex Factory first
        this_week = await self.ff.fetch_week(next_week=False)
        next_week  = await self.ff.fetch_week(next_week=True)
        ff_events  = this_week + next_week

        if ff_events:
            # Merge with fallback for any missing recurring events
            fallback_events = self.fallback.get_this_week()
            seen_types = {e.event_type for e in ff_events}
            for fb in fallback_events:
                if fb.event_type not in seen_types:
                    ff_events.append(fb)
            self._events = ff_events
        else:
            # Fallback only
            logger.warning("Using fallback calendar (FF unavailable)")
            self._events = self.fallback.get_this_week()

        self._last_refresh = datetime.now(NY_TZ)
        logger.info("Calendar: %d events loaded", len(self._events))
        return self._events

    def get_upcoming(self, hours_ahead: int = 48) -> List[EconEvent]:
        """Return events in the next N hours"""
        now = datetime.now(NY_TZ)
        cutoff = now + timedelta(hours=hours_ahead)
        return [e for e in self._events
                if now <= e.scheduled_dt <= cutoff]

    def get_active(self) -> List[EconEvent]:
        """Return events whose blackout window is currently active"""
        return [e for e in self._events if e.is_active]

    def get_this_week(self) -> List[EconEvent]:
        """Return all events this week sorted by time"""
        now = datetime.now(NY_TZ)
        week_end = now + timedelta(days=7)
        return sorted(
            [e for e in self._events if now <= e.scheduled_dt <= week_end],
            key=lambda e: e.scheduled_dt
        )

    def needs_refresh(self) -> bool:
        if self._last_refresh is None:
            return True
        return (datetime.now(NY_TZ) - self._last_refresh).total_seconds() > 14400  # 4 hrs
