"""
TechPowerUp Spider — Crawl cấu hình VGA từ vgabios collection
"""

import re
import logging
from .base import BaseSpider

logger = logging.getLogger(__name__)

class TechPowerUpSpider(BaseSpider):
    name = "techpowerup"
    shop_name = "TechPowerUp"
    base_url = "https://www.techpowerup.com/vgabios"

    async def crawl_products(self, page) -> list[dict]:
        """Crawl list of products (Không dùng cho crawler tuần tự từ 1->10000)"""
        return []

    async def get_bios_details(self, page, bios_id: int) -> dict | None:
        """Crawl dữ liệu từ một ID vgabios cụ thể."""
        url = f"{self.base_url}/{bios_id}/"
        try:
            logger.info(f"[{self.name}] Crawling BIOS ID {bios_id} - URL: {url}")
            
            # Đợi tải content
            response = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            
            if response and response.status == 404:
                logger.warning(f"[{self.name}] BIOS ID {bios_id} không tồn tại (404).")
                return None
                
            has_cardinfo = await page.evaluate("() => !!document.querySelector('section.cardinfo')")
            if not has_cardinfo:
                logger.warning(f"[{self.name}] Không tìm thấy thẻ section.cardinfo tại trang {bios_id}.")
                return None

            # Dùng execute JS để trích xuất dữ liệu từ Table/Description List trong phần cardinfo
            data = await page.evaluate("""
                () => {
                    const getThValue = (label) => {
                        const ths = Array.from(document.querySelectorAll('section.cardinfo table th'));
                        for (let th of ths) {
                            if (th.textContent.trim().toLowerCase().includes(label.toLowerCase())) {
                                const td = th.nextElementSibling;
                                return td ? td.textContent.trim() : '';
                            }
                        }
                        
                        const dts = Array.from(document.querySelectorAll('section.cardinfo dl dt'));
                        for (let dt of dts) {
                            if (dt.textContent.trim().toLowerCase().includes(label.toLowerCase())) {
                                const dd = dt.nextElementSibling;
                                return dd ? dd.textContent.trim() : '';
                            }
                        }
                        return '';
                    };
                    const getImageUrl = () => {
                        const img = document.querySelector('section.cardimage figure a img');
                        return img ? img.src : '';
                    };

                    const vgaName = getThValue('Model') || 'Unknown GPU';
                    const brand = getThValue('Manufacturer') || 'Unknown Brand';
                    const memSize = getThValue('Memory Size');
                    const memType = getThValue('Memory Type');
                    const gpuClock = getThValue('GPU Clock');
                    const memClock = getThValue('Memory Clock');
                    const interfaceType = getThValue('Bus Interface');
                    const imgUrl = getImageUrl();

                    return {
                        name: vgaName,
                        brand: brand,
                        specs: {
                            memory_size: memSize,
                            memory_type: memType,
                            gpu_clock: gpuClock,
                            memory_clock: memClock,
                            interface: interfaceType
                        },
                        image_url: imgUrl,
                        url: location.href
                    }
                }
            """)

            vga_name = data.get("name", "Unknown GPU")
            brand = data.get("brand", "Unknown")
            
            # Build full name: Brand + GPU Model + Mem Size
            parts = [brand, vga_name]
            if data["specs"].get("memory_size"):
                parts.append(data["specs"]["memory_size"])
            
            # Xóa Unknown ra khỏi tên nếu có
            clean_parts = [p for p in parts if "Unknown" not in p and p.strip()]
            full_name = " ".join(clean_parts)
            
            if not full_name:
                full_name = f"VGA BIOS ID {bios_id}"

            slug = self._make_slug(f"{full_name}-bios-{bios_id}")

            filtered_specs = {k: v for k, v in data["specs"].items() if v}

            product = {
                "name": full_name.strip(),
                "slug": slug,
                "category": "gpu",
                "brand": self._extract_brand(brand),
                "specs": filtered_specs,
                "image_url": data["image_url"] if data["image_url"] else "",
                "description": f"Specs crawled from TechPowerUp VGA BIOS database (ID: {bios_id}).",
                "base_price": 0,  # Không có giá từ database BIOS
                "shop_url": data["url"]
            }

            return product

        except Exception as e:
            logger.error(f"[{self.name}] Lỗi khi crawl BIOS ID {bios_id}: {e}")
            return None

    def crawl_prices(self, page, product_slug: str) -> dict | None:
        return None

    def _make_slug(self, name: str) -> str:
        slug = str(name).lower().strip()
        slug = str(re.sub(r"[^\w\s-]", "", slug))
        slug = str(re.sub(r"[\s_]+", "-", slug))
        slug = str(re.sub(r"-+", "-", slug))
        return slug[:200]

    def _extract_brand(self, brand: str) -> str:
        base_brand = brand.split(" ")[0] if brand else "Unknown"
        brands = [
            "ASUS", "MSI", "Gigabyte", "Zotac", "NVIDIA", "AMD", 
            "EVGA", "Sapphire", "PowerColor", "XFX", "Palit", "GALAX",
            "Inno3D", "Colorful", "PNY", "Gainward"
        ]
        brand_lower = brand.lower()
        for b in brands:
            if b.lower() in brand_lower:
                return b
        return base_brand
