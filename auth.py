# -*- coding: utf-8 -*-
"""auth.py — 登录态管理(持久化 Chromium profile + 人工扫码登录)。

原则与边界(与团队既定方案一致):
- 本模块不做任何登录绕过、凭据注入或签名逆向;只在"账号本人已登录且有权限"的
  会话内操作。
- 首次登录必须 headful(有头可见窗口),由本机人工扫码 / 输入验证码完成;
- 多账号:一个账号 = 一个独立持久化 profile 目录(见 accounts.py;--account 切换),
  不同目录之间的登录态完全隔离(实测见 docs/多账号切换.md);
- 登录凭据保存在独立持久化 profile 目录(config.browser.profile_dir,
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

import accounts as acctmod
import config as cfgmod

# 抖音体系**登录成功后**才会种下的鉴权 Cookie 名称(仅用于存在性判断,绝不打印值)。
#
# 注意:不要把"仅打开页面(未登录)就会种下"的匿名 Cookie 放进来,否则会把
# "还没登录"误判成"已登录"。实测反例(打开登录页即出现、与是否登录无关):
#   passport_csrf_token / passport_csrf_token_default / ttwid / s_v_web_id /
#   csrf_session_id / biz_trace_id / be-token / gfkadpd 等。
# 历史上 passport_csrf_token 曾被列入本集合,导致 python main.py login 在
# 未扫码的情况下数秒内误报"登录成功"(随后各命令失败: status_code=8 用户未登录)。
_LOGIN_COOKIE_HINTS = {
    "sessionid",
    "sessionid_ss",
    "sid_tt",
    "uid_tt",
    "sid_guard",
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


def account_name(cfg: dict) -> str:
    """当前使用的账号名(config.browser.account,由 accounts.apply_account 写入;缺省 = 默认账号)。"""
    return str((cfg.get("browser", {}) or {}).get("account") or acctmod.DEFAULT_ACCOUNT)


def account_hint(cfg: dict) -> str:
    """命令后缀提示:默认账号无需写 --account;具名账号给出可直接复制的后缀。"""
    acc = account_name(cfg)
    return "" if acc == acctmod.DEFAULT_ACCOUNT else f" --account {acc}"


# 登录后首页右上角账号面板的类名(2026-09 实测为 panel-profile-name-XXXXXX;
# CSS Module 的语义前缀稳定、哈希后缀会变,故只按前缀匹配)
_NICKNAME_SELECTORS = (
    "[class*='panel-profile-name']",
    "[class*='head-container'] [class*='profile'] [class*='name']",
)


def read_account_nickname(page) -> str:
    """尽力读取页面上当前登录账号的名称(仅用于展示与登记;失败返回空串,绝不抛)。"""
    js = """(sels) => {
      for (const sel of sels) {
        let els = [];
        try { els = document.querySelectorAll(sel); } catch (e) { continue; }
        for (const el of els) {
          const t = (el.textContent || '').trim();
          if (t && t.length <= 40) return t;
        }
      }
      return '';
    }"""
    try:
        return str(page.evaluate(js, list(_NICKNAME_SELECTORS)) or "").strip()
    except Exception:
        return ""


def record_login(cfg: dict, page) -> str:
    """登录成功后写账号登记(账号名 / 页面账号名 / 最近登录时间);失败不影响登录结果。"""
    acc = account_name(cfg)
    nick = read_account_nickname(page)
    if not nick:
        # 扫码后页面可能仍停在登录页:回首页再读一次(仅展示用,超时/失败都忽略)
        try:
            page.goto(cfg.get("browser", {}).get("login_url", "https://eos.douyin.com/"),
                      wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(4000)
            nick = read_account_nickname(page)
        except Exception:
            nick = ""
    try:
        meta_path = acctmod.write_account_meta(cfg, acc, nickname=nick)
    except Exception as exc:
        print(f"[auth] 账号登记写入失败(不影响登录): {exc}", file=sys.stderr)
        return nick
    shown = f",页面账号名: {nick}" if nick else ""
    print(f"[auth] 账号 {acc} 登录成功{shown};已登记 {meta_path}")
    return nick


def sync_account_nickname(cfg: dict, page) -> str:
    """在读数据的页面上顺手刷新"页面账号名"(登录登记里的 nickname);读不到返回空串。

    为什么要做:--account 只是本地登录 profile 的名字,用户切账号后真正代表
    数据归属的是百应页面右上角的账号名。导出时路过页面就更新一次登记,
    日报因此能写出"1(示例账号A)"这样的归属。失败绝不影响导出。
    """
    nick = read_account_nickname(page)
    if not nick:
        return ""
    try:
        acctmod.update_account_nickname(cfg, account_name(cfg), nick)
    except Exception:  # noqa: BLE001 — 只影响展示
        pass
    return nick


def _headless_from(cfg: dict, headful_override: bool | None = None) -> bool:
    if headful_override is not None:
        return not headful_override
    return not bool(cfg.get("browser", {}).get("headful", True))


def open_session(cfg: dict, *, headful: bool | None = None):
    """打开持久化 Chromium 会话。

    ``headful`` 为 None 时跟随配置 ``browser.headful``(命令行 --headless/--headful 会先写进
    cfg,所以业务路径传 None 即可);显式 True/False 会覆盖配置——人工登录必须传 True。
    返回 ``(pw, context, page)``;调用方必须用 ``finally`` 关闭:
        context.close(); pw.stop()
    首次运行会创建 profile 目录;headful=True 用于人工登录,默认跟随配置。
    """
    sync_playwright = _import_playwright()
    profile_dir = resolve_profile_dir(cfg)
    headless = _headless_from(cfg, headful)
    mode = "无头(后台运行,不弹窗)" if headless else "有头(可见窗口)"
    print(f"[auth] 账号: {account_name(cfg)}(profile: {profile_dir}) · {mode}")
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


def _douyin_cookie_names(context) -> set:
    """当前 context 下 douyin 域 Cookie 的**名称**集合(永不读取/打印 Cookie 值)。"""
    try:
        cookies = context.cookies()
    except Exception:
        return set()
    return {c.get("name", "") for c in cookies if _cookie_is_douyin(c)}


def matched_login_cookies(context) -> list:
    """命中"已登录"判定的 Cookie 名(供日志说明用;不含任何 Cookie 值)。"""
    return sorted(_douyin_cookie_names(context) & _LOGIN_COOKIE_HINTS)


def session_available(cfg: dict, context) -> bool:
    """Cookie 层面的**预筛**:本地是否还留着"登录后才会种下"的鉴权 Cookie。

    注意:这是必要条件,不是充分条件。鉴权 Cookie 的到期时间远长于服务端会话的
    实际寿命(2026-11-01 实测:sessionid 还剩 39 天才到期,服务端却已判未登录)。
    真正的登录判定请用 verify_session()。
    """
    return bool(matched_login_cookies(context))


# ---------------------------------------------------------------------------
# 服务端登录态校验(2026-11-01 修复:Cookie 还在 → 误判"已登录")
#
# 实测证据(账号 1):
#   - 5 个鉴权 Cookie 全在,sessionid 到期 2026-12-10(还剩 39 天);
#   - session_available() 判 True;
#   - 但 /dp/liveScreen 被重定向到 /livesite/login(页面=抖音扫码登录);
#   - room/paged 返回 status_code=8 msg='用户未登录'。
# 修复前 ensure_logged_in 命中 Cookie 就直接 return,连页面都不打开 →
# login 几秒内误报"成功"、用户拿不到扫码窗口也来不及扫,直到 daily 导出
# 才以 status_code=8 失败;且 record_login 会把 logged_in_at 刷成假时间。
# 现在:Cookie 只作预筛,必须再向服务端确认。
# ---------------------------------------------------------------------------

SESSION_OK = "ok"
SESSION_EXPIRED = "expired"
SESSION_UNKNOWN = "unknown"

# 接口统一信封里的状态码:0=成功;8=用户未登录(实测)
_SESSION_OK_CODES = {0}
_SESSION_EXPIRED_CODES = {8}
# 会话失效时页面会被踢到的路径(实测 /livesite/login)
_SESSION_EXPIRED_URL_MARKERS = ("/livesite/login", "/passport/login", "/login")
# 登录页 / 过期提示的文案特征
_SESSION_EXPIRED_TEXT_MARKERS = (
    "扫码登录", "验证码登录", "登录已过期", "登录过期", "请重新登录", "重新扫码",
)

_PROBE_TIMEOUT_MS = 20_000


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _url_path(url) -> str:
    try:
        from urllib.parse import urlsplit
        return urlsplit(str(url or "")).path or ""
    except Exception:
        return ""


def classify_session_probe(*, status_code=None, url=None, text=None) -> str:
    """把探测信号归类为 SESSION_OK / SESSION_EXPIRED / SESSION_UNKNOWN(纯函数,可离线测)。

    优先级:接口状态码 > 页面 URL > 页面文案。
    - 状态码 0 → ok(服务端已确认,页面还没渲染完也算数);
    - 状态码 8 → expired;
    - 其它状态码(权限类错误等)不单独下结论,继续看页面信号;
    - 都判不出来 → unknown(调用方保守处理并提示,不冒充已登录)。
    """
    code = _as_int(status_code)
    if code is not None:
        if code in _SESSION_OK_CODES:
            return SESSION_OK
        if code in _SESSION_EXPIRED_CODES:
            return SESSION_EXPIRED
    path = _url_path(url) if url else ""
    if path and any(marker in path for marker in _SESSION_EXPIRED_URL_MARKERS):
        return SESSION_EXPIRED
    if text and any(marker in str(text) for marker in _SESSION_EXPIRED_TEXT_MARKERS):
        return SESSION_EXPIRED
    return SESSION_UNKNOWN


def _page_body_text(page) -> str:
    try:
        return str(page.evaluate(
            "() => (document.body && document.body.innerText) || ''") or "")
    except Exception:
        return ""


def probe_session_status(cfg: dict, context):
    """用**一次 GET**向服务端确认会话(不渲染整页、不读取/不打印 Cookie 值)。

    返回 (status_code, detail);拿不到响应时 status_code 为 None。
    """
    try:
        # 延迟导入:auth 被 extractor.sessions 导入,模块级互引会循环
        from extractor.sessions import day_paged_url
        import datetime as _dt
        url = day_paged_url(_dt.date.today().strftime("%Y-%m-%d"))
    except Exception as exc:  # noqa: BLE001 — 探测失败不应中断调用方
        return None, f"构造探测请求失败: {type(exc).__name__}: {exc}"
    try:
        payload = context.request.get(url, timeout=_PROBE_TIMEOUT_MS).json()
    except Exception as exc:  # noqa: BLE001
        return None, f"探测请求失败: {type(exc).__name__}: {exc}"
    if not isinstance(payload, dict):
        return None, "探测响应不是 JSON 对象"
    return payload.get("status_code"), str(payload.get("status_msg") or "")


def verify_session(cfg: dict, context, page=None) -> str:
    """向服务端确认会话是否仍然有效,返回 SESSION_OK / SESSION_EXPIRED / SESSION_UNKNOWN。"""
    code, _detail = probe_session_status(cfg, context)
    verdict = classify_session_probe(status_code=code)
    if verdict != SESSION_UNKNOWN:
        return verdict
    # 拿到了响应但状态码不明确(例如权限类错误码)→ 再看业务页有没有被踢到登录页
    if page is not None and code is not None:
        base = cfg.get("probe", {}).get("base_url", "https://eos.douyin.com/dp/liveScreen")
        try:
            page.goto(base, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(4000)
        except Exception:  # noqa: BLE001
            return SESSION_UNKNOWN
        return classify_session_probe(url=getattr(page, "url", ""), text=_page_body_text(page))
    return SESSION_UNKNOWN


def ensure_logged_in(cfg: dict, context, page, *, wait_seconds: int | None = None,
                     verify: bool = True, wait_for_scan: bool = False) -> bool:
    """确保会话**真的**可用;不可用时的处置取决于 wait_for_scan。

    判定顺序(2026-11-01 起):
    1. 本地有鉴权 Cookie → 再问服务端(verify_session):
       - 明确 ok      → 复用,返回 True;
       - 明确 expired → 判定为**需要重新扫码**;
       - 判不出来     → 警告后按已有 Cookie 继续(与旧行为一致,避免网络抖动时误拦)。
    2. 需要重新扫码时:
       - wait_for_scan=False(export/daily/sessions/probe 等)→ **立即返回 False**,
         并打印 `python main.py login<账号后缀>`,不让批处理任务空等人工;
       - wait_for_scan=True(仅 login 子命令)→ 打开登录页,轮询到**服务端**确认为止。

    verify=False 可退回「只看 Cookie」的旧行为(仅供排障)。
    返回是否可用;失败时不做任何注入/绕过,由调用方给出人工引导。
    """
    acc = account_name(cfg)
    cookies = matched_login_cookies(context)
    need_scan = not cookies

    if cookies:
        state = verify_session(cfg, context, page) if verify else SESSION_OK
        if state == SESSION_OK:
            print(f"[auth] 账号 {acc}:已登录(服务端校验通过);profile 自动复用,"
                  "鉴权 Cookie: " + ", ".join(cookies) + ",无需重新扫码。")
            return True
        if state == SESSION_EXPIRED:
            need_scan = True
            print(f"[auth] 账号 {acc}:本地仍有鉴权 Cookie(" + ", ".join(cookies) + "),"
                  "但**服务端会话已失效**(接口返回未登录 / 页面要求扫码)。")
            print("[auth] 原因:鉴权 Cookie 的到期时间比会话实际寿命长,只看 Cookie 会误判成已登录。")
        else:
            print(f"[auth] 账号 {acc}:警告 — 无法向服务端确认登录态(网络或接口异常),"
                  "暂按已有 Cookie 继续;若随后报「未登录」,请重新扫码。")
            return True

    if not need_scan:
        return False

    if not wait_for_scan:
        print(f"[auth] 需要重新扫码登录。请先运行:"
              f" python main.py login{account_hint(cfg)}", file=sys.stderr)
        return False

    login_url = cfg.get("browser", {}).get("login_url", "https://eos.douyin.com/")
    total = wait_seconds or int(cfg.get("browser", {}).get("wait_login_seconds", 1800))
    print(f"[auth] 账号 {acc}:请重新扫码登录,马上打开登录页。")
    print(f"[auth] 登录地址: {login_url}")
    print("[auth] 请在浏览器窗口里扫码 / 登录这个账号(需对目标直播间有查看权限);"
          "要再挂一个账号请另起账号名: python main.py login --account <名字>")
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
            # Cookie 出现还不够,必须服务端确认(修复前这里就是误报的来源)
            state = verify_session(cfg, context, page) if verify else SESSION_OK
            if state == SESSION_OK:
                print(f"[auth] 账号 {acc} 登录成功(服务端校验通过;鉴权 Cookie: "
                      + ", ".join(matched_login_cookies(context)) + ")。")
                return True
        now = time.time()
        if now - last_report >= 15:
            remaining = int(deadline - now)
            print(f"[auth] 仍在等待人工登录… 剩余约 {remaining} 秒。")
            last_report = now
        page.wait_for_timeout(5000)

    print(f"[auth] 等待超时,未检测到账号 {acc} 的有效登录。", file=sys.stderr)
    return False


def perform_login(cfg: dict) -> int:
    """login 子命令主体:打开会话并等待人工登录,返回进程退出码。"""
    acc = account_name(cfg)
    profile_dir = resolve_profile_dir(cfg)
    if profile_exists(cfg):
        print(f"[auth] 账号 {acc} 的 profile 已存在: {profile_dir}")
        print("[auth] 将复用该会话;若要换成另一个账号,请用别的账号名"
              "(python main.py login --account <名字>),或删掉本目录后重登。")

    pw, context, page = open_session(cfg, headful=True)
    try:
        ok = ensure_logged_in(cfg, context, page, wait_for_scan=True)
        if ok:
            record_login(cfg, page)
            print("[auth] 登录态已就绪并持久化于本地 profile(不入库、不导出)。")
            print("[auth] 后续 probe / export / daily 会复用本会话(如: "
                  f"python main.py daily{account_hint(cfg)});会话过期时重跑 login 即可。")
            return 0
        print("[auth] 未在限定时间内完成登录,请重试: "
              f"python main.py login{account_hint(cfg)}", file=sys.stderr)
        return 2
    finally:
        try:
            context.close()
        finally:
            pw.stop()
