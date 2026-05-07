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


def save_product(conn, product_data: dict):
    """
    Lưu hoặc cập nhật sản phẩm vào database.
    Nếu slug đã tồn tại, cập nhật thông tin mới.
    """
    # Generate embedding text
    text_to_embed = f"{product_data.get('category', '')} {product_data.get('brand', '')} {product_data.get('name', '')} {json.dumps(product_data.get('specs', {}))} {product_data.get('description', '')}"
    
    try:
        embedding_vector = embedder.generate_embedding(text_to_embed)
        embedding_value = str(embedding_vector)
    except Exception as e:
        print(f"Error generating embedding for {product_data.get('slug', 'unknown')}: {e}")
        embedding_value = None

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO products (name, slug, category, brand, specs, image_url, description, base_price, embedding)
            VALUES (%(name)s, %(slug)s, %(category)s, %(brand)s, %(specs)s, %(image_url)s, %(description)s, %(base_price)s, %(embedding)s)
            ON CONFLICT (slug) DO UPDATE SET
                name = EXCLUDED.name,
                specs = EXCLUDED.specs,
                image_url = EXCLUDED.image_url,
                description = EXCLUDED.description,
                base_price = EXCLUDED.base_price,
                embedding = COALESCE(EXCLUDED.embedding, products.embedding),
                updated_at = NOW()
            RETURNING id
            """,
            {
                **product_data,
                "specs": Json(product_data.get("specs", {})),
                "embedding": embedding_value,
            },
        )
        product_id = cur.fetchone()["id"]
        conn.commit()
        return product_id


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


def save_products_batch(conn, products_data: list):
    """
    Lưu hoặc cập nhật một batch sản phẩm vào database.
    """
    if not products_data:
        return []

    ids = []
    with conn.cursor() as cur:
        for product_data in products_data:
            cur.execute(
                """
                INSERT INTO products (name, slug, category, brand, specs, image_url, description, base_price)
                VALUES (%(name)s, %(slug)s, %(category)s, %(brand)s, %(specs)s, %(image_url)s, %(description)s, %(base_price)s)
                ON CONFLICT (slug) DO UPDATE SET
                    name = EXCLUDED.name,
                    specs = EXCLUDED.specs,
                    image_url = EXCLUDED.image_url,
                    description = EXCLUDED.description,
                    base_price = EXCLUDED.base_price,
                    updated_at = NOW()
                RETURNING id
                """,
                {
                    **product_data,
                    "specs": Json(product_data.get("specs", {})),
                },
            )
            res = cur.fetchone()
            if res:
                ids.append(res["id"])
        conn.commit()
    return ids
