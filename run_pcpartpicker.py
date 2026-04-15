"""
Chạy riêng spider PCPartPicker.

Usage:
    python run_pcpartpicker.py                 # Crawl tất cả categories
    python run_pcpartpicker.py --category cpu   # Chỉ crawl CPU
    python run_pcpartpicker.py --dry-run        # Chỉ in ra, không lưu DB

Lưu ý:
- Crawl-delay: 60s/request theo robots.txt
- 9 categories → thời gian chạy tối thiểu: ~9 phút
- Cần Playwright: pip install playwright && playwright install chromium
"""

import sys
import asyncio
import logging
from datetime import datetime

from playwright.async_api import async_playwright
from spiders.pcpartpicker import PCPartPickerSpider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pcpartpicker_runner")


async def run(category_filter: str = "", dry_run: bool = False):
    spider = PCPartPickerSpider()

    # Nếu chỉ crawl 1 category
    if category_filter:
        if category_filter not in spider.category_urls:
            logger.error(f"Category '{category_filter}' không hợp lệ.")
            logger.info(f"Danh sách: {', '.join(spider.category_urls.keys())}")
            return
        spider.category_urls = {category_filter: spider.category_urls[category_filter]}

    # PCPartPickerSpider hiện tại đã tự quản lý DrissionPage bên trong crawl_products 
    # để vượt Cloudflare Turnstile nấp đằng sau API /fetch/.
    # Do đó ta không cần khởi tạo Playwright ở đây nữa.
    
    try:
        products: list[dict] = await spider.crawl_products()
    except Exception as e:
        logger.error(f"Lỗi khi chạy crawler: {e}")
        products = []

    logger.info(f"\n{'='*60}")
    logger.info(f"TỔNG KẾT: {len(products)} sản phẩm từ PCPartPicker")
    logger.info(f"{'='*60}")

    if dry_run:
        for p in products[:10]:
            logger.info(f"  {p['category']:>10} | {p['brand']:>15} | {p['name'][:50]:<50} | {p['base_price']:>12,} VND")
        if len(products) > 10:
            logger.info(f"  ... và {len(products) - 10} sản phẩm khác")
    else:
        from db import get_connection, save_product, save_price

        conn = get_connection()
        saved: int = 0
        for product_data in products:
            shop_url = product_data.pop("shop_url", "")
            try:
                product_id = save_product(conn, product_data)
                if product_data["base_price"] > 0:
                    save_price(conn, {
                        "product_id": product_id,
                        "shop_name": spider.shop_name,
                        "price": product_data["base_price"],
                        "url": shop_url,
                        "in_stock": True,
                    })
                saved += 1
            except Exception as e:
                logger.error(f"Error saving: {e}")
                conn.rollback()
        conn.close()
        logger.info(f"Đã lưu {saved}/{len(products)} sản phẩm vào database.")


if __name__ == "__main__":
    category = ""
    dry_run = False

    args = sys.argv
    for i, arg in enumerate(args):
        if arg == "--dry-run":
            dry_run = True
        elif arg in ("--category", "-c"):
            if i + 1 < len(args):
                category = args[i + 1]

    logger.info(f"🚀 PCPartPicker crawler started at {datetime.now()}")
    if category:
        logger.info(f"📂 Category filter: {category}")
    if dry_run:
        logger.info(f"🔍 Dry-run mode (không lưu DB)")

    asyncio.run(run(category, dry_run))
    logger.info("✅ Done!")
