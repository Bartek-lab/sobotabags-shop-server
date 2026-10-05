from app.config import settings

BUCKET = "product-images"


def public_url(storage_key: str) -> str:
    return f"{settings.supabase_url}/storage/v1/object/public/{BUCKET}/{storage_key}"
