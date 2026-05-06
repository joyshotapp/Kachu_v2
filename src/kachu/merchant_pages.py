from __future__ import annotations

import json
import pathlib
import re
from typing import Any


_REQUIRED_MERCHANT_FIELDS = {
    "merchant_name",
    "category",
    "tagline",
    "phone",
    "address",
    "business_hours",
    "canonical_url",
    "services",
}

_MERCHANT_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


def normalize_merchant_slug(value: str) -> str:
    slug = str(value or "").strip().lower()
    if not slug or not _MERCHANT_SLUG_PATTERN.match(slug):
        raise ValueError("merchant_slug must contain only lowercase letters, numbers, and hyphens")
    return slug


def validate_merchant_page_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload or {})
    missing_fields = sorted(field for field in _REQUIRED_MERCHANT_FIELDS if not normalized.get(field))
    if missing_fields:
        raise ValueError(f"Merchant page data is incomplete: {', '.join(missing_fields)}")

    services = normalized.get("services")
    if not isinstance(services, list) or not services:
        raise ValueError("Merchant page data is incomplete: services")
    for service in services:
        if not isinstance(service, dict) or not str(service.get("title", "")).strip() or not str(service.get("description", "")).strip():
            raise ValueError("Merchant page service items must include title and description")

    return normalized


def build_merchant_page_template(
    merchant_slug: str,
    *,
    base_url: str,
    tenant_name: str = "",
    industry_type: str = "",
    address: str = "",
) -> dict[str, Any]:
    normalized_slug = normalize_merchant_slug(merchant_slug)
    normalized_base_url = str(base_url or "").strip().rstrip("/")
    canonical_url = f"{normalized_base_url}/merchants/{normalized_slug}" if normalized_base_url else f"/merchants/{normalized_slug}"
    brand_name = str(tenant_name or "").strip() or "示範商家"
    category = str(industry_type or "").strip() or "請填寫商家分類"
    business_address = str(address or "").strip() or "請填寫商家地址"

    payload = {
        "merchant_name": brand_name,
        "category": category,
        "tagline": f"{brand_name} 官方商家頁",
        "phone": "請填寫聯絡電話",
        "line_id": "",
        "booking_note": "歡迎透過 LINE 或電話預約。",
        "address": business_address,
        "business_hours": "請填寫營業時間",
        "service_mode": "請填寫服務方式",
        "line_url": "",
        "canonical_url": canonical_url,
        "service_tags": [category],
        "featured_points": [
            f"適合初次認識 {brand_name} 的正式介紹頁",
            "可補齊 Google 商家檔案網站欄位與審查佐證",
        ],
        "brand_intro": f"{brand_name} 提供 {category} 相關服務，這裡先放最小正式頁範本，後續可再補齊完整介紹。",
        "services": [
            {
                "title": "主要服務",
                "description": "請填寫主要服務內容與對應客群。",
            }
        ],
        "service_scenarios": ["請填寫適用情境"],
    }
    return validate_merchant_page_payload(payload)


def merchant_page_path(base_dir: pathlib.Path, merchant_slug: str) -> pathlib.Path:
    return base_dir / f"{normalize_merchant_slug(merchant_slug)}.json"


def load_merchant_page_payload(base_dir: pathlib.Path, merchant_slug: str) -> dict[str, Any]:
    merchant_path = merchant_page_path(base_dir, merchant_slug)
    if not merchant_path.exists():
        raise FileNotFoundError(merchant_slug)

    with merchant_path.open("r", encoding="utf-8") as merchant_file:
        payload = json.load(merchant_file)
    return validate_merchant_page_payload(payload)


def save_merchant_page_payload(base_dir: pathlib.Path, merchant_slug: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized_slug = normalize_merchant_slug(merchant_slug)
    normalized_payload = validate_merchant_page_payload(payload)
    base_dir.mkdir(parents=True, exist_ok=True)
    merchant_path = merchant_page_path(base_dir, normalized_slug)
    with merchant_path.open("w", encoding="utf-8") as merchant_file:
        json.dump(normalized_payload, merchant_file, ensure_ascii=False, indent=2)
        merchant_file.write("\n")
    return normalized_payload