"""
PC Parts Crawler — Main entry point.
Chạy crawler thu thập sản phẩm và giá từ các shop.

Usage:
    python main.py              # Chạy crawl một lần
    python main.py --schedule   # Chạy theo lịch (giá mỗi 6h, sản phẩm mỗi 24h)
"""

import sys
import asyncio
import logging
from datetime import datetime

from playwright.async_api import async_playwright

from db import get_connection, save_products_batch, save_price, BATCH_SIZE
# from spiders.phongvu import PhongVuSpider
# from spiders.gearvn import GearVNSpider
from spiders.pcpartpicker import PCPartPickerSpider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("crawler")


def _save_in_batches(conn, spider_name: str, shop_name: str, products: list[dict]) -> int:
    """Lưu danh sách sản phẩm theo batch, trả về số lượng đã lưu thành công."""
    total = len(products)
    total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
    saved_count = 0

    for batch_idx in range(total_batches):
        batch = products[batch_idx * BATCH_SIZE : (batch_idx + 1) * BATCH_SIZE]

        # Tách shop_url ra trước khi lưu (không phải cột trong DB)
        shop_urls = [p.pop("shop_url", "") for p in batch]

        try:
            ids = save_products_batch(conn, batch)

            # Lưu giá (chỉ khi base_price > 0)
            for product_id, product_data, shop_url in zip(ids, batch, shop_urls):
                if product_id and product_data.get("base_price", 0) > 0:
                    try:
                        save_price(conn, {
                            "product_id": product_id,
                            "shop_name": shop_name,
                            "price": product_data["base_price"],
                            "url": shop_url,
                            "in_stock": True,
                        })
                    except Exception as e:
                        logger.error(f"[{spider_name}] Lỗi lưu giá product_id={product_id}: {e}")
                        conn.rollback()

            batch_saved = sum(1 for i in ids if i is not None)
            saved_count += batch_saved
            logger.info(
                f"[{spider_name}] Batch {batch_idx + 1}/{total_batches}: "
                f"lưu {batch_saved}/{len(batch)} sản phẩm"
            )

        except Exception as e:
            logger.error(f"[{spider_name}] Batch {batch_idx + 1} thất bại: {e}")
            conn.rollback()

    return saved_count


async def run_crawl():
    """Chạy tất cả spiders, lưu kết quả vào DB theo batch."""
    spiders = [PCPartPickerSpider()]
    conn = get_connection()

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ]
            )
            context = await browser.new_context(
                user_agent=spiders[0].get_random_user_agent(),
                viewport={"width": 1920, "height": 1080},
                java_script_enabled=True,
                locale="en-US",
            )
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
            """)
            page = await context.new_page()

            for spider in spiders:
                logger.info(f"{'='*50}")
                logger.info(f"Starting spider: {spider.shop_name}")
                logger.info(f"{'='*50}")

                try:
                    products: list[dict] = await spider.crawl_products(page)
                    logger.info(f"[{spider.name}] Crawled {len(products)} sản phẩm, bắt đầu lưu DB...")

                    saved = _save_in_batches(conn, spider.name, spider.shop_name, products)
                    logger.info(f"[{spider.name}] Hoàn tất: {saved}/{len(products)} sản phẩm đã lưu")

                except Exception as e:
                    logger.error(f"[{spider.name}] Spider thất bại: {e}")

            await browser.close()

    finally:
        conn.close()


def run_with_schedule():
    """Chạy crawler theo lịch với APScheduler."""
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler()
    scheduler.add_job(
        lambda: asyncio.run(run_crawl()),
        "interval",
        hours=6,
        id="crawl_prices",
        name="Crawl prices from all shops",
    )

    logger.info("🕐 Scheduler started. Crawling every 6 hours.")
    logger.info("   Press Ctrl+C to stop.")

    asyncio.run(run_crawl())

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")


if __name__ == "__main__":
    if "--schedule" in sys.argv:
        run_with_schedule()
    else:
        logger.info(f"🚀 Starting one-time crawl at {datetime.now()}")
        asyncio.run(run_crawl())
        logger.info("✅ Crawl completed!")
