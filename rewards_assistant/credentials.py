# -*- coding: utf-8 -*-
"""本机加密保存 Microsoft Rewards 会话 Cookie。

同步链路只需要带 Cookie 的两个 GET，不需要一整个 Chromium 进程。凭证在
用户本人于可见 Edge 窗口完成登录（含 MFA）后由 ``context.cookies()`` 导出，
加密写入数据目录；之后 ``HttpFetcher`` 直接发 HTTP 请求。

安全口径（与 README 一致）：

* 只写本机数据目录，**不上传、不打印、不写运行日志**；
* Windows 用 DPAPI（绑定当前 Windows 用户账户），其余平台用机器绑定密钥
  + PBKDF2 派生 + encrypt-then-MAC，文件权限 0600；
* 不读取用户日常 Edge 的 Cookie 数据库（app-bound encryption 且运行中
  被独占，无法安全读取）；
* 「退出登录」删除加密文件并清空应用独立 profile 的登录态。

这不是硬件级密钥库：拿到本机文件 + 同一用户/机器环境仍可解密。目标是
**不以明文落盘**，而不是对抗已经攻陷本机的攻击者。机器名或系统用户变化
会让非 Windows 凭证失效，重新登录一次即可。

多开隔离：凭证文件落在 ``config.instance_home()/<FILENAME>``，且非默认实例
的加密熵追加实例 id，因此不同环境保存的是不同账号的会话，互相拷文件也解不开。

零第三方依赖：只用标准库；Windows DPAPI 通过 ctypes 调用系统 DLL。
"""
from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import os
import platform
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

FILENAME = "session_cookies.bin"
LEGACY_FILENAME = "cookies.txt"
MAGIC = b"HSAC1"                       # 5 bytes，后接 1 byte 模式
MODE_DPAPI = b"D"
MODE_DERIVED = b"X"
ENTROPY = b"MSRewardsAssistant/session-cookies/v1"
PBKDF2_ROUNDS = 120_000
TAG_LEN = 32
NONCE_LEN = 16

# 登录窗口会导出整个 profile 的 Cookie；只保存 Microsoft Rewards / 账户相关
# 域，避免把无关站点（广告、分析、第三方登录）的凭证也写进本机文件、并随
# 同步请求带出去。迁移自明文 Cookie 头的 hostless 项（domain 为空）保留。
RELEVANT_SUFFIXES = (
    "bing.com", "bing.net", "microsoft.com", "microsoftonline.com",
    "live.com", "live.net", "msn.com", "office.com", "windows.net",
    "microsofttranslator.com", "azure.com",
)


def _relevant_domain(domain: str) -> bool:
    d = (domain or "").lstrip(".").lower()
    if not d:
        return True
    return any(d == s or d.endswith("." + s) for s in RELEVANT_SUFFIXES)


def data_dir() -> Path:
    """当前实例的数据目录；多开时每个实例一份，互不共享。

    平台默认路径、``MS_REWARDS_HOME`` 覆盖与实例隔离只在
    ``config.instance_home()`` 里定义一次，避免多开时凭证目录与清理目录
    指向不同位置。
    """
    from .config import instance_home  # 延迟导入，避免与 config 形成导入环
    return Path(instance_home())


def _entropy() -> bytes:
    """每个实例用不同的加密熵。

    默认实例沿用既有 ENTROPY，既有凭证文件零迁移；新实例追加实例 id，
    因此即便把 A 环境的凭证文件拷到 B 环境目录，也解不出 A 的账号会话 ——
    多开的「环境独立」在密钥层同样成立。
    """
    try:
        from .config import instance_id as _id
        ident = _id()
    except Exception:
        return ENTROPY
    if ident == "default":
        return ENTROPY
    return ENTROPY + b"|instance=" + ident.encode("ascii", "ignore")


# ---------------------------------------------------------------- Windows DPAPI

class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong),
                ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _make_blob(buf: bytes):
    arr = (ctypes.c_byte * max(1, len(buf))).from_buffer_copy(buf or b"\0")
    return _BLOB(len(buf), ctypes.cast(arr, ctypes.POINTER(ctypes.c_byte))), arr


def _dpapi(data: bytes, *, protect: bool) -> Optional[bytes]:
    """CryptProtectData / CryptUnprotectData，绑定当前 Windows 用户。"""
    if sys.platform != "win32":
        return None
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(_BLOB), ctypes.c_wchar_p,
                       ctypes.POINTER(_BLOB), ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_ulong, ctypes.POINTER(_BLOB)]
        fn.restype = ctypes.c_int
        src, src_arr = _make_blob(data)
        ent, ent_arr = _make_blob(_entropy())
        out = _BLOB()
        ok = fn(ctypes.byref(src), "MSRewardsAssistant", ctypes.byref(ent),
                None, None, 0, ctypes.byref(out))
        if not ok or not out.pbData or out.cbData == 0:
            return None
        try:
            raw = bytes(ctypes.cast(out.pbData,
                                    ctypes.POINTER(ctypes.c_byte * out.cbData)).contents)
        finally:
            kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))
        del src_arr, ent_arr
        return raw
    except Exception:
        return None


