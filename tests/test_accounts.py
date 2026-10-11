# -*- coding: utf-8 -*-
"""多账号切换离线回归(不联网、不打开浏览器、不需要登录)。

覆盖:
  1. 账号名校验:空/None → 默认账号;非法字符、过长、Windows 保留设备名一律 AccountError;
  2. 默认账号**不改动** browser.profile_dir(老用户零迁移,行为与升级前完全一致);
  3. 具名账号 = <accounts_dir>/<name>/browser_profile;
  4. 优先级:--account > config.browser.account > 默认账号;
  5. account_paths 只算路径、不建目录(校验失败不得留下任何目录);
  6. 账号登记(meta)读写往返:昵称不被空值覆盖、first_login_at 保留;
  7. list_accounts:默认账号恒在首位,并能发现目录 / 孤立登记文件;
  8. .gitignore 必须忽略 data/accounts/(该目录含登录凭据);
  9. CLI:main.py accounts 可列账号;六个子命令 --help 均有 --account;
     非法账号名在所有入口都以退出码 2 拒绝且**不进入浏览器**。

运行:python tests/test_accounts.py
"""
import copy
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import accounts as acct  # noqa: E402
import config as cfgmod  # noqa: E402

ACC = "spa2"


def _cfg(tmpdir) -> dict:
    cfg = copy.deepcopy(cfgmod.DEFAULT_CONFIG)
    cfg["browser"]["accounts_dir"] = str(tmpdir)
    return cfg


def _raises_account_error(fn, *a, **kw) -> bool:
    try:
        fn(*a, **kw)
    except acct.AccountError:
        return True
    return False


