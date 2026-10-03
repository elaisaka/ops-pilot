"""Local demo bearer identities. No default credential and no client-selected role."""
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from fastapi import Header, HTTPException

ROLES = ("operator", "supervisor", "auditor")


def create_demo_credentials(directory: Path):
    """Explicit setup only; return private tokens for tests/local credential file."""
    directory.mkdir(parents=True, exist_ok=True)
    tokens = {role: secrets.token_urlsafe(32) for role in ROLES}
    identities = [{"name": "demo-" + role, "role": role,
                   "digest": hashlib.sha256(token.encode()).hexdigest()}
                  for role, token in tokens.items()]
    path = directory / "demo-auth.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(identities, stream)
    path.chmod(0o600)
    return path, tokens


def require_roles(*roles):
    def authenticate(authorization: str | None = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "请使用本地演示凭据登录", headers={"WWW-Authenticate": "Bearer"})
        token = authorization[7:]
        if not 20 <= len(token) <= 256:
            raise HTTPException(401, "凭据无效")
        try:
            rows = json.loads(Path(os.getenv("DEMO_AUTH_FILE", "data/demo-auth.json")).read_text(encoding="utf-8"))
            digest = hashlib.sha256(token.encode()).hexdigest()
            identity = next((row for row in rows if hmac.compare_digest(row["digest"], digest)), None)
            if identity and (identity["role"] not in ROLES or identity["name"] != "demo-" + identity["role"]):
                raise ValueError("invalid identity")
        except (OSError, ValueError, TypeError, KeyError):
            raise HTTPException(503, "本地认证配置不可用，请执行演示账号初始化") from None
        if not identity:
            raise HTTPException(401, "凭据无效")
        if identity["role"] not in roles:
            raise HTTPException(403, "当前身份无此操作权限")
        return {"name": identity["name"], "role": identity["role"]}
    return authenticate
