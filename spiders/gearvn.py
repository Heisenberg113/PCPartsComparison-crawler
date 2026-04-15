"""
GearVN Spider — Crawl sản phẩm và giá từ gearvn.com
"""

import re
import logging
from .base import BaseSpider

logger = logging.getLogger(__name__)


class GearVNSpider(BaseSpider):
    name = "gearvn"
    shop_name = "GearVN"
    base_url = "https://gearvn.com"

    category_urls = {
        "cpu": "/collections/cpu-bo-vi-xu-ly",
        "gpu": "/collections/vga-card-man-hinh",
        "ram": "/collections/ram-pc",
        "ssd": "/collections/ssd",
        "mainboard": "/collections/mainboard",
        "psu": "/collections/psu-nguon-may-tinh",
        "case": "/collections/case-thung-may-tinh",
        "cooler": "/collections/tan-nhiet-may-tinh",
        "monitor": "/collections/lcd-man-hinh-may-tinh",
    }

    async def crawl_products(self, page) -> list[dict]:
        """Crawl sản phẩm từ GearVN."""
        all_products = []

        for category, url_path in self.category_urls.items():
            try:
                logger.info(f"[{self.name}] Crawling category: {category}")
                url = f"{self.base_url}{url_path}"

                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                
                try:
                    await page.wait_for_selector('.proloop, .product-item, .cdt-product', timeout=10000)
                except Exception:
                    logger.warning(f"[{self.name}] Timeout waiting for products in {category}")

                self.random_delay()

                products = await page.evaluate("""
                    () => {
                        const items = document.querySelectorAll('.proloop, .product-item, .cdt-product');
                        return Array.from(items).slice(0, 20).map(item => {
                            const nameEl = item.querySelector('.proloop-name a, .product-name, h3 a');
                            const priceEl = item.querySelector('.proloop-price .price, .product-price, .price-new');
                            const imgEl = item.querySelector('.proloop-img img, .product-img img, img');
                            const linkEl = item.querySelector('.proloop-name a, h3 a, a');

                            const priceText = priceEl ? priceEl.textContent.replace(/[^0-9]/g, '') : '0';

                            return {
                                name: nameEl ? nameEl.textContent.trim() : '',
                                price: parseInt(priceText) || 0,
                                image_url: imgEl ? (imgEl.dataset.src || imgEl.src || '') : '',
                                url: linkEl ? linkEl.href : '',
                            };
                        }).filter(p => p.name && p.price > 0);
                    }
                """)

                for product in products:
                    slug = self._make_slug(product["name"])
                    all_products.append({
                        "name": product["name"],
                        "slug": slug,
                        "category": category,
                        "brand": self._extract_brand(product["name"]),
                        "specs": {},
                        "image_url": product["image_url"],
                        "description": "",
                        "base_price": product["price"],
                        "shop_url": product["url"],
                    })

                logger.info(f"[{self.name}] Found {len(products)} products in {category}")
                self.random_delay()

            except Exception as e:
                logger.error(f"[{self.name}] Error crawling {category}: {e}")
                continue

        return all_products

    async def crawl_prices(self, page, product_slug: str) -> dict | None:
        return None

    def _make_slug(self, name: str) -> str:
        slug = str(name).lower().strip()
        slug = str(re.sub(r"[^\w\s-]", "", slug))
        slug = str(re.sub(r"[\s_]+", "-", slug))
        slug = str(re.sub(r"-+", "-", slug))
        return slug[:200]

    def _extract_brand(self, name: str) -> str:
        brands = [
            "Intel", "AMD", "NVIDIA", "ASUS", "MSI", "Gigabyte", "ASRock",
            "Corsair", "G.Skill", "Kingston", "Samsung", "Western Digital", "WD",
            "Seagate", "Crucial", "NZXT", "Cooler Master", "be quiet!",
            "Thermaltake", "Lian Li", "Noctua", "LG", "Dell", "ViewSonic",
            "EVGA", "Zotac", "Sapphire", "PowerColor", "XFX", "Palit",
        ]
        name_lower = name.lower()
        for brand in brands:
            if brand.lower() in name_lower:
                return brand
        return "Unknown"
