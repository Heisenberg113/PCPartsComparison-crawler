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
import os
import tempfile
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from .base import BaseSpider

logger = logging.getLogger(__name__)

CRAWL_DELAY = 10
NUM_WORKERS = 3


class PCPartPickerSpider(BaseSpider):
    name = "pcpartpicker"
    shop_name = "PCPartPicker"
    base_url = "https://pcpartpicker.com"

    min_delay = 10.0
    max_delay = 15.0

    category_urls = {
        "cpu": "/products/cpu/specs/",
        "gpu": "/products/video-card/specs/",
        "ram": "/products/memory/specs/",
        "harddrive": "/products/internal-hard-drive/specs/",
        "mainboard": "/products/motherboard/specs/",
        "psu": "/products/power-supply/specs/",
        "case": "/products/case/specs/",
        # "cooler": "/products/cpu-cooler/specs/",
        # "monitor": "/products/monitor/specs/",
        # "case-fan": "/products/case-fan/specs/",
        # "keyboard": "/products/keyboard/specs/",
        # "mouse": "/products/mouse/specs/",
        # "headphones": "/products/headphones/specs/",
        # "speakers": "/products/speakers/specs/",
        # "external-hard-drive": "/products/external-hard-drive/specs/",
    }

    # ──────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────

    def _create_browser(self, user_data_dir: str = None):
        """Tạo một instance DrissionPage mới."""
        from DrissionPage import ChromiumPage, ChromiumOptions
        co = ChromiumOptions()
        co.auto_port()
        co.set_argument('--disable-images')
        co.set_argument('--disable-extensions')
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-gpu')
        if user_data_dir:
            co.set_argument(f'--user-data-dir={user_data_dir}')
        return ChromiumPage(co)

    def _copy_profile(self, src_dir: str, worker_idx: int) -> str:
        """Copy profile của browser chính sang profile riêng cho worker."""
        worker_dir = tempfile.mkdtemp(prefix=f"pcpp_worker_{worker_idx}_")
        for item in ['Default', 'Local State']:
            src = os.path.join(src_dir, item)
            dst = os.path.join(worker_dir, item)
            if os.path.exists(src):
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
        return worker_dir

    def _parse_specs(self, dp_page) -> dict:
        """Parse toàn bộ specs từ trang detail sản phẩm."""
        specs = {}

        # Lấy tất cả groups, sau đó chỉ giữ nhóm thuộc container đầu tiên
        # bằng cách dùng selector scoped
        spec_groups = dp_page.eles('css:.block.xs-hide.md-block.specs .group.group--spec')

        if not spec_groups:
            spec_groups = dp_page.eles('css:.block.xs-block.md-hide.specs .group.group--spec')

        if not spec_groups:
            logger.warning("Không tìm thấy spec groups")
            return specs

        for group in spec_groups:
            try:
                title_ele = group.ele('css:h3.group__title')
                if not title_ele:
                    continue
                group_title = title_ele.text.strip()
                if not group_title:
                    continue

                content_div = group.ele('css:div.group__content')
                if not content_div:
                    continue

                # Dạng 1: ul > li — nhiều giá trị
                li_eles = content_div.eles('css:ul li')
                if li_eles:
                    values = [
                        li.text.strip() for li in li_eles
                        if li.text.strip() and li.text.strip() not in ["—", "-"]
                    ]
                    if values:
                        specs[group_title] = values[0] if len(values) == 1 else values
                    continue

                # Dạng 2: p tag — single value
                p_ele = content_div.ele('css:p')
                if p_ele:
                    val = p_ele.text.strip()
                    if val and val not in ["—", "-", ""]:
                        specs[group_title] = val
                    continue

                # Dạng 3: fallback — text trực tiếp
                val = content_div.text.strip()
                if val and val not in ["—", "-", ""]:
                    specs[group_title] = val

            except Exception as e:
                logger.error(f"Lỗi parse group: {e}")
                continue

        return specs

    # ──────────────────────────────────────────────
    # WORKER (chạy trong thread riêng)
    # ──────────────────────────────────────────────

    def _worker(self, worker_id: int, items: list, category: str,
                results: list, user_data_dir: str):
        logger.info(f"[Worker-{worker_id}] Khởi động, xử lý {len(items)} items...")

        dp_page = self._create_browser(user_data_dir=user_data_dir)

        # Stagger khởi động để tránh conflict
        time.sleep(worker_id * 2)

        try:
            for item in items:
                name = item["name"]
                detail_url = item["detail_url"]
                try:
                    logger.info(f"[Worker-{worker_id}] {name[:50]} | {detail_url[:60]}...")
                    dp_page.get(detail_url, timeout=30, retry=1)
                    time.sleep(1.5)

                    # Kiểm tra bị Cloudflare chặn
                    current_url = dp_page.url or ""
                    body_text = ""
                    try:
                        body = dp_page.ele('css:body')
                        body_text = body.text[:200] if body else ""
                    except Exception:
                        pass

                    if ("challenges.cloudflare" in current_url
                            or "Performing security verification" in body_text):
                        logger.warning(f"[Worker-{worker_id}] Cloudflare block! Đợi 30s...")
                        time.sleep(30)
                        dp_page.get(detail_url, timeout=30, retry=1)
                        time.sleep(3)

                    specs = self._parse_specs(dp_page)

                    slug = self._make_slug(name)
                    brand = self._extract_brand(name)

                    results.append({
                        "name": name,
                        "slug": slug,
                        "category": category,
                        "brand": brand,
                        "specs": specs,
                        "image_url": "",
                        "description": "Imported from PCPartPicker",
                        "base_price": 0,
                        "shop_url": detail_url,
                    })

                    logger.info(f"[Worker-{worker_id}] OK: {name[:40]} | {len(specs)} specs")
                    time.sleep(1)

                except Exception as e:
                    logger.error(f"[Worker-{worker_id}] Lỗi {detail_url}: {e}")
                    time.sleep(3)
                    continue
        finally:
            logger.info(f"[Worker-{worker_id}] Xong, đóng browser.")
            dp_page.quit()

    # ──────────────────────────────────────────────
    # MAIN CRAWL
    # ──────────────────────────────────────────────

    async def crawl_products(self, page=None) -> list[dict]:
        all_products = []

        from DrissionPage import ChromiumPage, ChromiumOptions

        user_data_dir = tempfile.mkdtemp(prefix="pcpp_crawl_")
        logger.info(f"[{self.name}] Shared user data dir: {user_data_dir}")

        co = ChromiumOptions()
        co.auto_port()
        co.set_argument(f'--user-data-dir={user_data_dir}')
        main_browser = ChromiumPage(co)

        try:
            for category, url_path in self.category_urls.items():
                try:
                    logger.info(f"[{self.name}] ── Category: {category} ──")
                    url = f"{self.base_url}{url_path}"

                    # ── BƯỚC 1: Thu thập links ──────────────────────────────
                    detail_urls = []

                    for page_num in range(1, 2):
                        page_url = f"{url}#page={page_num}"
                        logger.info(f"[{self.name}] Trang {page_num}: {page_url}")
                        main_browser.get(page_url)

                        if page_num == 1:
                            logger.info(f"[{self.name}] Đợi Cloudflare...")
                            table = main_browser.ele('css:table', timeout=60)
                            time.sleep(2)  # để cookie ghi vào disk
                            logger.info(f"[{self.name}] Cloudflare passed, cookie đã lưu.")
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
                            (i for i, h in enumerate(headers) if h == "Name"), -1
                        )
                        if name_idx == -1:
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
                            link = name_link.link
                            if name and link:
                                detail_urls.append({"name": name, "detail_url": link})
                                page_links_count += 1

                        logger.info(f"[{self.name}] Trang {page_num}: {page_links_count} links")
                        time.sleep(2)

                        if page_links_count < 100:
                            break

                    if not detail_urls:
                        logger.warning(f"[{self.name}] Không có links cho {category}, bỏ qua.")
                        continue

                    logger.info(f"[{self.name}] Tổng {len(detail_urls)} links → chia cho {NUM_WORKERS} workers")

                    # ── BƯỚC 2: Copy profile cho workers ────────────────────
                    worker_profile_dirs = []
                    for i in range(NUM_WORKERS):
                        worker_dir = self._copy_profile(user_data_dir, i)
                        worker_profile_dirs.append(worker_dir)
                        logger.info(f"[{self.name}] Worker-{i} profile: {worker_dir}")

                    # ── BƯỚC 3: Chia chunks và spawn workers ────────────────
                    chunk_size = max(1, len(detail_urls) // NUM_WORKERS)
                    chunks = [
                        detail_urls[i:i + chunk_size]
                        for i in range(0, len(detail_urls), chunk_size)
                    ]
                    # Đảm bảo số chunks == NUM_WORKERS (chunk cuối có thể dư)
                    while len(chunks) < NUM_WORKERS:
                        chunks.append([])

                    worker_results = [[] for _ in range(NUM_WORKERS)]

                    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
                        futures = {
                            executor.submit(
                                self._worker,
                                i, chunks[i], category,
                                worker_results[i], worker_profile_dirs[i]
                            ): i
                            for i in range(NUM_WORKERS)
                            if chunks[i]  # bỏ qua worker không có việc
                        }
                        for future in as_completed(futures):
                            worker_id = futures[future]
                            try:
                                future.result()
                                logger.info(f"[{self.name}] Worker-{worker_id} hoàn thành.")
                            except Exception as e:
                                logger.error(f"[{self.name}] Worker-{worker_id} lỗi: {e}")

                    # Dọn worker profiles
                    for d in worker_profile_dirs:
                        shutil.rmtree(d, ignore_errors=True)

                    # Gộp kết quả
                    for wr in worker_results:
                        all_products.extend(wr)

                    cat_count = sum(len(r) for r in worker_results)
                    logger.info(f"[{self.name}] Category {category}: {cat_count} sản phẩm.")
                    logger.info(f"[{self.name}] Nghỉ {CRAWL_DELAY}s trước category tiếp theo...")
                    time.sleep(CRAWL_DELAY)

                except Exception as e:
                    logger.error(f"[{self.name}] Lỗi category {category}: {e}", exc_info=True)
                    time.sleep(CRAWL_DELAY)
                    continue

        finally:
            logger.info(f"[{self.name}] Đóng browser chính.")
            main_browser.quit()
            shutil.rmtree(user_data_dir, ignore_errors=True)

        logger.info(f"[{self.name}] Hoàn tất! Tổng: {len(all_products)} sản phẩm")
        return all_products

    async def crawl_prices(self, page, product_slug: str) -> dict | None:
        return None

    def _make_slug(self, name: str) -> str:
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