def _cli(*argv):
    return subprocess.run([sys.executable, "main.py", *argv], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def test_name_normalization():
    """空/None/大小写形式的 default 都归一为默认账号;正常名字原样保留。"""
    assert acct.normalize_account_name(None) == "default"
    assert acct.normalize_account_name("") == "default"
    assert acct.normalize_account_name("   ") == "default"
    assert acct.normalize_account_name("default") == "default"
    assert acct.normalize_account_name("Default") == "default"
    assert acct.normalize_account_name(" DEFAULT ") == "default"
    assert acct.normalize_account_name(" spa2 ") == "spa2"
    assert acct.normalize_account_name("spa2_live-01.bak") == "spa2_live-01.bak"
    assert acct.normalize_account_name("A1") == "A1"


def test_invalid_names_rejected():
    """非法账号名必须抛 AccountError(不能静默回退到默认账号,否则会串号)。"""
    for bad in ("a/b", "a\\b", "-x", "_x", ".x", "中文字", "a b", "a:b", "a" * 33, "x@y"):
        assert _raises_account_error(acct.normalize_account_name, bad), bad
    for reserved in ("con", "CON", "com1", "lpt9", "nul.txt", "aux.bak"):
        assert _raises_account_error(acct.normalize_account_name, reserved), reserved


def test_default_account_keeps_profile_dir():
    """默认账号不得改写 browser.profile_dir(否则升级后老用户会"突然未登录")。"""
    with tempfile.TemporaryDirectory() as td:
        cfg = _cfg(td)
        before = cfg["browser"]["profile_dir"]
        assert acct.apply_account(cfg, None) == "default"
        assert cfg["browser"]["profile_dir"] == before
        assert cfg["browser"]["account"] == "default"
        assert acct.apply_account(cfg, "default") == "default"
        assert cfg["browser"]["profile_dir"] == before


def test_named_account_uses_own_profile_dir():
    """具名账号 → <accounts_dir>/<name>/browser_profile,且与默认目录不同。"""
    with tempfile.TemporaryDirectory() as td:
        cfg = _cfg(td)
        default_dir = pathlib.Path(acct.account_paths(cfg, None)[0])
        assert acct.apply_account(cfg, ACC) == ACC
        prof, meta = acct.account_paths(cfg, ACC)
        assert pathlib.Path(cfg["browser"]["profile_dir"]) == prof
        assert prof.name == "browser_profile"
        assert prof.parent.name == ACC
        assert prof != default_dir
        assert meta == pathlib.Path(td) / ACC / "account.json"
        assert cfg["browser"]["account"] == ACC


def test_cli_beats_config_account():
    """优先级:--account > config.browser.account > 默认账号。"""
    with tempfile.TemporaryDirectory() as td:
        cfg = _cfg(td)
        cfg["browser"]["account"] = "fromyaml"
        assert acct.resolve_account_name(cfg, "fromcli") == "fromcli"
        assert acct.resolve_account_name(cfg, None) == "fromyaml"
        # apply_account 会把最终账号写回 cfg(后续 print/日志都读它)
        assert acct.apply_account(cfg, "fromcli") == "fromcli"
        assert cfg["browser"]["account"] == "fromcli"
        cfg["browser"]["account"] = ""
        assert acct.resolve_account_name(cfg, None) == "default"
        assert acct.resolve_account_name(cfg, "  ") == "default"
        assert acct.apply_account(cfg) == "default"
        assert cfg["browser"]["account"] == "default"


def test_account_paths_do_not_create_dirs():
    """只算路径不建目录;非法名字也不得留下目录。"""
    with tempfile.TemporaryDirectory() as td:
        cfg = _cfg(td)
        prof, meta = acct.account_paths(cfg, ACC)
        assert not prof.exists() and not meta.exists()
        assert not pathlib.Path(td).exists() or not any(pathlib.Path(td).iterdir())
        assert _raises_account_error(acct.apply_account, cfg, "a/b")
        assert not (pathlib.Path(td) / "a").exists()


def test_meta_roundtrip():
    """登记文件往返:记录昵称/时间;再次写时昵称不被空值覆盖、首次时间保留。"""
    with tempfile.TemporaryDirectory() as td:
        cfg = _cfg(td)
        assert acct.read_account_meta(cfg, ACC) == {}
        p = acct.write_account_meta(cfg, ACC, nickname="方寸自在SPA")
        assert p.exists()
        meta = acct.read_account_meta(cfg, ACC)
        assert meta["account"] == ACC and meta["nickname"] == "方寸自在SPA"
        assert meta["logged_in_at"] and meta["first_login_at"]
        first = meta["first_login_at"]
        acct.write_account_meta(cfg, ACC, nickname="")
        meta2 = acct.read_account_meta(cfg, ACC)
        assert meta2["nickname"] == "方寸自在SPA"
        assert meta2["first_login_at"] == first
        assert acct.read_account_meta(cfg, "nope") == {}


def test_list_accounts_default_first():
    """默认账号恒在首位;能发现目录与孤立登记文件;ready 反映 profile 是否有内容。"""
    with tempfile.TemporaryDirectory() as td:
        cfg = _cfg(td)
        prof, _ = acct.account_paths(cfg, ACC)
        prof.mkdir(parents=True)
        (prof / "Cookies").write_text("x", encoding="utf-8")
        (pathlib.Path(td) / "spa3.json").write_text("{}", encoding="utf-8")
        rows = acct.list_accounts(cfg)
        assert rows[0]["account"] == "default"
        names = [r["account"] for r in rows]
        assert ACC in names and "spa3" in names
        by = {r["account"]: r for r in rows}
        assert by[ACC]["ready"] is True and by["spa3"]["ready"] is False


def test_gitignore_covers_accounts_dir():
    """.gitignore 必须忽略账号目录(里面有登录凭据)。"""
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/accounts/" in text
    assert "data/browser_profile/" in text


def test_config_defaults():
    """配置默认值:accounts_dir 与 account 均存在且为空账号(默认账号)。"""
    b = cfgmod.DEFAULT_CONFIG["browser"]
    assert b["accounts_dir"] == "data/accounts"
    assert b["account"] == ""
    assert "accounts_dir" in (ROOT / "config.example.yaml").read_text(encoding="utf-8")


def test_cli_accounts_command():
    """python main.py accounts:退出码 0,明确列出默认账号。"""
    r = _cli("accounts")
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, f"{r.returncode}\n{out}"
    assert "default" in out and "账号" in out


def test_cli_help_has_account_flag():
    """六个子命令的 --help 都要出现 --account。"""
    for sub in ("login", "probe", "export", "stats", "daily", "sessions"):
        r = _cli(sub, "--help")
        out = (r.stdout or "") + (r.stderr or "")
        assert r.returncode == 0, f"{sub} --help rc={r.returncode}\n{out}"
        assert "--account" in out, f"{sub} --help 缺少 --account"


def test_cli_invalid_account_rejected_without_browser():
    """非法账号名:所有入口退出码 2 并给出中文原因,且不创建任何目录。"""
    cases = [
        ("login", "--account", "a/b"),
        ("sessions", "--date", "09-04", "--account", "a/b"),
        ("stats", "--date", "09-04", "--account", "a/b"),
        ("probe", "--account", "a/b"),
        ("daily", "--date", "09-04", "--account", "a/b"),
        ("export", "--room-id", "7000000000000000001", "--date", "09-04", "--account", "a/b"),
    ]
    for argv in cases:
        r = _cli(*argv)
        out = (r.stdout or "") + (r.stderr or "")
        assert r.returncode == 2, f"{argv} rc={r.returncode}\n{out}"
        assert "账号名不合法" in out, f"{argv} 未给出中文原因:\n{out}"
        assert "未找到 playwright" not in out, f"{argv} 竟然走到了浏览器:\n{out}"
    assert not (ROOT / "data" / "accounts" / "a").exists()


def test_watch_capture_cli_invalid_account_rejected():
    """watch_capture 的 --account 与主 CLI 同一口径(非法名不进入浏览器)。"""
    r = subprocess.run([sys.executable, "-m", "exporter.watch_capture", "--room-id",
                        "7000000000000000001", "--date", "09-04", "--account", "a/b"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 2, f"{r.returncode}\n{out}"
    assert "账号名不合法" in out, out


def _run_all():
    tests = [(n, fn) for n, fn in sorted(globals().items())
             if n.startswith("test_") and callable(fn)]
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
    print(f"\n全部 {len(tests)} 个多账号用例通过")


if __name__ == "__main__":
    _run_all()
