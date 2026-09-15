# -*- coding: utf-8 -*-
"""config.py — 轻量配置加载(仅标准库依赖)。

设计约定:
- 默认配置内置在 DEFAULT_CONFIG(与 config.example.yaml 内容一致),项目根目录下
  存在 ``config.yaml`` 时加载并深度合并(文件值覆盖默认值,未提供的键保留默认)。
- 优先尝试 PyYAML(``import yaml``);若用户未安装,则回退到内置的 YAML 子集解析器
  ``_parse_subset_yaml`` —— 仅支持本项目 config 文件的子集:
  注释(#)、两级空格缩进嵌套映射、内联列表(``[a, b]``)与块状标量列表(``- item``)、
  常见标量(true/false/null/int/float/字符串)。字符串中若含 ``#`` 请用引号包裹。
- 所有相对路径键(如 profile_dir / output_dir)一律以本项目根目录为基准解析,
  与调用方当前工作目录无关。
- 不做任何敏感信息读取或写入;调用方负责不把凭据写进配置。
"""
from __future__ import annotations

import copy
import pathlib
import re
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# 默认配置(与 config.example.yaml 保持一致;修改其中一处请同步另一处)
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "browser": {
        "headful": True,
        "profile_dir": "data/browser_profile",
        "login_url": "https://eos.douyin.com/",
        "login_ok_url_prefix": "https://eos.douyin.com",
        "wait_login_seconds": 1800,
    },
    "probe": {
        "base_url": "https://eos.douyin.com/dp/liveScreen",
        "output_dir": "data/probe",
        "load_wait_seconds": 35,  # 分钟指标响应在页面加载中后期返回,默认多等一会(2026-09 实测)
        "scroll_wait_seconds": 3,
        "max_row_sample": 5,
        "network_url_hints": ["trend", "stats", "live", "room", "board", "gmv"],
        "try_l3": False,
    },
    # M2 导出(输出目录;结果不入库,见 .gitignore data/output*/)
    "export": {
        "out_dir": "data/outputs",
        # T12:回放页自定义指标保留集合(趋势图同屏 ≤6,成交金额 gmv 必留;可改)
        "watch_capture_keep_metrics": [
            "成交金额", "成交订单数", "在线人数", "进入人数",
            "直播间观看量", "千次观看成交金额",
        ],
    },
    # M2 内置校验(容差可配)
    "validation": {
        "sum_abs_tolerance": 1.0,     # 元:求和校验绝对容差
        "sum_rel_tolerance": 0.001,   # 相对累计成交金额(0.1%)
        "row_tolerance_minutes": 0,   # 行数校验容差(行);默认严格相等
    },
    # M3 扩展指标(订单数/在线人数等):默认关闭,不阻断成交金额导出。
    # 在取得“真机可得的分钟级序列”在线证据前禁止启用并表(避免伪造粒度);
    # 结论见 docs/指标可得性结论.md。
    "extra_metrics": {
        "enabled": False,             # 指标并表开关,默认关闭
        "column_ids": [],             # 并入列 id(如 order_min/online_uv_min),需先真机验证可得
    },
}


