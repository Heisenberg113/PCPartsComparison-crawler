"""
PCPartPicker Spider — Crawl thông số kỹ thuật linh kiện từ pcpartpicker.com

LƯU Ý QUAN TRỌNG:
- Tuân thủ robots.txt: Crawl-delay 10 giây giữa mỗi category
- Không truy cập /accounts/, /api/, /search/, /user/
- Chỉ crawl các trang specs công khai (/products/<category>/specs/)
- Detail pages của PCPartPicker là SPA (client-side rendered), phải dùng DrissionPage
- Dữ liệu chỉ dùng cho mục đích cá nhân, phi thương mại
"""

import re
import logging
import time
import tempfile
import shutil
from bs4 import BeautifulSoup
from .base import BaseSpider

logger = logging.getLogger(__name__)

CRAWL_DELAY = 10
DETAIL_DELAY = 1.0

# Các cột spec cần lấy thêm từ list page (nhanh, không cần vào detail)
LIST_QUICK_SPECS: dict[str, list[str]] = {
    "gpu":      ["Chipset", "Memory"],
    "psu":      ["Wattage"],
    "ram":      ["Speed", "Modules"],
}


class PCPartPickerSpider(BaseSpider):
    name = "pcpartpicker"
    shop_name = "PCPartPicker"
    base_url = "https://pcpartpicker.com"

    min_delay = 10.0
    max_delay = 15.0

    category_urls = {
        # "cpu": "/products/cpu/specs/",
        # "gpu": "/products/video-card/specs/",
        "ram": "/products/memory/specs/",
        # "harddrive": "/products/internal-hard-drive/specs/",
        # "mainboard": "/products/motherboard/specs/",
        # "psu": "/products/power-supply/specs/",
        # "case": "/products/case/specs/",
        # "cooler": "/products/cpu-cooler/specs/",
        # "monitor": "/products/monitor/specs/",
    }

    # ──────────────────────────────────────────────
    # CDP HTML FETCH (non-blocking — bypasses JS execution queue)
    # ──────────────────────────────────────────────

    def _get_page_html(self, dp_page) -> str:
        """Lấy HTML hiện tại qua CDP DOM.getOuterHTML — không đợi JS hoàn thành."""
        try:
            doc = dp_page.run_cdp('DOM.getDocument', depth=0)
            root_node_id = doc.get('root', {}).get('nodeId')
            if root_node_id:
                result = dp_page.run_cdp('DOM.getOuterHTML', nodeId=root_node_id)
                return result.get('outerHTML', '')
        except Exception as e:
            logger.warning(f"CDP getOuterHTML thất bại: {e}")
        return ''

    # ──────────────────────────────────────────────
    # PARSERS (BeautifulSoup — từ HTML string)
    # ──────────────────────────────────────────────

    def _parse_specs_bs(self, html: str) -> dict:
        """Parse specs từ HTML string bằng BeautifulSoup."""
        soup = BeautifulSoup(html, 'lxml')
        specs = {}

        spec_groups = soup.select('.block.xs-hide.md-block.specs .group.group--spec')
        if not spec_groups:
            spec_groups = soup.select('.block.xs-block.md-hide.specs .group.group--spec')
        if not spec_groups:
            logger.warning("Không tìm thấy spec groups")
            return specs

        for group in spec_groups:
            try:
                title_el = group.select_one('h3.group__title')
                if not title_el:
                    continue
                group_title = title_el.get_text(strip=True)
                if not group_title:
                    continue

                content_div = group.select_one('div.group__content')
                if not content_div:
                    continue

                # Dạng 1: ul > li — nhiều giá trị
                li_eles = content_div.select('ul li')
                if li_eles:
                    values = [
                        li.get_text(strip=True) for li in li_eles
                        if li.get_text(strip=True) and li.get_text(strip=True) not in ['—', '-']
                    ]
                    if values:
                        specs[group_title] = values[0] if len(values) == 1 else values
                    continue

                # Dạng 2: p tag — single value
                p_el = content_div.select_one('p')
                if p_el:
                    val = p_el.get_text(strip=True)
                    if val and val not in ['—', '-', '']:
                        specs[group_title] = val
                    continue

                # Dạng 3: fallback — text trực tiếp
                val = content_div.get_text(strip=True)
                if val and val not in ['—', '-', '']:
                    specs[group_title] = val

            except Exception as e:
                logger.error(f"Lỗi parse group: {e}")
                continue

        return specs

    def _parse_rating_bs(self, html: str) -> tuple:
        """Parse (rating_avg, rating_count) từ HTML string bằng BeautifulSoup."""
        soup = BeautifulSoup(html, 'lxml')
        rating_avg = None
        rating_count = None

        # PCPartPicker: <ul class="product--rating list-unstyled">
        # Text node kế bên: "(15 Ratings, 4.9 Average)"
        ul_rating = soup.select_one('ul.product--rating')
        if ul_rating:
            parent = ul_rating.parent
            if parent:
                parent_text = parent.get_text()
                m = re.search(
                    r'\((\d[\d,]*)\s+Ratings?,\s+([\d.]+)\s+Average\)',
                    parent_text, re.IGNORECASE
                )
                if m:
                    try:
                        rating_count = int(m.group(1).replace(',', ''))
                        rating_avg = float(m.group(2))
                    except (ValueError, IndexError):
                        pass

        return rating_avg, rating_count

    def _parse_img_url_bs(self, html: str) -> str:
        """Parse image URL từ HTML string."""
        soup = BeautifulSoup(html, 'lxml')

        # Ưu tiên og:image — luôn có trong <head>, không cần JS hay lazy load
        og = soup.select_one('meta[property="og:image"]')
        if og:
            url = og.get('content', '').strip()
            if url and not url.startswith('data:'):
                return url if url.startswith('http') else f'https:{url}'

        # Fallback: img.product__image — kiểm tra src và data-src
        img = soup.select_one('img.product__image')
        if img:
            url = (img.get('src') or img.get('data-src') or '').strip()
            if url and not url.startswith('data:'):
                return url if url.startswith('http') else f'https:{url}'

        return ''

    # ──────────────────────────────────────────────
    # DETAIL FETCHING (DrissionPage — tuần tự)
    # ──────────────────────────────────────────────

    def _fetch_details_with_browser(self, items: list, dp_page) -> list[dict]:
        """Fetch và parse detail pages tuần tự bằng DrissionPage."""
        results = []
        total = len(items)

        for idx, item in enumerate(items, 1):
            name = item['name']
            detail_url = item['detail_url']
            category = item['category']

            try:
                logger.info(f"[{idx}/{total}] {name[:55]}")

                try:
                    dp_page.get(detail_url, timeout=25)
                except Exception:
                    pass  # Timeout OK — explicit element wait below

                # Chờ specs element xuất hiện (xác nhận DOM đã render)
                dp_page.ele('css:.group.group--spec', timeout=20)

                # Kiểm tra CF challenge — chỉ dùng URL, không đọc body
                current_url = dp_page.url or ''
                if 'challenges.cloudflare' in current_url:
                    logger.warning("CF challenge! Đợi 30s...")
                    time.sleep(30)
                    try:
                        dp_page.get(detail_url, timeout=25)
                    except Exception:
                        pass
                    dp_page.ele('css:.group.group--spec', timeout=20)

                # Lấy HTML qua CDP (không chờ JS — bypass hàng đợi analytics scripts)
                page_html = self._get_page_html(dp_page)

                if page_html:
                    specs = self._parse_specs_bs(page_html)
                    # Bổ sung quick specs từ list page (detail page ưu tiên hơn)
                    for k, v in item.get('list_specs', {}).items():
                        if k not in specs:
                            specs[k] = v
                    rating_avg, rating_count = self._parse_rating_bs(page_html)
                    img_url = self._parse_img_url_bs(page_html)
                else:
                    logger.warning(f"CDP HTML trống, bỏ qua {detail_url[:60]}")
                    time.sleep(3)
                    continue

                results.append({
                    'name': name,
                    'slug': self._make_slug(name),
                    'category': category,
                    'brand': self._extract_brand(name),
                    'specs': specs,
                    'image_url': img_url,
                    'description': 'Imported from PCPartPicker',
                    'base_price': 0,
                    'shop_url': detail_url,
                    'rating_avg': rating_avg,
                    'rating_count': rating_count,
                })

                logger.info(f"  → {len(specs)} specs"
                            + (f" | rating={rating_avg}" if rating_avg else ''))
                time.sleep(DETAIL_DELAY)

            except Exception as e:
                logger.error(f"Lỗi {detail_url[:60]}: {e}")
                time.sleep(3)
                continue

        return results

    # ──────────────────────────────────────────────
    # MAIN CRAWL
    # ──────────────────────────────────────────────

    async def crawl_products(self, _page=None) -> list[dict]:
        all_products = []

        from DrissionPage import ChromiumPage, ChromiumOptions

        user_data_dir = tempfile.mkdtemp(prefix='pcpp_crawl_')
        logger.info(f'[{self.name}] user data dir: {user_data_dir}')

        co = ChromiumOptions()
        co.auto_port()
        co.set_argument(f'--user-data-dir={user_data_dir}')
        co.set_argument('--disable-images')
        co.set_argument('--disable-extensions')
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-gpu')
        main_browser = ChromiumPage(co)
        # load_mode.none() → get() trả về ngay sau khi nhận response đầu tiên
        main_browser.set.load_mode.none()
        main_browser.set.timeouts(page_load=25)

        try:
            for category, url_path in self.category_urls.items():
                try:
                    logger.info(f'[{self.name}] ── Category: {category} ──')
                    url = f'{self.base_url}{url_path}'
                    detail_urls = []

                    # ── BƯỚC 1: Thu thập links từ list pages ────────────────
                    for page_num in range(1, 2):
                        page_url = f'{url}#page={page_num}'
                        logger.info(f'[{self.name}] Trang {page_num}: {page_url}')
                        main_browser.get(page_url)

                        if page_num == 1:
                            logger.info(f'[{self.name}] Đợi Cloudflare...')
                            table = main_browser.ele('css:table', timeout=60)
                            time.sleep(2)
                            logger.info(f'[{self.name}] Cloudflare passed.')
                        else:
                            time.sleep(3)
                            table = main_browser.ele('css:table', timeout=20)

                        if not table:
                            break

                        rows = []
                        for _ in range(10):
                            rows = table.eles('css:tr')
                            if len(rows) > 1:
                                break
                            time.sleep(1)

                        if len(rows) <= 1:
                            break

                        headers = [th.text.strip() for th in rows[0].eles('css:th')]
                        name_idx = next(
                            (i for i, h in enumerate(headers) if h == 'Name'), -1
                        )
                        if name_idx == -1:
                            break

                        # Tìm index của các cột quick-spec cần lấy từ list page
                        quick_spec_names = LIST_QUICK_SPECS.get(category, [])
                        spec_col_indices: dict[str, int] = {}
                        for spec_name in quick_spec_names:
                            for i, h in enumerate(headers):
                                if h.strip() == spec_name:
                                    spec_col_indices[spec_name] = i
                                    break

                        page_links_count = 0
                        for row in rows[1:]:
                            cells = row.eles('css:td')
                            if len(cells) <= name_idx:
                                continue
                            name_link = cells[name_idx].ele('css:a')
                            if not name_link:
                                continue
                            name = name_link.text.strip()
                            # Xoá rating count "(N)" và newline ở cuối tên
                            name = re.sub(r'\s*\(\d+\)\s*$', '', name).strip()
                            name = ' '.join(name.split())
                            link = name_link.link
                            if not (name and link):
                                continue

                            # Lấy quick specs từ các cột list page
                            list_specs: dict[str, str] = {}
                            for spec_name, col_idx in spec_col_indices.items():
                                if col_idx < len(cells):
                                    try:
                                        cell_text = cells[col_idx].text.strip()
                                        # Format: "Label\nValue" — lấy dòng cuối
                                        lines = [l.strip() for l in cell_text.split('\n') if l.strip()]
                                        val = lines[-1] if len(lines) >= 2 else (lines[0] if lines else '')
                                        if val and val not in ['—', '-', '']:
                                            list_specs[spec_name] = val
                                    except Exception:
                                        pass

                            detail_urls.append({
                                'name': name,
                                'detail_url': link,
                                'category': category,
                                'list_specs': list_specs,
                            })
                            page_links_count += 1

                        logger.info(f'[{self.name}] Trang {page_num}: {page_links_count} links')
                        time.sleep(2)

                        if page_links_count < 100:
                            break

                    if not detail_urls:
                        logger.warning(f'[{self.name}] Không có links cho {category}, bỏ qua.')
                        continue

                    logger.info(f'[{self.name}] Tổng {len(detail_urls)} links, bắt đầu fetch...')

                    # ── BƯỚC 2: Fetch detail pages bằng DrissionPage ─────────
                    products = self._fetch_details_with_browser(detail_urls, main_browser)
                    all_products.extend(products)

                    logger.info(f'[{self.name}] Category {category}: {len(products)} sản phẩm.')
                    logger.info(f'[{self.name}] Nghỉ {CRAWL_DELAY}s trước category tiếp theo...')
                    time.sleep(CRAWL_DELAY)

                except Exception as e:
                    logger.error(f'[{self.name}] Lỗi category {category}: {e}', exc_info=True)
                    time.sleep(CRAWL_DELAY)
                    continue

        finally:
            logger.info(f'[{self.name}] Đóng browser chính.')
            main_browser.quit()
            shutil.rmtree(user_data_dir, ignore_errors=True)

        logger.info(f'[{self.name}] Hoàn tất! Tổng: {len(all_products)} sản phẩm')
        return all_products

    async def crawl_prices(self, _page, _product_slug: str) -> dict | None:
        return None

    def _make_slug(self, name: str) -> str:
        slug = name.lower().strip()
        slug = re.sub(r'[^\w\s-]', '', slug)
        slug = re.sub(r'[\s_]+', '-', slug)
        slug = re.sub(r'-+', '-', slug)
        return slug[:200]

    def _extract_brand(self, name: str) -> str:
        brands = [
            'Intel', 'AMD', 'NVIDIA', 'ASUS', 'MSI', 'Gigabyte', 'ASRock',
            'Corsair', 'G.Skill', 'Kingston', 'Samsung', 'Western Digital', 'WD',
            'Seagate', 'Crucial', 'NZXT', 'Cooler Master', 'be quiet!',
            'Thermaltake', 'Lian Li', 'Noctua', 'LG', 'Dell', 'ViewSonic',
            'EVGA', 'Zotac', 'Sapphire', 'PowerColor', 'XFX', 'Palit',
            'Seasonic', 'Super Flower', 'Fractal Design', 'Phanteks',
            'GALAX', 'Inno3D', 'PNY', 'DEEPCOOL', 'Arctic', 'Thermalright',
            'Acer', 'BenQ', 'AOC', 'ADATA', 'Team', 'Patriot', 'Lexar',
        ]
        name_lower = name.lower()
        for brand in brands:
            if brand.lower() in name_lower:
                return brand
        return 'Unknown'
