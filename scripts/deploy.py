#!/usr/bin/env python3
"""
Kachu+ Production 部署腳本

用法：
  python scripts/deploy.py --host root@172.234.85.159

選項：
  --host              SSH 目標，例如 root@172.234.85.159（必填）
  --remote-root       伺服器上的部署目錄（預設 /opt/kachu-plus）
  --agentos-remote    伺服器上的 AgentOS 目錄（預設 /opt/AgentOS_real）
  --local-agentos     本機 AgentOS_real 路徑（預設 ../AgentOS_real）
  --services          指定只部署哪些服務，例如 kachu-plus（預設全部）
  --skip-agentos-sync 跳過 AgentOS rsync（若伺服器上已是最新版）
  --skip-ssl          跳過 SSL 憑證申請步驟
  --skip-nginx        跳過 nginx 設定更新步驟

範例：
  # 首次完整部署
  python scripts/deploy.py --host root@172.234.85.159

  # 只更新 kachu-plus 本身（不動 AgentOS、nginx、SSL）
  python scripts/deploy.py --host root@172.234.85.159 \\
      --services kachu-plus \\
      --skip-agentos-sync --skip-ssl --skip-nginx
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path

# ── 常數 ──────────────────────────────────────────────────────────────────────
DEFAULT_HOST = "root@172.234.85.159"
DEFAULT_REMOTE_ROOT = "/opt/kachu-plus"
DEFAULT_AGENTOS_REMOTE = "/opt/AgentOS_real"
DEFAULT_LOCAL_AGENTOS = str(Path(__file__).parent.parent.parent / "AgentOS_real")
KACHU_CONTAINER = "kachu-plus-kachu-plus-1"
AGENTOS_CONTAINER = "kachu-plus-agentos-1"
V2_NGINX_CONF = "/opt/kachu-v2/infra/nginx/nginx.prod.conf"
V2_NGINX_CONTAINER = "kachu-v2-gateway-1"
DOMAIN = "plus.kachu.tw"
V2_CERTBOT_CONTAINER = "kachu-v2-certbot-1"
V2_CERTBOT_WWW = "/var/www/certbot"

# ── 簡易 remote smoke test ────────────────────────────────────────────────────
KACHU_SMOKE = """\
import httpx, sys
r = httpx.get("http://localhost:8001/health", timeout=5)
assert r.status_code == 200, f"health check failed: {r.status_code}"
print("kachu-plus health OK:", r.json())
"""


# ── 工具函式 ──────────────────────────────────────────────────────────────────
def _run(cmd: list[str], *, input_text: str | None = None, check: bool = True) -> None:
    print(f"\n▶ {' '.join(shlex.quote(c) for c in cmd)}")
    result = subprocess.run(cmd, input=input_text, text=True)
    if check and result.returncode != 0:
        print(f"✗ 指令失敗（exit {result.returncode}）", file=sys.stderr)
        sys.exit(result.returncode)


def _ssh(host: str, remote_cmd: str, *, input_text: str | None = None) -> None:
    _run(["ssh", host, remote_cmd], input_text=input_text)


def _rsync(src: str, dst_host: str, dst_path: str, *, exclude: list[str] | None = None) -> None:
    cmd = [
        "rsync", "-avz", "--delete",
        "--exclude=__pycache__", "--exclude=*.pyc", "--exclude=.venv*",
        "--exclude=.env.prod", "--exclude=*.egg-info",
    ]
    for ex in (exclude or []):
        cmd += [f"--exclude={ex}"]
    cmd += [f"{src}/", f"{dst_host}:{dst_path}/"]
    _run(cmd)


def _compose_cmd(remote_root: str) -> str:
    env_file = f"{remote_root}/.env.prod"
    compose_file = f"{remote_root}/docker-compose.prod.yml"
    return f"docker compose --env-file {env_file} -f {compose_file}"


# ── 部署步驟 ──────────────────────────────────────────────────────────────────
def step_rsync_kachu_plus(host: str, remote_root: str) -> None:
    print("\n━━ [1/8] rsync Kachu+ → 伺服器 ━━")
    local_root = str(Path(__file__).parent.parent)
    _ssh(
        host,
        " ".join(
            [
                f"rm -rf {shlex.quote(remote_root)}/src/kachu",
                f"rm -f {shlex.quote(remote_root)}/alembic/versions/202604*.py",
                f"rm -f {shlex.quote(remote_root)}/alembic/versions/20260502_*.py",
                f"rm -f {shlex.quote(remote_root)}/alembic/versions/20260503_*.py",
                f"rm -f {shlex.quote(remote_root)}/alembic/versions/20260505_*.py",
            ]
        ),
    )
    _rsync(
        local_root,
        host,
        remote_root,
        exclude=[
            "tests",
            "docs",
            ".git",
            ".pytest_cache",
            ".tmp",
            "src/kachu",
            "alembic/versions/202604*.py",
            "alembic/versions/20260502_*.py",
            "alembic/versions/20260503_*.py",
            "alembic/versions/20260505_*.py",
        ],
    )
    print("✓ Kachu+ 程式碼已同步")


def step_rsync_agentos(host: str, local_agentos: str, remote_agentos: str) -> None:
    print("\n━━ [2/8] rsync AgentOS_real → 伺服器 ━━")
    local_path = Path(local_agentos)
    if not local_path.exists():
        print(f"✗ 找不到本機 AgentOS 路徑：{local_path}", file=sys.stderr)
        sys.exit(1)
    _rsync(str(local_path), host, remote_agentos, exclude=["tests", ".git", "*.db"])
    print("✓ AgentOS 程式碼已同步")


def step_build(host: str, remote_root: str, services: list[str] | None) -> None:
    print("\n━━ [3/8] docker compose build ━━")
    compose = _compose_cmd(remote_root)
    svc_str = " ".join(services) if services else ""
    _ssh(host, f"cd {remote_root} && {compose} build {svc_str}")
    print("✓ 映像檔建構完成")


def step_start(host: str, remote_root: str, services: list[str] | None) -> None:
    print("\n━━ [4/8] docker compose up -d ━━")
    compose = _compose_cmd(remote_root)
    svc_str = " ".join(services) if services else ""
    _ssh(host, f"cd {remote_root} && {compose} up -d {svc_str} && {compose} ps")
    print("✓ 容器已啟動")


def step_migrate(host: str, remote_root: str) -> None:
    print("\n━━ [5/8] alembic migrate (kachu-plus) ━━")
    compose = _compose_cmd(remote_root)
    _ssh(
        host,
        f"cd {remote_root} && {compose} exec -T kachu-plus alembic upgrade head",
    )
    print("✓ DB migration 完成")


def step_ssl(host: str) -> None:
    print(f"\n━━ [6/8] 申請 SSL 憑證：{DOMAIN} ━━")
    print(f"  前提：DNS {DOMAIN} → {host.split('@')[-1]} 已生效")
    _ssh(
        host,
        f"docker exec {V2_CERTBOT_CONTAINER} certbot certonly "
        f"--webroot --webroot-path={V2_CERTBOT_WWW} "
        f"-d {DOMAIN} --non-interactive --agree-tos "
        f"--email admin@kachu.tw 2>&1",
    )
    print(f"✓ SSL 憑證已取得：/etc/letsencrypt/live/{DOMAIN}/")


def step_nginx(host: str, remote_root: str) -> None:
    print("\n━━ [7/8] 更新 v2 nginx 設定 ━━")
    marker = f"server_name {DOMAIN};"
    check_cmd = f"grep -q '{marker}' {V2_NGINX_CONF} && echo EXISTS || echo MISSING"
    result = subprocess.run(
        ["ssh", host, check_cmd], capture_output=True, text=True
    )
    if "EXISTS" in result.stdout:
        print(f"  nginx 已有 {DOMAIN} server block，跳過")
    else:
        snippet_local = str(Path(__file__).parent.parent / "infra" / "nginx-plus.server.conf")
        snippet_content = Path(snippet_local).read_text()
        # 找到最後一個 } 之前插入
        insert_cmd = (
            f"python3 -c \""
            f"import re, pathlib; "
            f"p=pathlib.Path('{V2_NGINX_CONF}'); "
            f"txt=p.read_text(); "
            f"# 在 http {{ ... }} 的最後一個 }} 前插入 server block; "
            f"idx=txt.rfind(chr(125)); "
            f"p.write_text(txt[:idx] + open('/tmp/nginx-plus.conf').read() + chr(125))"
            f"\""
        )
        # 先上傳 snippet
        _run(["ssh", host, f"cat > /tmp/nginx-plus.conf"], input_text=snippet_content)
        # 插入到 nginx.prod.conf
        _ssh(
            host,
            f"python3 -c \""
            "import pathlib; "
            f"p=pathlib.Path('{V2_NGINX_CONF}'); "
            "txt=p.read_text(); "
            "idx=txt.rfind('}'); "
            "snippet=open('/tmp/nginx-plus.conf').read(); "
            "p.write_text(txt[:idx] + snippet + '}')"
            "\"",
        )
        # 測試 nginx 設定並 reload
        _ssh(
            host,
            f"docker exec {V2_NGINX_CONTAINER} nginx -t "
            f"&& docker exec {V2_NGINX_CONTAINER} nginx -s reload",
        )
        print("✓ nginx 設定更新完成並 reload")


def step_smoke(host: str) -> None:
    print("\n━━ [8/8] Smoke test ━━")
    _ssh(
        host,
        f"docker exec -i {KACHU_CONTAINER} python -",
        input_text=KACHU_SMOKE,
    )
    # 外部 HTTPS 驗證
    _ssh(host, f"curl -sf https://{DOMAIN}/health && echo ' ← HTTPS OK'")
    print("✓ Smoke test 通過")


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Kachu+ Production 部署腳本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(__doc__ or ""),
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="SSH host")
    parser.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
    parser.add_argument("--agentos-remote", default=DEFAULT_AGENTOS_REMOTE)
    parser.add_argument("--local-agentos", default=DEFAULT_LOCAL_AGENTOS)
    parser.add_argument("--services", nargs="*", help="指定部署的 services")
    parser.add_argument("--skip-agentos-sync", action="store_true")
    parser.add_argument("--skip-ssl", action="store_true")
    parser.add_argument("--skip-nginx", action="store_true")
    args = parser.parse_args()

    print(f"\n=== Kachu+ Production Deploy ===")
    print(f"  Host        : {args.host}")
    print(f"  Remote root : {args.remote_root}")
    print(f"  Domain      : {DOMAIN}")

    step_rsync_kachu_plus(args.host, args.remote_root)

    if not args.skip_agentos_sync:
        step_rsync_agentos(args.host, args.local_agentos, args.agentos_remote)

    step_build(args.host, args.remote_root, args.services)
    step_start(args.host, args.remote_root, args.services)
    step_migrate(args.host, args.remote_root)

    if not args.skip_ssl:
        step_ssl(args.host)

    if not args.skip_nginx:
        step_nginx(args.host, args.remote_root)

    step_smoke(args.host)

    print("\n✅ Kachu+ production deploy 完成。")
    print(f"   LINE webhook URL: https://{DOMAIN}/webhooks/line/{{tenant_id}}")
    print(f"   Admin API:        https://{DOMAIN}/admin/tenants")


if __name__ == "__main__":
    main()
