# -*- coding: utf-8 -*-
"""auth 登录判定离线回归(不依赖浏览器/网络/PyPI)。

背景(修复的真实缺陷):
  抖音在"打开登录页但尚未登录"时就会种下 passport_csrf_token 等**匿名** Cookie;
  该名字曾被列入 auth._LOGIN_COOKIE_HINTS,于是 "python main.py login" 在用户
  还没扫码的情况下数秒内就打印"登录成功"并以退出码 0 结束;随后所有需要登录的
  命令都失败(实测 room/paged 返回 status_code=8 msg=用户未登录)。

判定契约(本用例锁定):
  - 只有**登录后才会种下**的鉴权 Cookie(sessionid/sessionid_ss/sid_tt/uid_tt/
    sid_guard)才算已登录;
  - 匿名 Cookie(passport_csrf_token 等)一律不算;
  - 非 douyin 域的 Cookie 一律不算;
  - 读取 Cookie 抛异常时按"未登录"处理(不误报)。

运行:python tests/test_auth_login_detect.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import auth  # noqa: E402

ANONYMOUS = [
    "passport_csrf_token",
    "passport_csrf_token_default",
    "ttwid",
    "s_v_web_id",
    "csrf_session_id",
    "biz_trace_id",
    "gfkadpd",
    "kura_cloud_uid",
]
AUTH_COOKIES = ["sessionid", "sessionid_ss", "sid_tt", "uid_tt", "sid_guard"]


class _Ctx:
    """最小 playwright BrowserContext 替身:只需 .cookies()。"""

    def __init__(self, cookies):
        self._cookies = cookies

    def cookies(self):
        return self._cookies


def _ck(name, domain=".douyin.com"):
    return {"name": name, "domain": domain, "value": "redacted"}


def test_anonymous_cookies_are_not_logged_in():
    """核心回归:只有匿名 Cookie 时必须判为未登录(修复前此处会误判为已登录)。"""
    ctx = _Ctx([_ck(n) for n in ANONYMOUS])
    assert auth.session_available({}, ctx) is False, "匿名 Cookie 不得判为已登录"
    assert auth.matched_login_cookies(ctx) == []


def test_hint_set_has_no_anonymous_names():
    """防止把匿名 Cookie 名字重新加回判定集合。"""
    forbidden = set(ANONYMOUS) & set(auth._LOGIN_COOKIE_HINTS)
    assert not forbidden, f"匿名 Cookie 不得出现在 _LOGIN_COOKIE_HINTS: {sorted(forbidden)}"


def test_each_real_auth_cookie_is_detected():
    for name in AUTH_COOKIES:
        ctx = _Ctx([_ck("ttwid"), _ck("passport_csrf_token"), _ck(name)])
        assert auth.session_available({}, ctx) is True, f"{name} 应判为已登录"
        assert auth.matched_login_cookies(ctx) == [name]


def test_non_douyin_domain_cookie_ignored():
    ctx = _Ctx([_ck("sessionid", "example.com"), _ck("sid_tt", "notdouyin.com")])
    assert auth.session_available({}, ctx) is False


def test_cookie_read_failure_treated_as_not_logged_in():
    class _Boom:
        def cookies(self):
            raise RuntimeError("boom")

    assert auth.session_available({}, _Boom()) is False


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
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
    print(f"\n全部 {len(tests)} 个 auth 判定用例通过")


if __name__ == "__main__":
    _run_all()
