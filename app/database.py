import asyncpg
import os
import logging

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://user:pass@db:5432/portfolio")
TABLE_NAME   = os.environ.get("TABLE_NAME", "transactions")   # your actual table name


class Database:
    pool: asyncpg.Pool | None = None

    async def connect(self):
        logger.info("Connecting to Postgres…")
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=5)
        logger.info("Connected")

    async def close(self):
        if self.pool:
            await self.pool.close()
