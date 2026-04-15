"""
Phong Vũ Spider — Crawl sản phẩm và giá từ phongvu.vn

Cấu trúc DOM (tính đến 03/2026):
- Product card: <a class="css-pxdb0j" href="/bo-vi-xu-ly-cpu-...--s260203202">
- Tên sản phẩm: <h3> bên trong card
- Giá: text chứa "₫" bên trong các div con
- Ảnh: <img> bên trong card
- Href: slug dạng /ten-san-pham--sXXXXXX

Lưu ý: Phong Vũ dùng CSS-in-JS, class name có thể thay đổi.
Spider dùng 3 chiến lược fallback để bền vững.
"""

import re
import logging
from .base import BaseSpider

logger = logging.getLogger(__name__)


class PhongVuSpider(BaseSpider):
    name = "phongvu"
    shop_name = "Phong Vũ"
    base_url = "https://phongvu.vn"

    category_urls = {
        "cpu":       "/c/cpu-bo-vi-xu-ly",
        "gpu":       "/c/vga-card-man-hinh",
        "ram":       "/c/ram-bo-nho-trong",
        "ssd":       "/c/o-cung-ssd",
        "mainboard": "/c/mainboard-bo-mach-chu",
        "psu":       "/c/nguon-may-tinh",
        "case":      "/c/case-vo-may-tinh",
        "cooler":    "/c/tan-nhiet",
        "monitor":   "/c/man-hinh-may-tinh",
    }

    async def crawl_products(self, page) -> list[dict]:
        """Crawl sản phẩm từ tất cả categories trên Phong Vũ."""
        all_products = []

        for category, url_path in self.category_urls.items():
            try:
                logger.info(f"[{self.name}] Crawling category: {category}")
                url = f"{self.base_url}{url_path}"

                await page.goto(url, wait_until="domcontentloaded", timeout=60000)

                # Scroll để kích hoạt lazy-load
                for _ in range(4):
                    await page.mouse.wheel(0, 2000)
                    await page.wait_for_timeout(1500)

                # Đợi thêm để JS render xong
                await page.wait_for_timeout(3000)

                # Lấy sản phẩm — 3 chiến lược selector fallback
                products = await page.evaluate("""
                    () => {
                        const results = [];
                        const seen = new Set();

                        // ── Tìm product cards ──────────────────────────────
                        // Chiến lược 1: class css-pxdb0j (CSS-in-JS hiện tại)
                        // Chiến lược 2: link chứa --s (product ID pattern)
                        // Chiến lược 3: bất kỳ <a> nào có h3 con
                        let cards = Array.from(document.querySelectorAll('a.css-pxdb0j'));
                        
                        if (cards.length === 0) {
                            cards = Array.from(document.querySelectorAll('a[href*="--s"]'));
                        }
                        if (cards.length === 0) {
                            cards = Array.from(document.querySelectorAll('a'))
                                .filter(a => a.querySelector('h3') && a.querySelector('img'));
                        }

                        for (const card of cards.slice(0, 30)) {
                            // ── Tên sản phẩm ──
                            const nameEl = card.querySelector('h3');
                            if (!nameEl) continue;
                            const name = nameEl.textContent.trim();
                            if (!name || name.length < 5 || seen.has(name)) continue;
                            seen.add(name);

                            // ── Giá ── tìm text chứa ₫ hoặc đ
                            let price = 0;
                            const walker = document.createTreeWalker(
                                card, NodeFilter.SHOW_TEXT, null
                            );
                            while (walker.nextNode()) {
                                const txt = walker.currentNode.textContent.trim();
                                if ((txt.includes('₫') || txt.endsWith('đ')) && txt.length < 30) {
                                    const nums = txt.replace(/[^0-9]/g, '');
                                    const val = parseInt(nums);
                                    if (val > 10000 && (price === 0 || val < price)) {
                                        price = val;
                                    }
                                }
                            }

                            // ── Ảnh ──
                            const imgEl = card.querySelector('img');
                            const imgUrl = imgEl 
                                ? (imgEl.src || imgEl.dataset?.src || '') 
                                : '';

                            // ── URL ──
                            const productUrl = card.href || '';

                            if (price > 0) {
                                results.push({
                                    name,
                                    price,
                                    image_url: imgUrl,
                                    url: productUrl,
                                });
                            }
                        }

                        return results;
                    }
                """)

                logger.info(f"[{self.name}] Found {len(products)} products in {category}")

                for product in products:
                    slug = self._make_slug(product["name"])
                    all_products.append({
                        "name":        product["name"],
                        "slug":        slug,
                        "category":    category,
                        "brand":       self._extract_brand(product["name"]),
                        "specs":       {},
                        "image_url":   product["image_url"],
                        "description": "",
                        "base_price":  product["price"],
                        "shop_url":    product["url"],
                    })

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
