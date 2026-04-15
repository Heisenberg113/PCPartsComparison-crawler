"""
Base Spider class — Lớp cơ sở cho tất cả các Spider crawl dữ liệu.
"""

import time
import random
import logging
from abc import ABC, abstractmethod
from fake_useragent import UserAgent

logger = logging.getLogger(__name__)
ua = UserAgent()


class BaseSpider(ABC):
    """Lớp cơ sở cho spider crawl sản phẩm."""

    name: str = "base"
    shop_name: str = "Unknown"
    base_url: str = ""

    # Anti-bot settings
    min_delay: float = 2.0
    max_delay: float = 5.0

    def get_random_user_agent(self) -> str:
        """Trả về User-Agent ngẫu nhiên."""
        return ua.random

    def random_delay(self):
        """Delay ngẫu nhiên giữa các request để tránh bị chặn."""
        delay = random.uniform(self.min_delay, self.max_delay)
        logger.debug(f"[{self.name}] Waiting {delay:.1f}s...")
        time.sleep(delay)

    @abstractmethod
    async def crawl_products(self, page) -> list[dict]:
        """
        Crawl danh sách sản phẩm từ trang shop.
        Returns: list of product dicts with keys:
            name, slug, category, brand, specs, image_url, description, base_price
        """
        pass

    @abstractmethod
    async def crawl_prices(self, page, product_slug: str) -> dict | None:
        """
        Crawl giá của một sản phẩm cụ thể.
        Returns: dict with keys: price, url, in_stock
        """
        pass
