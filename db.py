"""
Database connection helper for the PC Parts Crawler.
Cung cấp kết nối PostgreSQL và các helper functions.
"""

import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from dotenv import load_dotenv
from embedding.google_embeddings_model import GoogleEmbeddingModelMethod

load_dotenv()

embedder = GoogleEmbeddingModelMethod()

BATCH_SIZE = 50

_INSERT_SQL = """
    INSERT INTO products (
        name, slug, category, brand, specs, image_url,
        description, base_price, embedding, ext_rating, ext_review_count
    )
    VALUES (
        %(name)s, %(slug)s, %(category)s, %(brand)s, %(specs)s, %(image_url)s,
        %(description)s, %(base_price)s, %(embedding)s, %(ext_rating)s, %(ext_review_count)s
    )
    ON CONFLICT (slug) DO UPDATE SET
        name             = EXCLUDED.name,
        specs            = EXCLUDED.specs,
        image_url        = EXCLUDED.image_url,
        description      = EXCLUDED.description,
        base_price       = EXCLUDED.base_price,
        embedding        = COALESCE(EXCLUDED.embedding, products.embedding),
        ext_rating       = COALESCE(EXCLUDED.ext_rating, products.ext_rating),
        ext_review_count = COALESCE(EXCLUDED.ext_review_count, products.ext_review_count),
        updated_at       = NOW()
    RETURNING id
"""


def _build_params(product_data: dict) -> dict:
    text_to_embed = (
        f"{product_data.get('category', '')} "
        f"{product_data.get('brand', '')} "
        f"{product_data.get('name', '')} "
        f"{json.dumps(product_data.get('specs', {}))} "
        f"{product_data.get('description', '')}"
    )
    try:
        embedding_value = str(embedder.generate_embedding(text_to_embed))
    except Exception as e:
        print(f"Embedding error [{product_data.get('slug', '?')}]: {e}")
        embedding_value = None

    return {
        **product_data,
        "specs": Json(product_data.get("specs", {})),
        "embedding": embedding_value,
        "ext_rating": product_data.get("rating_avg"),
        "ext_review_count": product_data.get("rating_count"),
    }


def get_connection():
    """Tạo kết nối tới PostgreSQL database."""
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_DATABASE", "pcparts"),
        user=os.getenv("DB_USERNAME", "pcparts_user"),
        password=os.getenv("DB_PASSWORD", "pcparts_secret_2024"),
        cursor_factory=RealDictCursor,
    )


def save_product(conn, product_data: dict) -> int:
    """Lưu hoặc cập nhật một sản phẩm, commit ngay."""
    with conn.cursor() as cur:
        cur.execute(_INSERT_SQL, _build_params(product_data))
        product_id = cur.fetchone()["id"]
    conn.commit()
    return product_id


def save_products_batch(conn, products_data: list) -> list[int]:
    """
    Lưu một batch sản phẩm trong một transaction duy nhất.
    Trả về danh sách product_id theo đúng thứ tự input.
    """
    if not products_data:
        return []

    ids = []
    with conn.cursor() as cur:
        for product_data in products_data:
            cur.execute(_INSERT_SQL, _build_params(product_data))
            res = cur.fetchone()
            ids.append(res["id"] if res else None)

    conn.commit()
    return ids


def save_price(conn, price_data: dict):
    """Lưu giá mới vào bảng prices (append-only)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO prices (product_id, shop_name, price, url, in_stock)
            VALUES (%(product_id)s, %(shop_name)s, %(price)s, %(url)s, %(in_stock)s)
            """,
            price_data,
        )
    conn.commit()


def find_product_by_slug(conn, slug: str):
    """Tìm sản phẩm theo slug."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM products WHERE slug = %s", (slug,))
        return cur.fetchone()