def setup_utf8_io() -> None:
    """重定向输出时把 stdout/stderr 重配为 UTF-8,避免 Windows GBK/UTF-8 混乱。

    交互式终端(真 tty)不改动,保持系统原生编码行为(Windows 控制台会用
    WriteConsoleW 正确处理 Unicode)。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------
def default_config() -> dict:
    """返回默认配置的深拷贝,避免调用方意外修改共享对象。"""
    return copy.deepcopy(DEFAULT_CONFIG)


def resolve_path(*parts: str) -> str:
    """把相对项目根(或绝对)的路径解析为绝对字符串。"""
    joined = pathlib.Path(*parts)
    if not joined.is_absolute():
        joined = PROJECT_ROOT / joined
    return str(joined)


def load_config(path=None) -> dict:
    """加载配置:path 缺省时尝试项目根下的 ``config.yaml``,找不到则用默认值。"""
    cfg = default_config()
    if path is None:
        candidate = PROJECT_ROOT / "config.yaml"
        if candidate.is_file():
            path = str(candidate)
        else:
            return cfg
    file_cfg = _read_yaml_file(path)
    return _deep_merge(cfg, file_cfg)


def _read_yaml_file(path) -> dict:
    p = pathlib.Path(path)
    if not p.is_file():
        print(f"[config] 找不到配置文件: {p} (使用默认配置)", file=sys.stderr)
        return {}
    try:
        text = p.read_text(encoding="utf-8-sig")
    except OSError as exc:
        print(f"[config] 读取配置失败: {exc} (使用默认配置)", file=sys.stderr)
        return {}
    try:
        import yaml  # type: ignore  # PyYAML(可选)
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError("配置顶层必须是映射")
        return data
    except ImportError:
        return _parse_subset_yaml(text)
    except Exception as exc:  # yaml 解析失败时回退到子集解析器
        print(f"[config] PyYAML 解析失败({exc}),回退到内置子集解析器", file=sys.stderr)
        return _parse_subset_yaml(text)


def _deep_merge(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


# ---------------------------------------------------------------------------
# YAML 子集解析器(仅标准库;注释中的能力边界必须与模块 docstring 一致)
# ---------------------------------------------------------------------------
def _strip_comment(line: str) -> str:
    """去掉行内注释:只处理不在单/双引号内的 '#'。"""
    out = []
    quote = None
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == quote:
                if i > 0 and line[i - 1] == "\\":
                    pass  # 转义引号,不闭合
                else:
                    quote = None
        else:
            if ch == "#":
                break
            if ch in "\"'":
                quote = ch
            out.append(ch)
        i += 1
    return "".join(out)


def _split_top(text: str, sep: str = ",") -> list:
    """按分隔符切分,忽略引号与方括号内的分隔符。"""
    parts, buf, depth, quote = [], [], 0, None
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        parts.append("".join(buf).strip())
    return parts


def _parse_scalar(text: str):
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(x) for x in _split_top(inner)]
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1]
    low = text.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~", ""):
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+([eE][+-]?\d+)?", text):
        return float(text)
    return text


def _parse_subset_yaml(text: str) -> dict:
    """把 YAML 子集文本解析为 dict。详见模块 docstring 的能力边界。"""
    lines = []
    for raw in text.splitlines():
        cleaned = _strip_comment(raw).rstrip()
        if not cleaned.strip():
            continue
        indent = len(cleaned) - len(cleaned.lstrip(" "))
        lines.append((indent, cleaned.strip()))
    data, _idx = _parse_block(lines, 0, 0 if not lines else lines[0][0])
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("config 顶层必须是映射(缩进从 0 开始)")
    return data


def _parse_block(lines, idx: int, indent: int):
    """解析从 lines[idx] 开始、基准缩进为 indent 的映射块;返回 (dict, 下一个 idx)。"""
    result: dict = {}
    while idx < len(lines):
        ind, content = lines[idx]
        if ind < indent:
            break
        if ind > indent:
            raise ValueError(f"非法缩进: 行 '{content}' (indent={ind} > {indent})")
        if content.startswith("-"):
            raise ValueError("顶层不支持块状序列,请使用 '- key:' 下的嵌套列表或内联 [a, b]")
        key, _, rest = content.partition(":")
        key = key.strip()
        if not key:
            raise ValueError(f"非法键行: '{content}'")
        rest = rest.strip()
        if rest == "":
            # 嵌套映射或块状列表
            idx += 1
            if idx < len(lines) and lines[idx][0] > indent:
                child_indent = lines[idx][0]
                if lines[idx][1].startswith("-"):
                    items, idx = _parse_block_list(lines, idx, child_indent)
                    result[key] = items
                else:
                    child, idx = _parse_block(lines, idx, child_indent)
                    result[key] = child
            else:
                result[key] = {}
        else:
            result[key] = _parse_scalar(rest)
            idx += 1
    return result, idx


def _parse_block_list(lines, idx: int, indent: int):
    """解析块状标量列表(- item);返回 (list, 下一个 idx)。"""
    items = []
    while idx < len(lines):
        ind, content = lines[idx]
        if ind < indent:
            break
        if ind > indent:
            raise ValueError(f"非法缩进(列表): '{content}'")
        if not content.startswith("-"):
            break
        item = content[1:].strip()
        if not item:
            raise ValueError(f"空列表项: 第 {idx + 1} 行附近")
        items.append(_parse_scalar(item))
        idx += 1
    return items, idx
