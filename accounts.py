# -*- coding: utf-8 -*-
"""accounts.py — 多账号(一账号一持久化 profile)支持(仅标准库)。

原理与已实测结论(详见 docs/多账号切换.md):
- 抖音/巨量百应的登录态完全存放在 Chromium 的**持久化 profile 目录**里;
  实测两个不同目录之间 Cookie 互不可见(空目录判定"未登录",已有目录判定"已登录"),
  把某个目录整体复制出去后在新目录里仍判定"已登录"——因此"切账号"= 切 profile 目录。
- 默认账号(不传 --account,或 --account default)= config.browser.profile_dir
  (默认 data/browser_profile),老用户行为完全不变;
- 具名账号 <name> = <accounts_dir>/<name>/browser_profile
  (accounts_dir 默认 data/accounts);
- 登录成功后写一份"账号登记"文件,记录账号名 / 页面账号名(昵称)/ 最近登录时间,
  供 python main.py accounts 展示,避免选错账号。
  默认账号登记在 <accounts_dir>/default.json,具名账号登记在
  <accounts_dir>/<name>/account.json。

安全约定:上述目录都在 .gitignore 内(含登录凭据),严禁提交;本模块只写路径与
时间戳/昵称,**绝不读写或打印任何 Cookie 值**。
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
import re

import config as cfgmod

DEFAULT_ACCOUNT = "default"

# 账号名:字母/数字开头,允许字母数字下划线短横线点,总长 <= 32(用作目录名)
_NAME_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_.-]{0,31}$")
# Windows 保留设备名(用作目录名会失败),提前拒绝并给出中文原因
_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
}


class AccountError(ValueError):
    """账号名非法(调用方应打印原因并以退出码 2 结束,不得静默回退到默认账号)。"""


def normalize_account_name(name) -> str:
    """校验并规范化账号名;非法时抛 AccountError(中文原因)。

    None / 空串 → 默认账号;Default / DEFAULT 同样归一为 default。
    """
    raw = str(name if name is not None else "").strip()
    if not raw:
        return DEFAULT_ACCOUNT
    if raw.lower() == DEFAULT_ACCOUNT:
        return DEFAULT_ACCOUNT
    if not _NAME_RE.match(raw):
        raise AccountError(
            f"账号名不合法: {raw!r}(只允许字母/数字/下划线/短横线/点,1-32 位,且以字母或数字开头)"
        )
    if raw.split(".")[0].lower() in _RESERVED_NAMES:
        raise AccountError(f"账号名不合法: {raw!r}(Windows 保留设备名,不能用作目录名)")
    return raw


def _resolve_rel(rel) -> pathlib.Path:
    """把配置里的相对路径按项目根解析为绝对路径(不创建目录)。"""
    p = pathlib.Path(str(rel))
    if not p.is_absolute():
        p = cfgmod.PROJECT_ROOT / p
    return p.resolve()


def accounts_root(cfg: dict) -> pathlib.Path:
    """具名账号的根目录(browser.accounts_dir,默认 data/accounts);不创建。"""
    rel = (cfg.get("browser", {}) or {}).get("accounts_dir") or "data/accounts"
    return _resolve_rel(rel)


def account_paths(cfg: dict, name=None) -> tuple:
    """返回 (profile 目录, 账号登记文件路径);只算路径,**不创建任何目录**。"""
    acc = normalize_account_name(name)
    root = accounts_root(cfg)
    if acc == DEFAULT_ACCOUNT:
        prof = _resolve_rel((cfg.get("browser", {}) or {}).get("profile_dir")
                            or "data/browser_profile")
        return prof, root / "default.json"
    return root / acc / "browser_profile", root / acc / "account.json"


def resolve_account_name(cfg: dict, cli_account=None) -> str:
    """确定本次要用的账号名:--account 优先,其次 config.browser.account,最后默认账号。"""
    if cli_account is not None and str(cli_account).strip():
        return normalize_account_name(cli_account)
    cfg_account = str((cfg.get("browser", {}) or {}).get("account") or "").strip()
    if cfg_account:
        return normalize_account_name(cfg_account)
    return DEFAULT_ACCOUNT


def apply_account(cfg: dict, cli_account=None) -> str:
    """把账号落到 cfg 上(browser.account / browser.profile_dir),返回实际账号名。

    默认账号**不改动** browser.profile_dir(保留用户自定义路径与老目录);
    具名账号则改写为该账号自己的 profile 目录,后续所有浏览器入口
    (auth.open_session → launch_persistent_context)自动使用它。
    """
    acc = resolve_account_name(cfg, cli_account)
    browser = cfg.setdefault("browser", {})
    browser["account"] = acc
    if acc != DEFAULT_ACCOUNT:
        prof, _ = account_paths(cfg, acc)
        browser["profile_dir"] = str(prof)
    return acc


def read_account_meta(cfg: dict, name=None) -> dict:
    """读账号登记文件(不存在/损坏 → 空 dict;绝不抛)。"""
    _, meta_path = account_paths(cfg, name)
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_account_meta(cfg: dict, name=None, *, nickname: str = "", extra: dict | None = None) -> pathlib.Path:
    """写账号登记文件(登录成功后调用):账号名 / 页面账号名 / 最近登录时间。

    保留首次登录时间与既有昵称(本次读不到昵称时不覆盖已知昵称)。
    """
    acc = normalize_account_name(name)
    prof, meta_path = account_paths(cfg, acc)
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    meta = read_account_meta(cfg, acc)
    meta.update({
        "tool": "douyin-live-gmv-export",
        "account": acc,
        "profile_dir": str(prof),
        "logged_in_at": now,
        "nickname": str(nickname or "").strip() or str(meta.get("nickname") or ""),
    })
    meta.setdefault("first_login_at", now)
    if extra:
        meta.update(extra)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta_path


def profile_ready(profile_dir: pathlib.Path) -> bool:
    """profile 目录是否已有内容(意味着可能已登录过;不读 Cookie,只数条目)。"""
    try:
        return profile_dir.is_dir() and any(profile_dir.iterdir())
    except OSError:
        return False


def list_accounts(cfg: dict) -> list:
    """列出已知账号:默认账号恒在首位,其余来自 accounts_dir 下的目录 / 登记文件。"""
    root = accounts_root(cfg)
    names = {DEFAULT_ACCOUNT}
    try:
        children = sorted(root.iterdir()) if root.is_dir() else []
    except OSError:
        children = []
    for child in children:
        if child.is_dir() and _NAME_RE.match(child.name):
            names.add(child.name)
        elif child.is_file() and child.suffix.lower() == ".json" and _NAME_RE.match(child.stem):
            names.add(child.stem)
    rows = []
    for acc in sorted(names, key=lambda n: (n != DEFAULT_ACCOUNT, n.lower())):
        prof, _ = account_paths(cfg, acc)
        rows.append({
            "account": acc,
            "profile_dir": prof,
            "ready": profile_ready(prof),
            "meta": read_account_meta(cfg, acc),
        })
    return rows


def display_path(p: pathlib.Path) -> str:
    """把项目内的绝对路径显示为相对项目根的形式(仅用于打印)。"""
    try:
        return str(pathlib.Path(p).resolve().relative_to(cfgmod.PROJECT_ROOT))
    except Exception:
        return str(p)
