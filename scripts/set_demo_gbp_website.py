#!/usr/bin/env python3
"""
set_demo_gbp_website.py
=======================
用途：將示範 tenant（或指定 tenant）的 GBP location website 欄位
     更新為該 tenant 在 Kachu 上的正式商家頁 URL。

執行前提：
  - tenant 已完成 Google OAuth 授權（production DB 中有有效 refresh_token）
  - 執行環境能連到 production DB（或直接在 production 容器內執行）

使用方式：

  # 使用預設示範 tenant（demo-sishixunyangtang），只做 dry run（只讀，不寫）
  python scripts/set_demo_gbp_website.py --dry-run

  # 實際更新示範 tenant 的 GBP website 欄位
  python scripts/set_demo_gbp_website.py

  # 指定其他 tenant
  python scripts/set_demo_gbp_website.py --tenant-id <tenant_id>

  # 在 production 容器內執行
  docker exec -i kachu-v2-kachu-1 python - < scripts/set_demo_gbp_website.py

GBP API 參考：
  https://developers.google.com/my-business/reference/businessinformation/rest/v1/locations/patch
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# ── 路徑設定（本機直接跑時）────────────────────────────────────────────────
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, os.path.join(_repo_root, "src"))

from kachu.config import get_settings
from kachu.google.business_client import GoogleBusinessClient
from kachu.persistence import KachuRepository, create_db_engine
from kachu.tenant_runtime import refresh_google_token


KACHU_MERCHANT_BASE = "https://kachu.tw/merchants"
DEFAULT_TENANT_ID = "demo-sishixunyangtang"


def _build_merchant_url(tenant_id: str, merchant_slug: str | None) -> str:
    slug = merchant_slug or tenant_id
    return f"{KACHU_MERCHANT_BASE}/{slug}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Update GBP location website URI for a Kachu tenant.")
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID, help="Kachu tenant_id")
    parser.add_argument("--dry-run", action="store_true", help="Only read; do not write to GBP")
    args = parser.parse_args()

    settings = get_settings()
    repo = KachuRepository(create_db_engine(settings.DATABASE_URL))

    # ── 1. 讀取 tenant ──────────────────────────────────────────────────────
    tenant = repo.get_or_create_tenant(args.tenant_id)
    merchant_slug = getattr(tenant, "merchant_slug", None) or args.tenant_id
    merchant_url = _build_merchant_url(args.tenant_id, merchant_slug)
    print(f"tenant_id      : {args.tenant_id}")
    print(f"merchant_slug  : {merchant_slug}")
    print(f"target website : {merchant_url}")

    # ── 2. 取得 GBP OAuth token ─────────────────────────────────────────────
    connector = repo.get_connector_account(args.tenant_id, "google_business")
    if connector is None:
        print(
            "\n[ERROR] 此 tenant 沒有 google_business connector。\n"
            "請先在 Kachu Dashboard 完成 Google OAuth 授權，再執行此腳本。"
        )
        sys.exit(1)

    creds_raw = connector.credentials if isinstance(connector.credentials, dict) else json.loads(connector.credentials or "{}")
    account_id = creds_raw.get("google_business_account_id", "")
    location_id = creds_raw.get("google_business_location_id", "")

    if not account_id or not location_id:
        print(
            "\n[ERROR] connector 缺少 account_id 或 location_id。\n"
            "請確認已完成 GBP location 選擇步驟。"
        )
        sys.exit(1)

    print(f"account_id     : {account_id}")
    print(f"location_id    : {location_id}")

    # 嘗試 token refresh
    try:
        access_token = refresh_google_token(repo, args.tenant_id, settings)
    except Exception as exc:
        print(f"\n[ERROR] Token refresh 失敗：{exc}")
        sys.exit(1)

    client = GoogleBusinessClient(access_token=access_token)

    # ── 3. 讀取目前 GBP website 欄位 ────────────────────────────────────────
    print("\n── 目前 GBP location 狀態 ─────────────────────────────────────")
    try:
        location_data = client.get_location(account_id, location_id, read_mask="name,title,websiteUri")
        current_website = location_data.get("websiteUri", "(空白)")
        print(f"title          : {location_data.get('title', '?')}")
        print(f"websiteUri     : {current_website}")
    except Exception as exc:
        print(f"[WARN] 無法讀取 location 資訊：{exc}")
        current_website = "(unknown)"

    if current_website == merchant_url:
        print(f"\n[OK] GBP website 已是正確值：{merchant_url}")
        return

    # ── 4. 更新 ──────────────────────────────────────────────────────────────
    if args.dry_run:
        print(f"\n[DRY RUN] 若執行將把 websiteUri 從\n  {current_website}\n更新為\n  {merchant_url}")
        print("傳入 --dry-run=False 或移除 --dry-run 旗標以實際寫入。")
        return

    print(f"\n更新 websiteUri → {merchant_url} ...")
    try:
        result = client.patch_location_website(account_id, location_id, merchant_url)
        updated = result.get("websiteUri", "?")
        if updated == merchant_url:
            print(f"[OK] 更新成功：websiteUri = {updated}")
        else:
            print(f"[WARN] API 回應 websiteUri = {updated}（與預期不符，請手動確認）")
            print(f"       完整回應：{json.dumps(result, ensure_ascii=False, indent=2)}")
    except Exception as exc:
        print(f"[ERROR] 更新失敗：{exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
