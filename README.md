# ICT v9 Manager Bot v2

TradingView signal manager with auto economic calendar, Telegram alerts, and 11-layer approval engine.

## Deploy to Railway

1. Connect this repo in Railway dashboard
2. Set environment variables:
   - `STARTING_EQUITY` = your account size (e.g. 35000)
   - `TELEGRAM_BOT_TOKEN` = from @BotFather on Telegram
   - `TELEGRAM_CHAT_ID` = your Telegram chat ID
3. Deploy — live in ~2 minutes

## Endpoints

| Method | URL | Description |
|--------|-----|-------------|
| GET | `/` | Dashboard UI |
| GET | `/health` | Health check |
| POST | `/webhook/signal` | TradingView sends signals here |
| GET | `/calendar/this-week` | Upcoming economic events |
| POST | `/news/lock` | Manual news lock |
| POST | `/news/unlock` | Manual unlock |
| POST | `/risk/mode` | Switch Conservative/Standard/Aggressive |
| POST | `/emergency/flatten` | Close all positions |

## TradingView Webhook Message Format

```json
{
  "bot_id": "gbpusd_5m",
  "strategy": "ict_v9",
  "symbol": "GBPUSD",
  "side": "{{strategy.order.action}}",
  "entry_price": {{strategy.order.price}},
  "setup_grade": "A",
  "score": 6,
  "session": "NY",
  "confidence": 80
}
```

## File Structure

```
manager_v2/
├── api/app.py              FastAPI server + all endpoints
├── core/engine.py          11-layer signal approval engine
├── core/models.py          Pydantic models + risk state
├── news/calendar.py        Auto economic calendar (Forex Factory RSS)
├── news/scheduler.py       Auto lock/unlock scheduler
├── notifications/telegram.py  Telegram phone alerts
├── dashboard/index.html    Web control panel UI
├── Procfile                Railway start command
├── requirements.txt        Python dependencies
└── railway.json            Railway deploy config
```
