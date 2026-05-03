# Kindle Portfolio Backend

Read-only FastAPI service that derives current holdings and P&L from your
Trading 212 transaction log stored in Postgres.  No writes, no external APIs.

## Setup

1. Edit `docker-compose.yml` — set `DATABASE_URL` and `TABLE_NAME`
2. `docker compose up -d --build`
3. `curl http://localhost:8000/portfolio | python3 -m json.tool`

## Kindle setup (the whole client side)

```sh
# /etc/cron.d/portfolio  — runs every 15 minutes on the Kindle
*/15 * * * * root curl -s http://YOUR_SERVER_IP:8000/display/image.png \
  -o /tmp/portfolio.png && eips -g /tmp/portfolio.png
```

That's it. The Kindle fetches a ready-to-display 800×600 greyscale PNG and
pushes it to the e-ink screen with `eips`. No Python, no rendering, nothing
else needed on the device.

If you're using KOReader's screensaver instead of `eips`:
```sh
curl -s http://YOUR_SERVER_IP:8000/display/image.png \
  -o /mnt/us/koreader/screensaver/portfolio.png
```

## API endpoints

```
GET /display/image.png   ← Kindle polls this — returns 800×600 PNG
GET /portfolio           ← Raw JSON (for debugging / future use)
GET /health              ← Liveness check
```

## JSON portfolio endpoint

```
GET /portfolio
```

Returns:

```json
{
  "total_cost_basis": 8420.50,
  "total_current_value": 10340.80,
  "total_gain_loss": 1920.30,
  "total_gain_loss_pct": 22.80,
  "total_dividends": 143.20,
  "total_interest": 12.40,
  "currency": "GBP",
  "generated_at": "2026-04-23T14:30:00Z",
  "as_of": "2026-04-22T16:00:00Z",
  "holdings": [
    {
      "ticker": "VWRL",
      "name": "Vanguard FTSE All-World",
      "isin": "IE00B3RBWM25",
      "shares": 25.0,
      "avg_cost": 105.50,
      "latest_price": 118.20,
      "currency": "USD",
      "cost_basis": 2637.50,
      "current_value": 2955.00,
      "gain_loss": 317.50,
      "gain_loss_pct": 12.04,
      "last_transaction": "2026-03-10T09:15:00Z"
    }
  ]
}
```

Holdings are sorted by current value, largest first.

## How positions are calculated

| Action | Effect |
|---|---|
| Market buy / Limit buy / Stock split open | Add shares, add to cost basis |
| Market sell / Stock split close | Subtract shares, reduce cost basis proportionally |
| Dividend (all variants) | Accumulated in `total_dividends` |
| Interest on cash | Accumulated in `total_interest` |
| Deposit / Withdrawal / Result adjustment | Ignored |

`latest_price` = `price_per_share` from the most recent buy or sell transaction
for that ticker.  This means values are **as-of your last trade**, not live.
If you want live prices later, that's a one-file change in `portfolio.py`.

## Kindle polling (example cron)

```sh
*/15 * * * * curl -s http://192.168.1.x:8000/portfolio > /tmp/portfolio.json
```
