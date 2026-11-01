# -*- coding: utf-8 -*-
"""服务端登录态校验的离线回归(不依赖浏览器/网络/登录态)。

背景(修复的真实缺陷,2026-11-01 真机实测):
  抖音的鉴权 Cookie(sessionid/sid_tt/uid_tt/sessionid_ss/sid_guard)到期时间
  远长于服务端会话的实际寿命。实测账号 1:5 个 Cookie 全在、sessionid 还剩 39 天,
  但 /dp/liveScreen 被重定向到 /livesite/login,room/paged 返回 status_code=8
  msg='用户未登录'。旧实现只看 Cookie 存在性 → login 几秒内误报"成功"却不打开
  扫码窗口,用户无法自救,直到 daily 导出才失败。

判定契约(本用例锁定):
  - Cookie 只是预筛,必须再由服务端确认(verify_session);
  - 服务端明确 ok    → 复用会话;
  - 服务端明确过期  → 走扫码流程(不能让用户去删 profile);
  - 无法确认        → 不拦人,但也不算"已登录"(警告后按旧行为继续);
  - 匿名 Cookie 不算已登录的不变量由 tests/test_auth_login_detect.py 继续守护。

运行:python tests/test_auth_session_verify.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import auth  # noqa: E402

LOGIN_URL = "https://eos.douyin.com/"
AUTH_COOKIES = ["sessionid", "sessionid_ss", "sid_tt", "uid_tt", "sid_guard"]


# --------------------------------------------------------------------------
# 最小替身
# --------------------------------------------------------------------------
class _Ctx:
    def __init__(self, cookies=None, request=None):
        self._cookies = cookies or []
        self.request = request

    def cookies(self):
        return self._cookies


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Req:
    """假 APIRequestContext:返回预设 payload 或抛预设异常。"""

    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if isinstance(self._payload, Exception):
            raise self._payload
        return _Resp(self._payload)


class _Page:
    def __init__(self, url="https://eos.douyin.com/dp/liveScreen", text=""):
        self.url = url
        self._text = text
        self.goto_calls = []
        self.waits = 0

    def goto(self, url, **kwargs):
        self.goto_calls.append(url)
        self.url = url

    def wait_for_timeout(self, ms):
        self.waits += 1

    def evaluate(self, js, *args):
        return self._text


def _ck(name, domain=".douyin.com"):
    return {"name": name, "domain": domain, "value": "redacted", "expires": 9999999999}


def _cfg():
    return {"browser": {"login_url": LOGIN_URL, "wait_login_seconds": 0},
            "probe": {"base_url": "https://eos.douyin.com/dp/liveScreen"}}


# --------------------------------------------------------------------------
# 1. 纯函数:探测信号归类
# --------------------------------------------------------------------------
def test_status_code_zero_is_ok():
    assert auth.classify_session_probe(status_code=0) == auth.SESSION_OK
    assert auth.classify_session_probe(status_code="0") == auth.SESSION_OK


def test_status_code_eight_is_expired():
    assert auth.classify_session_probe(status_code=8) == auth.SESSION_EXPIRED
    assert auth.classify_session_probe(status_code="8") == auth.SESSION_EXPIRED


def test_login_url_is_expired():
    url = "https://eos.douyin.com/livesite/login?from=liveScreen"
    assert auth.classify_session_probe(url=url) == auth.SESSION_EXPIRED


def test_login_text_is_expired():
    # 实测登录页文案:「抖音扫码登录 / 如何扫码 / 验证码登录 / 获取验证码 / 登录」
    for text in ("抖音扫码登录", "登录已过期,请重新登录", "验证码登录"):
        assert auth.classify_session_probe(text=text) == auth.SESSION_EXPIRED, text


def test_ok_status_wins_over_page_signals():
    """状态码已确认登录时,页面还在加载/残留登录文案不应把结论翻成过期。"""
    url = "https://eos.douyin.com/livesite/login"
    assert auth.classify_session_probe(status_code=0, url=url, text="扫码登录") == auth.SESSION_OK


def test_unknown_when_nothing_conclusive():
    assert auth.classify_session_probe() == auth.SESSION_UNKNOWN
    assert auth.classify_session_probe(status_code=10003) == auth.SESSION_UNKNOWN
    assert auth.classify_session_probe(url="https://eos.douyin.com/dp/liveScreen") == auth.SESSION_UNKNOWN


def test_permission_error_plus_login_url_is_expired():
    assert (auth.classify_session_probe(status_code=10003,
                                        url="https://eos.douyin.com/livesite/login")
            == auth.SESSION_EXPIRED)


# --------------------------------------------------------------------------
# 2. 探针:一次 GET,不渲染页面
# --------------------------------------------------------------------------
def test_probe_reads_status_code():
    ctx = _Ctx(request=_Req({"status_code": 8, "status_msg": "用户未登录"}))
    code, detail = auth.probe_session_status(_cfg(), ctx)
    assert code == 8, code
    assert "未登录" in detail


def test_probe_failure_is_none_not_exception():
    ctx = _Ctx(request=_Req(RuntimeError("network down")))
    code, detail = auth.probe_session_status(_cfg(), ctx)
    assert code is None
    assert "探测请求失败" in detail


def test_verify_session_expired_without_touching_page():
    ctx = _Ctx(request=_Req({"status_code": 8}))
    page = _Page()
    assert auth.verify_session(_cfg(), ctx, page) == auth.SESSION_EXPIRED
    assert page.goto_calls == [], "状态码已给出结论时不应再渲染页面"


def test_verify_session_ok_without_touching_page():
    ctx = _Ctx(request=_Req({"status_code": 0}))
    page = _Page()
    assert auth.verify_session(_cfg(), ctx, page) == auth.SESSION_OK
    assert page.goto_calls == []


def test_verify_session_unknown_when_request_raises():
    ctx = _Ctx(request=_Req(RuntimeError("boom")))
    page = _Page()
    assert auth.verify_session(_cfg(), ctx, page) == auth.SESSION_UNKNOWN
    assert page.goto_calls == []


# --------------------------------------------------------------------------
# 3. ensure_logged_in 的分支行为(注入假 verifier,不联网)
# --------------------------------------------------------------------------
def _patch_verify(monkeypatch_state, verdicts):
    """把 auth.verify_session 换成按顺序返回 verdicts 的假函数。"""
    seq = list(verdicts)
    monkeypatch_state["calls"] = 0

    def fake(cfg, context, page=None):
        i = monkeypatch_state["calls"]
        monkeypatch_state["calls"] = i + 1
        return seq[i] if i < len(seq) else seq[-1]

    return fake


def _with_fake_verify(verdicts, fn):
    original = auth.verify_session
    state = {}
    auth.verify_session = _patch_verify(state, verdicts)
    try:
        return fn(state)
    finally:
        auth.verify_session = original


def test_cookies_plus_ok_reuses_session_without_opening_page():
    ctx = _Ctx([_ck(n) for n in AUTH_COOKIES])
    page = _Page()

    def run(_state):
        return auth.ensure_logged_in(_cfg(), ctx, page)

    assert _with_fake_verify([auth.SESSION_OK], run) is True
    assert page.goto_calls == [], "服务端确认已登录时不应打开页面"


def test_cookies_but_expired_fails_fast_for_batch_commands():
    """核心回归:修复前这里直接 return True(误报成功)。

    现在普通命令(export/daily/sessions…)必须**立刻**返回 False 并给出扫码指令,
    不能为了等扫码把批处理/GUI 挂住 30 分钟。
    """
    ctx = _Ctx([_ck(n) for n in AUTH_COOKIES])
    page = _Page()

    def run(_state):
        return auth.ensure_logged_in(_cfg(), ctx, page)

    assert _with_fake_verify([auth.SESSION_EXPIRED], run) is False
    assert page.goto_calls == [], "普通命令不应打开登录页空等人工"


def test_login_command_opens_scan_window_when_expired():
    """login 子命令(wait_for_scan=True)必须真的打开登录页,让用户能重新扫码。"""
    ctx = _Ctx([_ck(n) for n in AUTH_COOKIES])
    page = _Page()

    def run(_state):
        return auth.ensure_logged_in(_cfg(), ctx, page, wait_seconds=0, wait_for_scan=True)

    assert _with_fake_verify([auth.SESSION_EXPIRED], run) is False
    assert page.goto_calls == [LOGIN_URL], page.goto_calls


def test_cookies_but_unknown_is_not_blocking():
    """探测不出结论时不要拦人(网络抖动),但也不算确认成功。"""
    ctx = _Ctx([_ck(n) for n in AUTH_COOKIES])
    page = _Page()

    def run(_state):
        return auth.ensure_logged_in(_cfg(), ctx, page)

    assert _with_fake_verify([auth.SESSION_UNKNOWN], run) is True
    assert page.goto_calls == []


def test_scan_then_verified_succeeds():
    """扫码前服务端说未登录、扫码后说 0 → 必须能识别为成功(轮询看服务端而非 Cookie)。"""
    ctx = _Ctx([_ck(n) for n in AUTH_COOKIES])
    page = _Page()

    def run(_state):
        return auth.ensure_logged_in(_cfg(), ctx, page, wait_seconds=30, wait_for_scan=True)

    ok = _with_fake_verify([auth.SESSION_EXPIRED, auth.SESSION_EXPIRED,
                            auth.SESSION_OK], run)
    assert ok is True
    assert page.goto_calls == [LOGIN_URL]


def test_no_cookies_fails_fast_without_wait_for_scan():
    ctx = _Ctx([])
    page = _Page()

    def run(_state):
        return auth.ensure_logged_in(_cfg(), ctx, page)

    assert _with_fake_verify([auth.SESSION_OK], run) is False
    assert page.goto_calls == []


def test_no_cookies_login_command_waits_for_scan():
    ctx = _Ctx([])
    page = _Page()

    def run(_state):
        return auth.ensure_logged_in(_cfg(), ctx, page, wait_seconds=0, wait_for_scan=True)

    assert _with_fake_verify([auth.SESSION_OK], run) is False
    assert page.goto_calls == [LOGIN_URL]


def test_verify_disabled_falls_back_to_cookie_rule():
    """排障开关:verify=False 时恢复旧行为(只看 Cookie),不发起探测。"""
    ctx = _Ctx([_ck(n) for n in AUTH_COOKIES])
    page = _Page()

    def run(state):
        return auth.ensure_logged_in(_cfg(), ctx, page, verify=False)

    assert _with_fake_verify([auth.SESSION_EXPIRED], run) is True
    assert page.goto_calls == []


def test_anonymous_cookies_alone_never_count():
    """与 test_auth_login_detect.py 同一不变量:匿名 Cookie 不算已登录。"""
    ctx = _Ctx([_ck("passport_csrf_token"), _ck("ttwid")])
    page = _Page()

    def run(_state):
        return auth.ensure_logged_in(_cfg(), ctx, page)

    assert _with_fake_verify([auth.SESSION_OK], run) is False


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failures.append((name, exc))
            print(f"FAIL {name}: {exc}")
    if failures:
        print(f"\n{len(failures)}/{len(tests)} 用例失败")
        sys.exit(1)
    print(f"\n全部 {len(tests)} 个登录态校验用例通过")


if __name__ == "__main__":
    _run_all()
