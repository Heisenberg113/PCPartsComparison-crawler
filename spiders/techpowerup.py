"""
TechPowerUp Spider — Crawl cấu hình VGA từ gpu specs
"""

import re
import logging
from .base import BaseSpider

logger = logging.getLogger(__name__)

class RateLimitError(Exception):
    """Raised when TechPowerUp returns 429 Too Many Requests"""
    pass

class TechPowerUpSpider(BaseSpider):
    async def crawl_products(self, page) -> list[dict]:
        """Crawl list of products (Không dùng cho crawler tuần tự từ 1->10000)"""
        return []

    async def get_details(self, page, url: str) -> dict | None:
        """Crawl dữ liệu từ một link cụ thể"""
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            
            # Phát hiện rate limit (429 Too Many Requests)
            if response and response.status == 429:
                raise RateLimitError(f"HTTP 429 tại {url}")
            
            # Kiểm tra thêm nội dung trang có chứa thông báo rate limit
            page_title = await page.title()
            if "too many" in page_title.lower() or "rate limit" in page_title.lower():
                raise RateLimitError(f"Rate limit detected (title: {page_title}) tại {url}")
            data = await page.evaluate("""
                () => {
                    const getDtValue = (label, keepHtml = false) => {
                        const dts = Array.from(document.querySelectorAll('section.details dl dt'));
                        for (let dt of dts) {
                            if (dt.textContent.trim().toLowerCase().includes(label.toLowerCase())) {
                                const dd = dt.nextElementSibling;
                                if (!dd) return '';
                                return keepHtml ? dd.innerHTML.trim() : dd.innerText.trim();
                            }
                        }
                        return '';
                    };
                    const getImageUrl = () => {
                        const img = document.querySelector('img.gpudb-large-image__img'); 
                        return img ? img.src : '';
                    };
                    const getName = () => {
                        const name = document.querySelector('h1.gpudb-name'); 
                        return name ? name.innerHTML.trim() : '';
                    };
                    const getBrand = () => {
                        const name = document.querySelector('h1.gpudb-name'); 
                        return name ? name.innerHTML.trim().split(" ")[0] : '';
                    };
                    const getDescription = () => {
                        const el = document.querySelector('div.desc.p');
                        return el ? el.innerHTML.trim() : '';
                    };
                        
                    const vgaName = getName() || 'Unknown GPU';
                    const brand = getBrand() || 'Unknown Brand';
                    const description = getDescription() || 'No description';
                    const releaseDate = getDtValue('Release Date') || 'N/A';
                    const architecture = getDtValue('Architecture') || 'N/A';
                    const foundry = getDtValue('Foundry') || 'N/A';
                    const generation = getDtValue('Generation') || 'N/A';
                    const interfaceType = getDtValue('Bus Interface') || 'N/A';
                    const memSize = getDtValue('Memory Size') || 'N/A';
                    const memType = getDtValue('Memory Type') || 'N/A';
                    const memBus = getDtValue('Memory Bus') || 'N/A';
                    const memBandwidth = getDtValue('Memory Bandwidth') || 'N/A';
                    const baseClock = getDtValue('Base Clock', true) || 'N/A';
                    const boostClock = getDtValue('Boost Clock', true) || 'N/A';
                    const memClock = getDtValue('Memory Clock', true) || 'N/A';
                    const tdp = getDtValue('TDP', true) || 'N/A';
                    const powerConnectors = getDtValue('Power Connectors', true) || 'N/A';
                    const slotWidth = getDtValue('Slot Width', true) || 'N/A';
                    const shadingUnits = getDtValue('Shading Units') || 'N/A';
                    const imgUrl = getImageUrl();

                    return {
                        name: vgaName,
                        brand: brand,
                        description: description,
                        specs: {
                            release_date: releaseDate,
                            generation: generation,
                            architecture: architecture,
                            foundry: foundry,
                            memory_size: memSize,
                            memory_type: memType,
                            memory_bus: memBus,
                            memory_bandwidth: memBandwidth,
                            base_clock: baseClock,
                            boost_clock: boostClock,
                            memory_clock: memClock,
                            interface: interfaceType,
                            tdp: tdp,
                            power_connectors: powerConnectors,
                            slot_width: slotWidth,
                            shading_units: shadingUnits
                        },
                        image_url: imgUrl,
                    }
                }
            """)

            vga_name = data.get("name", "Unknown GPU")
            brand = data.get("brand", "Unknown")

            slug = self._make_slug(f"{vga_name}")

            filtered_specs = {k: v for k, v in data["specs"].items() if v}

            product = {
                "name": vga_name,
                "slug": slug,
                "category": "gpu",
                "brand": self._extract_brand(brand),
                "specs": filtered_specs,
                "image_url": data["image_url"] if data["image_url"] else "",
                "description": f"{data['description']}\n\nSpecs crawled from TechPowerUp GPU database",
                "base_price": 0,  # Không có giá từ database BIOS
            }

            return product

        except RateLimitError:
            raise  # Để runner xử lý rate limit (rotate proxy)
        except Exception as e:
            logger.error(f"[{self.name}] Lỗi khi crawl link {url}: {e}")
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
            "3dfx", "AMD", "ATI", "Intel", "Matrox", "Moore Threads", "NVIDIA", "S3", "SiS", "Sony", "VIA", "XGI"
        ]
        brand_lower = brand.lower()
        for b in brands:
            if b.lower() in brand_lower:
                return b
        return base_brand
