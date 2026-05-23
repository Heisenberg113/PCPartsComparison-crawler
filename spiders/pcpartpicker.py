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
BLOCK_THRESHOLD = 3   # số lần 0-spec liên tiếp trước khi bỏ batch
BLOCK_SLEEP = 90      # giây nghỉ khi phát hiện bị block


class PCPartPickerSpider(BaseSpider):
    name = "pcpartpicker"
    shop_name = "PCPartPicker"
    base_url = "https://pcpartpicker.com"

    min_delay = 10.0
    max_delay = 15.0

    category_urls = {
        "cpu": "/products/cpu/specs/",
        # "gpu": "/products/video-card/specs/",
        # "ram": "/products/memory/specs/",
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
        consecutive_fails = 0

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

                # Timeout 8s thay vì 20s — phát hiện block nhanh hơn
                dp_page.ele('css:.group.group--spec', timeout=8)

                # Kiểm tra CF challenge — chỉ dùng URL, không đọc body
                current_url = dp_page.url or ''
                if 'challenges.cloudflare' in current_url:
                    logger.warning("CF challenge! Đợi 30s...")
                    time.sleep(30)
                    try:
                        dp_page.get(detail_url, timeout=25)
                    except Exception:
                        pass
                    dp_page.ele('css:.group.group--spec', timeout=8)

                # Lấy HTML qua CDP (không chờ JS — bypass hàng đợi analytics scripts)
                page_html = self._get_page_html(dp_page)

                if not page_html:
                    logger.warning(f"CDP HTML trống, bỏ qua {detail_url[:60]}")
                    consecutive_fails += 1
                    if consecutive_fails >= BLOCK_THRESHOLD:
                        logger.warning(
                            f"[{self.name}] {consecutive_fails} lần liên tiếp lỗi — "
                            f"dừng sớm ({idx}/{total}), nghỉ {BLOCK_SLEEP}s."
                        )
                        time.sleep(BLOCK_SLEEP)
                        break
                    time.sleep(3)
                    continue

                specs = self._parse_specs_bs(page_html)
                rating_avg, rating_count = self._parse_rating_bs(page_html)
                img_url = self._parse_img_url_bs(page_html)

                if not specs:
                    consecutive_fails += 1
                    if consecutive_fails >= BLOCK_THRESHOLD:
                        logger.warning(
                            f"[{self.name}] {consecutive_fails} sản phẩm liên tiếp 0 specs — "
                            f"có thể bị block. Dừng sớm ({idx}/{total}), nghỉ {BLOCK_SLEEP}s."
                        )
                        time.sleep(BLOCK_SLEEP)
                        break
                    time.sleep(3)
                    continue

                consecutive_fails = 0

                # RAM: bổ sung (Modules) Speed vào tên để phân biệt các variant
                # Ví dụ: "Corsair Vengeance RGB 32 GB" → "Corsair Vengeance RGB 32 GB (2 x 16 GB) DDR5-6400"
                if category == 'ram':
                    modules = specs.get('Modules', '')
                    speed = specs.get('Speed', '')
                    suffix = ''
                    if modules:
                        suffix += f' ({modules})'
                    if speed and speed not in name:
                        suffix += f' {speed}'
                    if suffix:
                        name = (name + suffix).strip()

                # GPU: bổ sung Chipset + Memory vào tên
                # Ví dụ: "Gigabyte GAMING OC" → "Gigabyte GAMING OC Radeon RX 9060 XT 16 GB"
                elif category == 'gpu':
                    chipset = specs.get('Chipset', '')
                    memory = specs.get('Memory', '')
                    suffix = ''
                    if chipset and chipset not in name:
                        suffix += f' {chipset}'
                    if memory and memory not in name:
                        suffix += f' {memory}'
                    if suffix:
                        name = (name + suffix).strip()

                # PSU: bổ sung Wattage vào tên
                # Ví dụ: "MSI MAG A750GL PCIE5" → "MSI MAG A750GL PCIE5 750 W"
                elif category == 'psu':
                    wattage = specs.get('Wattage', '')
                    if wattage and wattage not in name:
                        name = f'{name} {wattage}'.strip()

                if category in ('ram', 'gpu', 'psu'):
                    logger.info(f"  → Tên: {name}")

                # Dùng URL slug của PCPartPicker (chứa model number, unique per variant)
                # Ví dụ: arctic-liquid-freezer-iii-pro-a-rgb-360-lf-q360-argb-pwm-r2-bla01
                url_slug = detail_url.rstrip('/').split('/')[-1]
                slug = (url_slug or self._make_slug(name))[:200]

                results.append({
                    'name': name,
                    'slug': slug,
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

    def _save_products(self, conn, products: list) -> int:
        """Lưu một trang sản phẩm vào DB ngay — embed từng cái, trả về số lượng lưu thành công."""
        from db import save_products_batch, save_price
        if not products:
            return 0

        shop_urls = [p.pop('shop_url', '') for p in products]
        try:
            ids = save_products_batch(conn, products)
            saved = sum(1 for i in ids if i is not None)

            for pid, p, url in zip(ids, products, shop_urls):
                if pid and p.get('base_price', 0) > 0:
                    try:
                        save_price(conn, {
                            'product_id': pid,
                            'shop_name': self.shop_name,
                            'price': p['base_price'],
                            'url': url,
                            'in_stock': True,
                        })
                    except Exception as e:
                        logger.error(f'[{self.name}] Lỗi lưu giá product_id={pid}: {e}')
                        conn.rollback()

            return saved
        except Exception as e:
            logger.error(f'[{self.name}] Lỗi lưu DB: {e}')
            conn.rollback()
            return 0

    async def crawl_products(self, _page=None, *, conn=None) -> list[dict]:
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
                    category_saved = 0

                    for page_num in range(7, 11):
                        page_url = f'{url}#page={page_num}'
                        logger.info(f'[{self.name}] Trang {page_num}: {page_url}')
                        main_browser.get(page_url)

                        if page_num == 1:
                            logger.info(f'[{self.name}] Đợi Cloudflare...')
                            table = main_browser.ele('css:table', timeout=60)
                            time.sleep(2)
                            if not table:
                                page_text = (main_browser.ele('css:body') or main_browser).text or ''
                                if 'unavailable' in page_text.lower():
                                    logger.error(
                                        f'[{self.name}] PCPartPicker không truy cập được '
                                        f'(có thể bị geo-block — thử bật VPN rồi chạy lại). Dừng crawl.'
                                    )
                                    return []
                                logger.warning(f'[{self.name}] Không tìm thấy table trang 1 — bỏ qua category.')
                                break
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

                        # Tìm row đầu tiên có th — không giả định rows[0] là header
                        # PCPartPicker đôi khi có sort-arrow trong th: "Speed\n↑" → lấy dòng đầu
                        headers: list[str] = []
                        for _r in rows:
                            ths = _r.eles('css:th')
                            if ths:
                                headers = [
                                    th.text.strip().split('\n')[0].strip()
                                    for th in ths
                                ]
                                break

                        if not headers:
                            logger.warning(f'[{self.name}] Không tìm thấy header row cho {category}')
                            break

                        logger.info(f'[{self.name}] Headers ({len(headers)}): {headers}')

                        name_idx = next(
                            (i for i, h in enumerate(headers) if h == 'Name'), -1
                        )
                        if name_idx == -1:
                            logger.warning(f'[{self.name}] Không tìm thấy cột Name, bỏ qua')
                            break

                        # ── BƯỚC 1: Thu thập links của trang list này ───────
                        detail_urls_this_page = []
                        page_links_count = 0
                        for row in rows:
                            cells = row.eles('css:td')
                            if not cells or len(cells) <= name_idx:
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

                            detail_urls_this_page.append({
                                'name': name,
                                'detail_url': link,
                                'category': category,
                            })
                            page_links_count += 1

                        logger.info(
                            f'[{self.name}] Trang {page_num}: {page_links_count} links'
                            f' — bắt đầu fetch chi tiết...'
                        )

                        # ── BƯỚC 2: Fetch chi tiết + lưu ngay trang này ─────
                        if detail_urls_this_page:
                            products = self._fetch_details_with_browser(
                                detail_urls_this_page, main_browser
                            )

                            if conn is not None:
                                saved = self._save_products(conn, products)
                                category_saved += saved
                                logger.info(
                                    f'[{self.name}] Trang {page_num}: '
                                    f'lưu {saved}/{len(products)} sản phẩm vào DB'
                                )

                        # Ít hơn 100 kết quả → đây là trang cuối
                        if page_links_count < 100:
                            break

                        logger.info(f'[{self.name}] Nghỉ {CRAWL_DELAY}s trước trang tiếp...')
                        time.sleep(CRAWL_DELAY)

                    logger.info(f'[{self.name}] Category {category}: đã lưu {category_saved} sản phẩm.')
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

        logger.info(f'[{self.name}] Hoàn tất crawl+lưu theo từng trang.')
        return []

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
