import sys
import os
import asyncio
import logging
from playwright.async_api import async_playwright
from dotenv import load_dotenv

from db import get_connection, save_product
from spiders.techpowerup import TechPowerUpSpider, RateLimitError

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("techpowerup_runner")

def get_proxy_config():
    """Lấy cấu hình proxy từ .env (nếu có)"""
    host = os.getenv("PROXY_HOST")
    port = os.getenv("PROXY_PORT")
    user = os.getenv("PROXY_USER")
    pw   = os.getenv("PROXY_PASS")
    if host and port:
        proxy = {"server": f"http://{host}:{port}"}
        if user:
            proxy["username"] = user
        if pw:
            proxy["password"] = pw
        return proxy
    return None

RATE_LIMIT_WAIT = 30  # Số giây đợi khi bị rate limit để proxy rotate IP
MAX_RETRIES = 3       # Số lần retry tối đa cho mỗi URL bị rate limit

async def create_browser_and_page(p, spider, proxy_config):
    """Tạo browser + context + page mới (để lấy IP proxy mới)"""
    launch_opts = {
        "channel": "msedge",
        "headless": False,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
    }
    if proxy_config:
        launch_opts["proxy"] = proxy_config

    browser = await p.chromium.launch(**launch_opts)
    context = await browser.new_context(
        user_agent=spider.get_random_user_agent(),
        viewport={"width": 1920, "height": 1080},
        java_script_enabled=True,
        locale="en-US",
    )
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
    """)
    page = await context.new_page()
    return browser, context, page

async def _check_rate_limit(page, response, url: str):
    """Kiểm tra rate limit từ response status và page title. Raise RateLimitError nếu phát hiện."""
    if response and response.status == 429:
        raise RateLimitError(f"HTTP 429 tại {url}")
    page_title = await page.title()
    if "too many" in page_title.lower() or "rate limit" in page_title.lower():
        raise RateLimitError(f"Rate limit detected (title: {page_title}) tại {url}")

async def get_gpu_links(p, page, browser, spider, proxy_config) -> tuple[list[str], object, object]:
    """Lấy danh sách GPU links. Trả về (links, browser, page) vì browser có thể được tạo lại khi bị rate limit."""
    base_url = f"https://www.techpowerup.com/gpu-specs/?f=generation"

    # --- Lấy danh sách generation (có rate limit detection) ---
    retries = 0
    generation_url = None
    while retries <= MAX_RETRIES:
        try:
            response = await page.goto(base_url, wait_until="domcontentloaded", timeout=20000)
            if response and response.status == 404:
                logger.warning(f"Không truy cập được vào {base_url}")
                return [], browser, page

            await _check_rate_limit(page, response, base_url)

            await page.wait_for_selector('select.filter-options-dropdown')
            generation_url = await page.evaluate("""
                () => {
                    const options = document.querySelectorAll('select.filter-options-dropdown option');
                    return Array.from(options, option => option.value.split(" ").slice(0, -1).join(" ")).filter(val => val !== '');
                }
            """)
            break  # Thành công, thoát vòng lặp
        except RateLimitError as rle:
            retries += 1
            logger.warning(f"⏳ Rate limit khi lấy danh sách generation: {rle}")
            if retries > MAX_RETRIES:
                logger.error(f"❌ Bỏ qua lấy danh sách generation sau {MAX_RETRIES} lần retry")
                return [], browser, page
            logger.info(f"🔄 Đợi {RATE_LIMIT_WAIT}s để proxy rotate IP... (retry {retries}/{MAX_RETRIES})")
            try:
                await browser.close()
            except Exception:
                pass
            await asyncio.sleep(RATE_LIMIT_WAIT)
            browser, _, page = await create_browser_and_page(p, spider, proxy_config)
            logger.info(f"🌐 Đã tạo kết nối mới, tiếp tục lấy generation...")

    if not generation_url:
        return [], browser, page

    # --- Lấy link GPU từ từng generation (có rate limit detection) ---
    all_links = []

    for generation in generation_url[1:]:
        retries = 0
        success = False

        while retries <= MAX_RETRIES and not success:
            try:
                gen_url = f"{base_url}_{generation}"
                response = await page.goto(gen_url, wait_until="domcontentloaded", timeout=20000)
                await _check_rate_limit(page, response, gen_url)

                logger.info(f"Đang lấy link từ {generation}")
                links = await page.evaluate("""
                    () => {
                        const anchors = document.querySelectorAll('div.item-name a');
                        return Array.from(anchors, a => a.href).filter(h => h.includes('/gpu-specs/') && !h.includes('?f='));
                    }
                """)
                links = list(set(links))
                all_links.extend(links)
                logger.info(f"Đã lấy được {len(all_links)} link từ {generation}")
                success = True

            except RateLimitError as rle:
                retries += 1
                logger.warning(f"⏳ Rate limit khi lấy link generation '{generation}': {rle}")
                if retries > MAX_RETRIES:
                    logger.error(f"❌ Bỏ qua generation '{generation}' sau {MAX_RETRIES} lần retry")
                    break
                logger.info(f"🔄 Đợi {RATE_LIMIT_WAIT}s để proxy rotate IP... (retry {retries}/{MAX_RETRIES})")
                try:
                    await browser.close()
                except Exception:
                    pass
                await asyncio.sleep(RATE_LIMIT_WAIT)
                browser, _, page = await create_browser_and_page(p, spider, proxy_config)
                logger.info(f"🌐 Đã tạo kết nối mới, tiếp tục lấy link...")

            except Exception as e:
                logger.error(f"❌ Lỗi khi lấy link từ generation '{generation}': {e}")
                success = True  # Bỏ qua, không retry lỗi khác

        spider.random_delay()

    return list(set(all_links)), browser, page


async def run():
    proxy_config = get_proxy_config()
    if proxy_config:
        logger.info(f"🌐 Proxy enabled: {proxy_config['server']}")
    else:
        logger.info("⚠️ No proxy configured, using direct connection")

    logger.info(f"🚀 Starting TechPowerUp Crawler")
    spider = TechPowerUpSpider()
    conn = get_connection()
    try:
        async with async_playwright() as p:
            browser, context, page = await create_browser_and_page(p, spider, proxy_config)

            gpu_links, browser, page = await get_gpu_links(p, page, browser, spider, proxy_config)
            logger.info(f"📋 Tổng cộng {len(gpu_links)} GPU links cần crawl")

            for idx, gpu_link in enumerate(gpu_links, 1):
                retries = 0
                success = False

                while retries <= MAX_RETRIES and not success:
                    try:
                        product = await spider.get_details(page, gpu_link)

                        if product:
                            try:
                                p_id = save_product(conn, product)
                                logger.info(f"✅ [{idx}/{len(gpu_links)}] Saved GPU: {product['name']} (ID: {p_id})")
                            except Exception as db_err:
                                logger.error(f"❌ DB Error for {product['slug']}: {db_err}")
                                conn.rollback()
                        success = True

                    except RateLimitError as rle:
                        retries += 1
                        logger.warning(f"⏳ [{idx}/{len(gpu_links)}] Rate limit detected: {rle}")
                        if retries > MAX_RETRIES:
                            logger.error(f"❌ Bỏ qua {gpu_link} sau {MAX_RETRIES} lần retry")
                            break

                        logger.info(f"🔄 Đợi {RATE_LIMIT_WAIT}s để proxy rotate IP... (retry {retries}/{MAX_RETRIES})")
                        # Đóng browser cũ để giải phóng kết nối proxy
                        try:
                            await browser.close()
                        except Exception:
                            pass
                        await asyncio.sleep(RATE_LIMIT_WAIT)

                        # Tạo browser mới → kết nối proxy mới → IP mới
                        browser, context, page = await create_browser_and_page(p, spider, proxy_config)
                        logger.info(f"🌐 Đã tạo kết nối mới, tiếp tục crawl...")

                    except Exception as crawl_err:
                        logger.error(f"❌ Crawl Error for {gpu_link}: {crawl_err}")
                        success = True  # Không retry lỗi khác, bỏ qua

                # Tránh gọi request dồn dập
                spider.random_delay()

            await browser.close()

    except Exception as e:
        logger.error(f"🔥 Critical crawler error: {e}")
    finally:
        conn.close()
        logger.info("🛑 Crawler finished")

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("🛑 Stopped by user (Ctrl+C)")
