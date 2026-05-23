import os
import json
import logging
import time
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from dotenv import load_dotenv
from embedding.google_embeddings_model import GoogleEmbeddingModelMethod

load_dotenv()

logger = logging.getLogger(__name__)
embedder = GoogleEmbeddingModelMethod()

_EMBED_DELAY = 0.65       # ~92 req/phút — dưới giới hạn 100/phút của free tier
_daily_quota_exhausted = True  # True khi hết quota ngày — bỏ qua toàn bộ embedding còn lại

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


def _make_embed_text(product_data: dict) -> str:
    return (
        f"{product_data.get('category', '')} "
        f"{product_data.get('brand', '')} "
        f"{product_data.get('name', '')} "
        f"{json.dumps(product_data.get('specs', {}))} "
        f"{product_data.get('description', '')}"
    )


def _build_params(product_data: dict, embedding_vec=None) -> dict:
    return {
        **product_data,
        "specs": Json(product_data.get("specs", {})),
        "embedding": str(embedding_vec) if embedding_vec is not None else None,
        "ext_rating": product_data.get("rating_avg"),
        "ext_review_count": product_data.get("rating_count"),
    }


def get_connection():
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_DATABASE", "pcparts"),
        user=os.getenv("DB_USERNAME", "pcparts_user"),
        password=os.getenv("DB_PASSWORD", "pcparts_secret_2024"),
        cursor_factory=RealDictCursor,
    )


def save_products_batch(conn, products_data: list) -> list[int]:
    """Embed từng sản phẩm với delay rồi lưu cả batch trong một transaction."""
    global _daily_quota_exhausted

    if not products_data:
        return []

    embedding_vectors = []
    for i, p in enumerate(products_data):
        if _daily_quota_exhausted:
            embedding_vectors.append(None)
            continue

        if i > 0:
            time.sleep(_EMBED_DELAY)

        try:
            vec = embedder.generate_embedding(_make_embed_text(p))
        except Exception as e:
            err_str = str(e)
            if 'PerDay' in err_str or 'per_day' in err_str.lower():
                logger.error("Hết quota embedding theo ngày. Bỏ qua embedding cho phần còn lại.")
                _daily_quota_exhausted = True
            else:
                logger.warning(f"Embedding error [{p.get('slug', '?')}]: {e}")
            vec = None
        embedding_vectors.append(vec)

    ok_count = sum(1 for v in embedding_vectors if v is not None)
    logger.info(f"Embeddings: {ok_count}/{len(products_data)} OK")

    ids = [None] * len(products_data)
    pairs = list(zip(products_data, embedding_vectors))

    # Thử lưu cả batch trong một transaction
    try:
        with conn.cursor() as cur:
            for idx, (product_data, emb_vec) in enumerate(pairs):
                cur.execute(_INSERT_SQL, _build_params(product_data, emb_vec))
                res = cur.fetchone()
                ids[idx] = res["id"] if res else None
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning(f"Batch save thất bại ({e}), thử lưu từng sản phẩm...")
        for idx, (product_data, emb_vec) in enumerate(pairs):
            try:
                with conn.cursor() as cur:
                    cur.execute(_INSERT_SQL, _build_params(product_data, emb_vec))
                    res = cur.fetchone()
                    ids[idx] = res["id"] if res else None
                conn.commit()
            except Exception as e2:
                conn.rollback()
                logger.error(f"Lỗi lưu [{product_data.get('slug', '?')}]: {e2}")

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
