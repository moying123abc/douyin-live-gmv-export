# -*- coding: utf-8 -*-
"""auth.py — 登录态管理(持久化 Chromium profile + 人工扫码登录)。

原则与边界(与团队既定方案一致):
- 本模块不做任何登录绕过、凭据注入或签名逆向;只在"账号本人已登录且有权限"的
  会话内操作。
- 首次登录必须 headful(有头可见窗口),由本机人工扫码 / 输入验证码完成;
  登录凭据保存在独立持久化 profile 目录(config.browser.profile_dir,
  默认 ``data/browser_profile``),该目录被 .gitignore 忽略,严禁提交进仓库。
- 会话有效期由页面 / Cookie 决定;过期后再次运行 login 即可重新扫码,
  无需重复安装任何东西。
- playwright 采用"调用时导入":未安装依赖时给出明确的中文引导并优雅退出,
  保证 ``--help`` / ``--dry-run`` 等无需浏览器内核的命令可在纯净环境运行。
"""
from __future__ import annotations

import pathlib
import sys
import time

import config as cfgmod

# 抖音体系登录成功后会种下的关键 Cookie 名称(仅用于存在性判断,绝不打印值)
_LOGIN_COOKIE_HINTS = {
    "sessionid",
    "sessionid_ss",
    "sid_tt",
    "uid_tt",
    "sid_guard",
    "passport_csrf_token",
}


def _import_playwright():
    """延迟导入 playwright;失败时打印中文安装引导并以退出码 2 优雅退出。"""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
        return sync_playwright
    except Exception as exc:  # ModuleNotFoundError 等
        print("[auth] 未找到 playwright 模块。", file=sys.stderr)
        print("[auth] 请先安装依赖并下载浏览器内核:", file=sys.stderr)
        print("        pip install -r requirements.txt", file=sys.stderr)
        print("        python -m playwright install chromium", file=sys.stderr)
        print(f"[auth] 原始错误: {exc}", file=sys.stderr)
        raise SystemExit(2)


def resolve_profile_dir(cfg: dict) -> pathlib.Path:
    """解析持久化 profile 目录(相对项目根),不存在则创建。"""
    rel = cfg.get("browser", {}).get("profile_dir", "data/browser_profile")
    p = pathlib.Path(rel)
    if not p.is_absolute():
        p = cfgmod.PROJECT_ROOT / p
    p = p.resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def profile_exists(cfg: dict) -> bool:
    """profile 目录里是否已有内容(意味着可能已有登录会话)。"""
    p = resolve_profile_dir(cfg)
    if not p.is_dir():
        return False
    return any(p.iterdir())


def _headless_from(cfg: dict, headful_override: bool | None = None) -> bool:
    if headful_override is not None:
        return not headful_override
    return not bool(cfg.get("browser", {}).get("headful", True))


def open_session(cfg: dict, *, headful: bool | None = None):
    """打开持久化 Chromium 会话。

    返回 ``(pw, context, page)``;调用方必须用 ``finally`` 关闭:
        context.close(); pw.stop()
    首次运行会创建 profile 目录;headful=True 用于人工登录,默认跟随配置。
    """
    sync_playwright = _import_playwright()
    profile_dir = resolve_profile_dir(cfg)
    headless = _headless_from(cfg, headful)
    pw = sync_playwright().start()
    try:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            viewport={"width": 1600, "height": 1000},
            locale="zh-CN",
        )
        page = context.pages[0] if context.pages else context.new_page()
        return pw, context, page
    except Exception:
        pw.stop()
        raise


def _cookie_is_douyin(cookie: dict) -> bool:
    domain = str(cookie.get("domain") or "").lower().lstrip(".")
    return domain == "douyin.com" or domain.endswith(".douyin.com")


def session_available(cfg: dict, context) -> bool:
    """通过 Cookie 存在性判断是否已有登录会话(不读取、不打印 Cookie 值)。"""
    try:
        cookies = context.cookies()
    except Exception:
        return False
    names = {c.get("name", "") for c in cookies if _cookie_is_douyin(c)}
    return bool(names & _LOGIN_COOKIE_HINTS)


def ensure_logged_in(cfg: dict, context, page, *, wait_seconds: int | None = None) -> bool:
    """确保会话已登录。

    - 若已有登录 Cookie → 直接返回 True(自动复用);
    - 否则在有头窗口打开登录地址,等本机人工扫码/登录(轮询 Cookie,
      最长 wait_seconds 秒;用户完成后无需按回车,会自动被检测到);
    - 返回是否登录成功;失败时不做任何注入/绕过,由调用方给出人工引导。
    """
    if session_available(cfg, context):
        print("[auth] 检测到已有登录会话(profile 自动复用),无需重新扫码。")
        return True

    login_url = cfg.get("browser", {}).get("login_url", "https://eos.douyin.com/")
    total = wait_seconds or int(cfg.get("browser", {}).get("wait_login_seconds", 1800))
    print("[auth] 未检测到已登录会话,将在有头(可见)浏览器窗口中打开登录页。")
    print(f"[auth] 登录地址: {login_url}")
    print("[auth] 请在浏览器窗口里扫码 / 登录你的抖音账号(需对目标直播间有查看权限)。")
    print(f"[auth] 登录完成后无需操作,程序会自动检测(最长等待 {total} 秒)。")
    print("[auth] 提示:登录凭据仅保存在本地持久化 profile,不会写入任何文件输出物。")

    try:
        page.goto(login_url, wait_until="domcontentloaded", timeout=60_000)
    except Exception as exc:
        print(f"[auth] 打开登录页失败: {exc}", file=sys.stderr)
        return False
    page.wait_for_timeout(2000)

    deadline = time.time() + total
    last_report = 0.0
    while time.time() < deadline:
        if session_available(cfg, context):
            print("[auth] 登录成功(检测到会话 Cookie)。")
            return True
        now = time.time()
        if now - last_report >= 15:
            remaining = int(deadline - now)
            print(f"[auth] 仍在等待人工登录… 剩余约 {remaining} 秒。")
            last_report = now
        page.wait_for_timeout(3000)

    print("[auth] 等待超时,未检测到登录。", file=sys.stderr)
    return False


def perform_login(cfg: dict) -> int:
    """login 子命令主体:打开会话并等待人工登录,返回进程退出码。"""
    profile_dir = resolve_profile_dir(cfg)
    if profile_exists(cfg):
        print(f"[auth] profile 已存在: {profile_dir}")
        print("[auth] 将复用该会话;若需强制重新登录,请先删除该目录后重试。")

    pw, context, page = open_session(cfg, headful=True)
    try:
        ok = ensure_logged_in(cfg, context, page)
        if ok:
            print("[auth] 登录态已就绪并持久化于本地 profile(不入库、不导出)。")
            print("[auth] 后续 probe / export 会复用本会话;会话过期时重跑 login 即可。")
            return 0
        print("[auth] 未在限定时间内完成登录,请重试: python main.py login", file=sys.stderr)
        return 2
    finally:
        try:
            context.close()
        finally:
            pw.stop()
