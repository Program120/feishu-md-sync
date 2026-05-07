"""Feishu (Lark) Open API client.

Auth + thin HTTP wrapper. Loads credentials from .feishu.local. The location is
resolved in this order:
    1. FEISHU_LOCAL_PATH environment variable (full file path)
    2. ./.feishu.local in the current working directory

Required keys:
    FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_WIKI_SPACE_ID, FEISHU_WIKI_TRD_PARENT_NODE
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

BASE = "https://open.feishu.cn/open-apis"


def resolve_local_path(explicit: Optional[str] = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("FEISHU_LOCAL_PATH")
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd() / ".feishu.local"


def load_local_env(path: Optional[str] = None) -> Dict[str, str]:
    p = resolve_local_path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"{p} not found. Create it with FEISHU_APP_ID, FEISHU_APP_SECRET, "
            f"FEISHU_WIKI_SPACE_ID, FEISHU_WIKI_TRD_PARENT_NODE — see "
            f".feishu.local.example bundled with this skill."
        )
    out: Dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self._token: Optional[str] = None
        self._token_exp: float = 0.0

    def _ensure_token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        resp = requests.post(
            f"{BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            raise RuntimeError(f"tenant_access_token failed: {body}")
        self._token = body["tenant_access_token"]
        self._token_exp = time.time() + int(body.get("expire", 7200))
        return self._token

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        raw: bool = False,
    ) -> Any:
        url = f"{BASE}{path}"
        headers = {"Authorization": f"Bearer {self._ensure_token()}"}
        if not files:
            headers["Content-Type"] = "application/json; charset=utf-8"
        resp = requests.request(
            method,
            url,
            params=params,
            json=json if not files and not data else None,
            files=files,
            data=data,
            headers=headers,
            timeout=60,
        )
        if raw:
            return resp
        try:
            body = resp.json()
        except ValueError:
            resp.raise_for_status()
            raise
        if isinstance(body, dict) and body.get("code") not in (0, None):
            raise RuntimeError(
                f"{method} {path} failed code={body.get('code')} msg={body.get('msg')}"
            )
        return body

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self.request("POST", path, **kw)

    def delete(self, path: str, **kw: Any) -> Any:
        return self.request("DELETE", path, **kw)

    def patch(self, path: str, **kw: Any) -> Any:
        return self.request("PATCH", path, **kw)


def from_local(path: Optional[str] = None) -> FeishuClient:
    env = load_local_env(path)
    app_id = env.get("FEISHU_APP_ID") or os.environ.get("FEISHU_APP_ID")
    app_secret = env.get("FEISHU_APP_SECRET") or os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise RuntimeError("FEISHU_APP_ID / FEISHU_APP_SECRET missing in .feishu.local")
    return FeishuClient(app_id, app_secret)


if __name__ == "__main__":
    import sys
    config = sys.argv[1] if len(sys.argv) > 1 else None
    c = from_local(config)
    tok = c._ensure_token()
    print(f"OK token len={len(tok)} expires≈{int(c._token_exp - time.time())}s")
