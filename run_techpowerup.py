import sys
import asyncio
import logging
from playwright.async_api import async_playwright

from db import get_connection, save_product
from spiders.techpowerup import TechPowerUpSpider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("techpowerup_runner")

async def run(start_id=1, end_id=10000):
    logger.info(f"🚀 Starting TechPowerUp Crawler from ID {start_id} to {end_id}")
    spider = TechPowerUpSpider()
    conn = get_connection()
    
    try:
        async with async_playwright() as p:
            # Dùng Edge để tránh bị nhận diện là bot tự động chuẩn Chromium
            browser = await p.chromium.launch(
                channel="msedge",
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ]
            )
            context = await browser.new_context(
                user_agent=spider.get_random_user_agent(),
                viewport={"width": 1920, "height": 1080},
                java_script_enabled=True,
                locale="en-US",
            )
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
            """)
            page = await context.new_page()

            # Lặp qua các trang từ start_id đến end_id
            for bios_id in range(start_id, end_id + 1):
                try:
                    product = await spider.get_bios_details(page, bios_id)
                    
                    if product:
                        # product["shop_url"] chưa có column trong DB, lưu vào `prices` sau?
                        # Ta lưu trực tiếp specs vào bảng `products`
                        try:
                            p_id = save_product(conn, product)
                            logger.info(f"✅ Saved GPU BIOS {bios_id}: {product['name']} (ID: {p_id})")
                        except Exception as db_err:
                            logger.error(f"❌ DB Error for BIOS {bios_id} - {product['slug']}: {db_err}")
                            conn.rollback() # Rollback để tránh sụp kết nối cho các record sau
                            
                except Exception as crawl_err:
                    logger.error(f"❌ Crawl Error for BIOS {bios_id}: {crawl_err}")
                
                # Tránh gọi request dồn dập
                spider.random_delay()
                
    except Exception as e:
        logger.error(f"🔥 Critical crawler error: {e}")
    finally:
        conn.close()
        logger.info("🛑 Crawler finished")

if __name__ == "__main__":
    try:
        asyncio.run(run(1, 10000))
    except KeyboardInterrupt:
        logger.info("🛑 Stopped by user (Ctrl+C)")
