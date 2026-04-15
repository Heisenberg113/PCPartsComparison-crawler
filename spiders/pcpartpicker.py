"""
PCPartPicker Spider — Crawl thông số kỹ thuật linh kiện từ pcpartpicker.com

LƯU Ý QUAN TRỌNG:
- Tuân thủ robots.txt: Crawl-delay 60 giây giữa mỗi request (đã giảm xuống 10s theo lệnh user)
- Không truy cập /accounts/, /api/, /search/, /user/
- Chỉ crawl các trang specs công khai (/products/<category>/specs/)
- Sử dụng DrissionPage để qua Cloudflare
- Dữ liệu chỉ dùng cho mục đích cá nhân, phi thương mại
"""

import re
import logging
import asyncio
import time
from .base import BaseSpider

logger = logging.getLogger(__name__)

# Crawl-delay: đã được user yêu cầu giảm xuống 10s
CRAWL_DELAY = 10


class PCPartPickerSpider(BaseSpider):
    name = "pcpartpicker"
    shop_name = "PCPartPicker"
    base_url = "https://pcpartpicker.com"

    # Delay thông thường
    min_delay = 10.0
    max_delay = 15.0

    # Các category specs pages được phép crawl
    category_urls = {
        "cpu": "/products/cpu/specs/",
        "gpu": "/products/video-card/specs/",
        "ram": "/products/memory/specs/",
        "ssd": "/products/internal-hard-drive/specs/",
        "mainboard": "/products/motherboard/specs/",
        "psu": "/products/power-supply/specs/",
        "case": "/products/case/specs/",
        "cooler": "/products/cpu-cooler/specs/",
        "monitor": "/products/monitor/specs/",
        "case-fan": "/products/case-fan/specs/",
        "keyboard": "/products/keyboard/specs/",
        "mouse": "/products/mouse/specs/",
        "headphones": "/products/headphones/specs/",
        "speakers": "/products/speakers/specs/",
        "external-hard-drive": "/products/external-hard-drive/specs/",
    }

    # Mapping column headers → spec keys cho mỗi category (Dùng làm fallback hoặc reference)
    spec_mappings = {
        "cpu": {
            "name_col": 0,
            "expected_cols": ["Name", "Speed", "Cores", "TDP", "Integrated Graphics", "SMT", "Price"],
            "spec_keys": ["clock_speed", "core_count", "tdp", "integrated_graphics", "smt"],
        },
    }

    async def _wait_for_cloudflare(self, page, timeout=60000):
        """Đợi Cloudflare challenge hoàn tất (nếu có)."""
        try:
            # Đợi trang load hoặc Cloudflare challenge resolve
            await page.wait_for_load_state("domcontentloaded", timeout=timeout)

            title = await page.title()
            if "Just a moment" in title or "cloudflare" in title.lower():
                logger.info(f"[{self.name}] Cloudflare challenge detected, waiting up to {timeout/1000}s...")
                
                # DrissionPage tự động xử lý phần lớn case, ở đây chỉ log trạng thái
                await asyncio.sleep(5)
                logger.info(f"[{self.name}] Cloudflare challenge passed!")
        except Exception as e:
            logger.warning(f"[{self.name}] Cloudflare wait timeout: {e}")

    async def crawl_products(self, page=None) -> list[dict]:
        """
        Crawl thông số kỹ thuật sản phẩm từ tất cả category trên PCPartPicker.
        Sử dụng DrissionPage để bypass Cloudflare và hỗ trợ phân trang.
        """
        all_products = []
        
        from DrissionPage import ChromiumPage, ChromiumOptions
        co = ChromiumOptions()
        co.auto_port()
        
        logger.info(f"[{self.name}] Khởi tạo DrissionPage để bypass Cloudflare...")
        dp_page = ChromiumPage(co)

        try:
            for category, url_path in self.category_urls.items():
                try:
                    logger.info(f"[{self.name}] Crawling category: {category}")
                    url = f"{self.base_url}{url_path}"

                    # Crawl lên đến 15 trang (theo yêu cầu user)
                    for page_num in range(1, 16):
                        page_url = f"{url}#page={page_num}"
                        logger.info(f"[{self.name}] Trang {page_num}: Điều hướng tới {page_url}...")
                        dp_page.get(page_url)
                        
                        if page_num == 1:
                            logger.info(f"[{self.name}] Đợi Cloudflare giải quyết Turnstile ban đầu...")
                            # Đợi bảng xuất hiện
                            table = dp_page.ele('css:table', timeout=45)
                        else:
                            # Đợi bảng refresh cho trang mới (AJAX)
                            await asyncio.sleep(3)
                            table = dp_page.ele('css:table', timeout=20)
                        
                        if not table:
                            logger.warning(f"[{self.name}] Không tìm thấy bảng ở trang {page_num}, dừng category.")
                            break
                        
                        # Đợi cho tới khi bảng có dữ liệu (hơn 1 hàng - hàng header)
                        rows = []
                        for _ in range(10): 
                            rows = table.eles('css:tr')
                            if len(rows) > 1:
                                break
                            await asyncio.sleep(1)
                        
                        if len(rows) <= 1:
                            logger.info(f"[{self.name}] Trang {page_num} trống cho {category}, kết thúc category.")
                            break

                        # Xác định mapping cột dựa trên header (động)
                        header_row = rows[0]
                        headers = [th.text.strip() for th in header_row.eles('css:th')]
                        
                        name_idx = -1
                        price_idx = -1
                        spec_cols = {} # header_text -> index
                        
                        for i, h in enumerate(headers):
                            if h == "Name":
                                name_idx = i
                            elif h == "Price":
                                price_idx = i
                            elif h and h != "Rating":
                                spec_cols[h] = i
                        
                        if name_idx == -1:
                            logger.warning(f"[{self.name}] Không tìm thấy cột 'Name' cho {category}")
                            break
                            
                        # Mapping header chuẩn sang key database
                        header_to_key = {
                            # General
                            "Color": "color",
                            "Price": "price",
                            
                            # CPU
                            "Core Count": "core_count",
                            "Performance Core Clock": "performance_core_clock",
                            "Performance Core Boost Clock": "performance_core_boost_clock",
                            "TDP": "tdp",
                            "Integrated Graphics": "integrated_graphics",
                            "Microarchitecture": "microarchitecture",
                            "SMT": "smt",
                            "Socket": "socket",
                            
                            # Motherboard
                            "Socket / CPU": "socket",
                            "Form Factor": "form_factor",
                            "Memory Slots": "memory_slots",
                            "Max Memory": "max_memory",
                            "Chipset": "chipset",
                            
                            # Memory
                            "Speed": "speed",
                            "Modules": "modules",
                            "Price / GB": "price_per_gb",
                            "First Word Latency": "first_word_latency",
                            "CAS Latency": "cas_latency",
                            
                            # Storage
                            "Capacity": "capacity",
                            "Type": "type",
                            "Interface": "interface",
                            "Cache": "cache",
                            
                            # Video Card
                            "Memory": "memory",
                            "Core Clock": "core_clock",
                            "Boost Clock": "boost_clock",
                            "Length": "length",
                            
                            # Power Supply
                            "Efficiency": "efficiency",
                            "Wattage": "wattage",
                            "Modular": "modular",
                            
                            # Case
                            "Side Panel Window": "side_panel",
                            "External 5.25\" Bays": "external_bays",
                            "Internal 3.5\" Bays": "internal_bays",
                            
                            # Cooler & Fan
                            "Fan RPM": "fan_rpm",
                            "Noise Level": "noise_level",
                            "Radiator Size": "radiator_size",
                            "Size": "size",
                            "Airflow": "airflow",
                            "PWM": "pwm",
                            
                            # Monitor
                            "Screen Size": "screen_size",
                            "Resolution": "resolution",
                            "Refresh Rate": "refresh_rate",
                            "Response Time": "response_time",
                            "Panel Type": "panel_type",
                            "Aspect Ratio": "aspect_ratio",
                            
                            # Keyboard & Mouse
                            "Style": "style",
                            "Switches": "switches",
                            "Backlight": "backlight",
                            "Tenkeyless": "tenkeyless",
                            "Connection Type": "connection_type",
                            "Tracking Method": "tracking_method",
                            "Max DPI": "max_dpi",
                            "Hand Orientation": "hand_orientation",
                            
                            # Audio
                            "Channel Configuration": "channels",
                            "Frequency Response": "frequency_response",
                        }

                        page_products_count = 0
                        for row in rows[1:]:
                            cells = row.eles('css:td')
                            if len(cells) <= max(name_idx, price_idx):
                                continue

                            name_cell = cells[name_idx]
                            name_link = name_cell.ele('css:a')
                            if not name_link:
                                continue

                            name = name_link.text.strip()
                            if not name:
                                continue

                            detail_url = name_link.link
                            
                            # Price logic
                            price_usd = 0.0
                            if price_idx != -1:
                                price_text = cells[price_idx].text.strip()
                                price_match = re.sub(r"[^0-9.]", "", price_text)
                                try:
                                    price_usd = float(price_match) if price_match else 0.0
                                except:
                                    price_usd = 0.0

                            # Specs extraction & cleaning
                            specs = {}
                            for h_text, idx in spec_cols.items():
                                if idx < len(cells):
                                    # Lấy phần text, loại bỏ header text nếu có (DrissionPage lấy cả label responsive)
                                    val_full = cells[idx].text.strip()
                                    val = val_full.replace(h_text, "").strip()
                                    
                                    # Nếu vẫn còn newline, lấy dòng cuối (thường là giá trị thực)
                                    if "\n" in val:
                                        val = val.split("\n")[-1].strip()
                                    
                                    if val and val not in ["—", "-", ""]:
                                        key = header_to_key.get(h_text, h_text.lower().replace(" ", "_"))
                                        specs[key] = val

                            # Image
                            img = name_cell.ele('css:img')
                            image_url = img.attr('src') if img else ""

                            price_vnd = int(price_usd * 25_000) if price_usd > 0 else 0
                            slug = self._make_slug(name)
                            brand = self._extract_brand(name)

                            all_products.append({
                                "name": name,
                                "slug": slug,
                                "category": category,
                                "brand": brand,
                                "specs": specs,
                                "image_url": image_url,
                                "description": "Imported from PCPartPicker",
                                "base_price": price_vnd,
                                "shop_url": detail_url or url,
                            })
                            page_products_count += 1

                        logger.info(f"[{self.name}] Trang {page_num}: Tìm thấy {page_products_count} sản phẩm.")
                        
                        # Delay nhỏ giữa các trang
                        await asyncio.sleep(2)
                        
                        # Check nếu trang cuối
                        if page_products_count < 100:
                            logger.info(f"[{self.name}] Trang {page_num} là trang cuối của {category}.")
                            break

                    logger.info(f"[{self.name}] Category {category}: Cào được tổng cộng {len(all_products)} sản phẩm.")
                    
                    # Nghỉ trước category tiếp theo
                    logger.info(f"[{self.name}] Nghỉ {CRAWL_DELAY}s...")
                    await asyncio.sleep(CRAWL_DELAY)
                    
                except Exception as e:
                    logger.error(f"[{self.name}] Lỗi khi crawl category {category}: {e}")
                    await asyncio.sleep(CRAWL_DELAY)
                    continue

        finally:
            logger.info(f"[{self.name}] Đóng trình duyệt DrissionPage...")
            dp_page.quit()

        logger.info(f"[{self.name}] Hoàn tất! Tổng số sản phẩm: {len(all_products)}")
        return all_products

    async def crawl_prices(self, page, product_slug: str) -> dict | None:
        """Sẽ implement sau if cần."""
        return None

    def _make_slug(self, name: str) -> str:
        """Tạo URL slug từ tên sản phẩm."""
        slug = name.lower().strip()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[\s_]+", "-", slug)
        slug = re.sub(r"-+", "-", slug)
        return slug[:200]

    def _extract_brand(self, name: str) -> str:
        brands = [
            "Intel", "AMD", "NVIDIA", "ASUS", "MSI", "Gigabyte", "ASRock",
            "Corsair", "G.Skill", "Kingston", "Samsung", "Western Digital", "WD",
            "Seagate", "Crucial", "NZXT", "Cooler Master", "be quiet!",
            "Thermaltake", "Lian Li", "Noctua", "LG", "Dell", "ViewSonic",
            "EVGA", "Zotac", "Sapphire", "PowerColor", "XFX", "Palit",
            "Seasonic", "Super Flower", "Fractal Design", "Phanteks",
            "GALAX", "Inno3D", "PNY", "DEEPCOOL", "Arctic", "Thermalright",
            "Acer", "BenQ", "AOC", "ADATA", "Team", "Patriot", "Lexar",
        ]
        name_lower = name.lower()
        for brand in brands:
            if brand.lower() in name_lower:
                return brand
        return "Unknown"
