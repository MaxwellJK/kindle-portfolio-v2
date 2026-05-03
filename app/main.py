from fastapi import FastAPI
from fastapi.responses import Response
from contextlib import asynccontextmanager
import logging
from datetime import datetime, timezone

from .database import Database
from .portfolio import PortfolioService
from .t212 import T212Client
from .models import PortfolioSummary
from .renderer import render_png

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

db   = Database()
t212 = T212Client()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    yield
    await db.close()


app = FastAPI(
    title="Kindle Portfolio",
    description="Serves portfolio data derived from Trading 212",
    version="2.0.0",
    lifespan=lifespan,
)


@app.get("/portfolio", response_model=PortfolioSummary)
async def get_portfolio():
    """Main JSON endpoint — returns computed positions and P&L."""
    svc = PortfolioService(db, t212)
    return await svc.get_summary()


@app.get("/display/image.png", response_class=Response)
async def get_display_image():
    """Kindle endpoint — returns a ready-to-display 800×600 greyscale PNG."""
    svc     = PortfolioService(db, t212)
    summary = await svc.get_summary()
    monthly = await svc.get_monthly_dividends(months=12)
    png_bytes = render_png(summary, monthly)
    return Response(content=png_bytes, media_type="image/png")


@app.get("/health")
async def health():
    try:
        await db.pool.fetchval("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "status":    "ok" if db_ok else "degraded",
        "db":        "connected" if db_ok else "disconnected",
        "positions": len(t212.all_positions()),
        "cash":      t212.cash,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