# ------------------------------------------------------- 非 Windows：派生密钥

@lru_cache(maxsize=1)
def _machine_seed() -> bytes:
    """机器 / 系统用户绑定；变化时凭证自动失效并提示重新登录。

    进程内不变，缓存后每次 load/save 不再重复读取 machine-id 文件并拼接
    字符串（PBKDF2 仍按 nonce 每次重新派生，安全强度不变）。
    """
    bits = [platform.node(), platform.system(), platform.machine()]
    try:
        import getpass
        bits.append(getpass.getuser())
    except Exception:
        pass
    for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            bits.append(Path(candidate).read_text(encoding="utf-8", errors="ignore").strip())
            break
        except OSError:
            continue
    return "|".join(bits).encode("utf-8", "ignore")


@lru_cache(maxsize=8)
def _pbkdf2_key(seed: bytes, nonce: bytes) -> bytes:
    """按「机器种子 + 实例熵 + 文件自带 nonce」缓存派生结果。

    PBKDF2(120k 轮) 是同步链路里最重的单点 CPU 开销，这里只缓存派生结果：
    ``load()`` 依然每次读文件、验 HMAC、解密，安全流程一步不减。

    * key 含完整 ``seed``：换机器 / 换用户 / 换实例 → seed 变 → 缓存不命中，
      重新派生后 HMAC 校验失败，「凭证自动失效」口径不变；
    * key 含文件里的 ``nonce``：每次 ``save()`` 用新的随机 nonce，不会被旧
      缓存命中而跳过校验；
    * ``maxsize=8`` 有界：最多留 8 份 32 字节密钥（256 B），频繁重登录也不会
      让缓存随保存次数增长。
    """
    return hashlib.pbkdf2_hmac("sha256", seed, nonce, PBKDF2_ROUNDS, 32)


def _derive_key(nonce: bytes) -> bytes:
    return _pbkdf2_key(_machine_seed() + _entropy(), nonce)


def _xor_stream(key: bytes, nonce: bytes, length: int) -> bytes:
    return hashlib.shake_256(key + nonce).digest(length)


def _xor_bytes(data: bytes, stream: bytes) -> bytes:
    """整块整数 XOR，避免逐字节 Python 循环（大整数运算在 C 层完成）。"""
    n = len(data)
    return (int.from_bytes(data, "big") ^ int.from_bytes(stream[:n], "big")
            ).to_bytes(n, "big")


def _seal(raw: bytes) -> bytes:
    protected = _dpapi(raw, protect=True)
    if protected is not None:
        return MAGIC + MODE_DPAPI + protected
    nonce = os.urandom(NONCE_LEN)
    key = _derive_key(nonce)
    ct = _xor_bytes(raw, _xor_stream(key, nonce, len(raw)))
    tag = hmac.new(key, MAGIC + MODE_DERIVED + nonce + ct, hashlib.sha256).digest()
    return MAGIC + MODE_DERIVED + nonce + ct + tag


def _unseal(blob: bytes) -> Optional[bytes]:
    if len(blob) < len(MAGIC) + 1 or blob[:len(MAGIC)] != MAGIC:
        return None
    mode, body = blob[len(MAGIC):len(MAGIC) + 1], blob[len(MAGIC) + 1:]
    if mode == MODE_DPAPI:
        return _dpapi(body, protect=False)
    if mode != MODE_DERIVED or len(body) < NONCE_LEN + TAG_LEN:
        return None
    nonce, rest = body[:NONCE_LEN], body[NONCE_LEN:]
    ct, tag = rest[:-TAG_LEN], rest[-TAG_LEN:]
    key = _derive_key(nonce)
    expect = hmac.new(key, MAGIC + MODE_DERIVED + nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expect):
        return None                       # 被篡改 / 换机器 → 视为无凭证
    return _xor_bytes(ct, _xor_stream(key, nonce, len(ct)))


# ------------------------------------------------------------------ CookieStore

def normalize(cookies: Iterable[Dict[str, Any]]) -> List[dict]:
    """把 Playwright ``context.cookies()`` 收敛成 requests 需要的最小字段集。"""
    out: List[dict] = []
    for c in cookies or []:
        if not isinstance(c, dict):
            continue
        name, value = str(c.get("name") or ""), c.get("value")
        if not name or value is None:
            continue
        domain = str(c.get("domain") or "")
        if not _relevant_domain(domain):
            continue
        expires = c.get("expires", -1)
        try:
            expires = float(expires)
        except (TypeError, ValueError):
            expires = -1
        out.append({
            "name": name, "value": str(value),
            "domain": domain,
            "path": str(c.get("path") or "/"),
            "secure": bool(c.get("secure", False)),
            "expires": expires,
        })
    return out


