#!/usr/bin/env python3
"""重置 xianyu-super-butler 的 admin 登录密码。

适用场景：
- 新镜像启动后，admin 登录提示"用户名或密码错误"；
- 根因是容器重建但宿主机 ./data/xianyu_data.db（卷挂载）被保留，
  admin 用户在首次建库时已经创建，密码沿用的是旧 ADMIN_PASSWORD，
  而不是当前镜像/compose 里的默认值。

用法：
    python scripts/reset_admin.py --password admin123
    python scripts/reset_admin.py --username admin --password NewPass123
    python scripts/reset_admin.py --db /app/data/xianyu_data.db --password admin123

说明：
- 只改 admin 用户的 password_hash，不影响 cookies / 商品 / 订单等其它数据。
- 密码以 sha256 存储，与 db_manager.verify_user_password 的校验方式一致。
"""
import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

# 与 docker-compose 默认 DB_PATH 对齐；宿主 ./data 挂载到容器 /app/data。
DEFAULT_DB_CANDIDATES = [
    Path("/app/data/xianyu_data.db"),
    Path("./data/xianyu_data.db"),
    Path("data/xianyu_data.db"),
]


def resolve_db_path(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            print(f"[错误] 指定的数据库文件不存在: {p}", file=sys.stderr)
            sys.exit(1)
        return p
    for cand in DEFAULT_DB_CANDIDATES:
        if cand.exists():
            return cand
    print("[错误] 未找到数据库文件，请用 --db 显式指定路径。", file=sys.stderr)
    print("        搜索过: " + ", ".join(str(c) for c in DEFAULT_DB_CANDIDATES), file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="重置 admin 登录密码")
    parser.add_argument("--db", default=None, help="SQLite 数据库文件路径")
    parser.add_argument("--username", default="admin", help="要重置的用户名，默认 admin")
    parser.add_argument("--password", required=True, help="新的登录密码")
    args = parser.parse_args()

    db_path = resolve_db_path(args.db)
    new_hash = hashlib.sha256(args.password.encode("utf-8")).hexdigest()

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, username, email, is_active FROM users WHERE username = ?",
            (args.username,),
        )
        row = cur.fetchone()
        if not row:
            print(f"[错误] 用户 {args.username} 不存在，无法重置。", file=sys.stderr)
            sys.exit(1)

        cur.execute(
            "UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE username = ?",
            (new_hash, args.username),
        )
        conn.commit()
        print(f"[成功] 用户 '{args.username}' 的密码已重置。")
        print(f"        数据库: {db_path}")
        print(f"        新密码: {args.password}")
        print(f"        请直接重新登录，无需重启服务。")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
