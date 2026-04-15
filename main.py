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

from db import get_connection, save_product, save_price
# from spiders.phongvu import PhongVuSpider
# from spiders.gearvn import GearVNSpider
from spiders.pcpartpicker import PCPartPickerSpider

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("crawler")


async def run_crawl():
    """Chạy tất cả spiders, lưu kết quả vào DB."""
    spiders = [PCPartPickerSpider()]
    conn = get_connection()

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                channel="msedge",
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ]
            )
            context = await browser.new_context(
                user_agent=spiders[0].get_random_user_agent(),
                viewport={"width": 1920, "height": 1080},
                # Ẩn dấu hiệu automation để qua Cloudflare
                java_script_enabled=True,
                locale="en-US",
            )
            # Ẩn webdriver flag
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
                    logger.info(f"[{spider.name}] Total products found: {len(products)}")

                    saved_count: int = 0
                    for product_data in products:
                        shop_url = product_data.pop("shop_url", "")
                        try:
                            product_id = save_product(conn, product_data)

                            # Lưu giá hiện tại
                            save_price(conn, {
                                "product_id": product_id,
                                "shop_name": spider.shop_name,
                                "price": product_data["base_price"],
                                "url": shop_url,
                                "in_stock": True,
                            })
                            saved_count += 1
                        except Exception as e:
                            logger.error(f"[{spider.name}] Error saving product: {e}")
                            conn.rollback()

                    logger.info(f"[{spider.name}] Saved {saved_count}/{len(products)} products")

                except Exception as e:
                    logger.error(f"[{spider.name}] Spider failed: {e}")

            await browser.close()

    finally:
        conn.close()


def run_with_schedule():
    """Chạy crawler theo lịch với APScheduler."""
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler()

    # Crawl giá mỗi 6 tiếng
    scheduler.add_job(
        lambda: asyncio.run(run_crawl()),
        "interval",
        hours=6,
        id="crawl_prices",
        name="Crawl prices from all shops",
    )

    logger.info("🕐 Scheduler started. Crawling every 6 hours.")
    logger.info("   Press Ctrl+C to stop.")

    # Run immediately first time
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