def live_only(cookies: Iterable[Dict[str, Any]], now: Optional[float] = None) -> List[dict]:
    """丢掉已过期的 Cookie；会话 Cookie（expires <= 0 / 缺失）保留。"""
    now = time.time() if now is None else now
    out = []
    for c in cookies or []:
        try:
            exp = float(c.get("expires", -1))
        except (TypeError, ValueError):
            exp = -1
        if exp <= 0 or exp > now:
            out.append(dict(c))
    return out


class CookieStore:
    """加密凭证；损坏 / 篡改 / 换机器一律当作「尚未保存会话」。

    默认（无显式 path）把 :func:`_seal` 的**密文 blob 原样**存进统一状态库
    ``kv[credentials]``，不解密、不重新加密，安全性与原来的 0600 独立文件
    等价。显式传 ``path`` 时保留独立文件模式，供诊断与回归测试使用。

    状态库写失败时自动回退到独立文件；两项都不可用时 ``save()`` 返回 0，
    调用方会把「没保存上」当成未登录，绝不假装登录成功。
    """

    def __init__(self, path=None) -> None:
        self._kv = path is None
        if path:
            self.path = Path(path)
        else:
            try:
                from .config import credentials_path as _cred
                self.path = Path(_cred())
            except Exception:
                self.path = data_dir() / FILENAME

    def _kv_blob(self):
        from . import state
        blob = state.kv_get(state.K_CREDENTIALS)
        if blob is None or len(blob) <= len(MAGIC) + 1:
            return None
        return blob

    def exists(self) -> bool:
        if self._kv:
            return self._kv_blob() is not None
        return self.path.is_file() and self.path.stat().st_size > len(MAGIC) + 1

    # ------------------------------------------------------------ 独立文件读写
    def _save_to_file(self, blob: bytes) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(str(tmp), str(self.path))
            try:
                os.chmod(str(self.path), 0o600)
            except OSError:
                pass                          # Windows 上 chmod 语义不同，忽略
            return True
        except (OSError, ValueError):
            try:
                self.path.with_name(self.path.name + ".tmp").unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def _read_file(self) -> Optional[bytes]:
        try:
            return self.path.read_bytes()
        except OSError:
            return None

    # ------------------------------------------------------------ 公开接口
    def save(self, cookies: Iterable[Dict[str, Any]]) -> int:
        items = normalize(cookies)
        raw = json.dumps(items, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        blob = _seal(raw)
        if self._kv:
            from . import state
            if state.kv_set(state.K_CREDENTIALS, blob):
                # 旧独立文件已完成迁移，删掉避免同一会话存两份
                try:
                    self.path.unlink(missing_ok=True)
                    self.path.with_name(self.path.name + ".tmp").unlink(missing_ok=True)
                except OSError:
                    pass
                return len(items)
            # 备选方案：状态库不可写时退回独立文件
            return len(items) if self._save_to_file(blob) else 0
        return len(items) if self._save_to_file(blob) else 0

    def load(self, now: Optional[float] = None) -> List[dict]:
        if self._kv:
            from . import state
            blob = self._kv_blob()
            if blob is None:
                blob = self._read_file()       # 旧版文件 / 写失败回退产物
                if blob is not None:
                    state.kv_set(state.K_CREDENTIALS, blob)
                    try:
                        self.path.unlink(missing_ok=True)
                    except OSError:
                        pass
        else:
            blob = self._read_file()
        if blob is None:
            return []
        raw = _unseal(blob)
        if raw is None:
            # 密文被篡改 / 换机器 / 跨实例拷贝：只清这一项，不影响积分库
            if self._kv:
                from . import state
                state.kv_delete(state.K_CREDENTIALS)
                state._note("凭证密文校验失败，已清除该项（视为未登录）")
            return []
        try:
            items = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError):
            return []
        if not isinstance(items, list):
            return []
        return live_only((x for x in items if isinstance(x, dict)), now=now)

    def clear(self) -> None:
        if self._kv:
            from . import state
            state.kv_delete(state.K_CREDENTIALS)
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            self.path.with_name(self.path.name + ".tmp").unlink(missing_ok=True)
        except OSError:
            pass

    def migrate_legacy(self) -> bool:
        """遗留明文 ``cookies.txt`` 一次性迁移为加密凭证，成功后删除明文。

        明文文件是 F12 复制的整条 Cookie 头，没有域信息；迁移后 requests 会把
        它们作为 hostless cookie 发送，仅用于平滑升级。界面会建议重新登录一次
        以获得完整的 domain/path 归属。
        """
        legacy = self.path.parent / LEGACY_FILENAME
        if not legacy.is_file():
            return False
        try:
            raw = legacy.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            return False
        if raw.lower().startswith("cookie:"):
            raw = raw.split(":", 1)[1]
        jar: Dict[str, str] = {}
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                k, v = k.strip(), v.strip()
                if k:
                    jar[k] = v
        if self.save([{"name": k, "value": v, "domain": "", "path": "/",
                       "secure": True, "expires": -1}
                      for k, v in jar.items()]) == 0:
            return False
        try:
            legacy.unlink()
        except OSError:
            pass
        return True
