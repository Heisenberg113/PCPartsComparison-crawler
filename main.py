"""
PC Parts Crawler — Main entry point.

Usage:
    python main.py              # Chạy crawl một lần
    python main.py --schedule   # Chạy theo lịch mỗi 6h
"""

import sys
import asyncio
import logging
from datetime import datetime

from db import get_connection
from spiders.pcpartpicker import PCPartPickerSpider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("crawler")


async def run_crawl():
    conn = get_connection()
    try:
        for spider in [PCPartPickerSpider()]:
            logger.info("=" * 50)
            logger.info(f"Starting spider: {spider.shop_name}")
            logger.info("=" * 50)
            try:
                await spider.crawl_products(conn=conn)
                logger.info(f"[{spider.name}] Hoàn tất crawl và lưu DB.")
            except Exception as e:
                logger.error(f"[{spider.name}] Spider thất bại: {e}")
    finally:
        conn.close()


def run_with_schedule():
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler()
    scheduler.add_job(
        lambda: asyncio.run(run_crawl()),
        "interval",
        hours=6,
        id="crawl_products",
        name="Crawl products from all shops",
    )

    logger.info("Scheduler started. Crawling every 6 hours. Press Ctrl+C to stop.")
    asyncio.run(run_crawl())

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


if __name__ == "__main__":
    if "--schedule" in sys.argv:
        run_with_schedule()
    else:
        logger.info(f"Starting one-time crawl at {datetime.now()}")
        asyncio.run(run_crawl())
        logger.info("Crawl completed!")
