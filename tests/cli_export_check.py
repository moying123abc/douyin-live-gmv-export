# -*- coding: utf-8 -*-
"""M2 CLI 层核验:1) 负向夹具导出必须被校验拦截(不产出文件);2) 正向 CSV 内容断言。"""
import csv
import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "outputs" / "_cli_selftest"
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "data" / "samples" / "sample_ended_session_60min.json"
GOOD_CSV = OUT / "good.csv"
BAD_JSON = OUT / "bad_sum.json"
BAD_CSV = OUT / "bad_sum.csv"

if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)

# ---- 负向:把累计值改错,导出必须拒绝且不写 CSV ----
data = json.loads(FIXTURE.read_text(encoding="utf-8-sig"))
data["cumulative_total"] = 987654.0
BAD_JSON.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
bad = subprocess.run(
    [sys.executable, "main.py", "export", "--fixture", str(BAD_JSON), "--out", str(BAD_CSV)],
    cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
)
assert bad.returncode == 3, f"预期 exit 3,实际 {bad.returncode}:\n{bad.stdout}\n{bad.stderr}"
assert not BAD_CSV.with_suffix(".csv").exists() or not pathlib.Path(str(BAD_CSV) + ".csv").exists()
assert "校验未通过" in bad.stderr or "求和" in bad.stderr, bad.stderr
print("NEGATIVE-CLI: 求和错误被拦截(exit 3,无输出文件)")

# ---- 正向:生成 CSV 并断言表头/行数/0 分钟保留 ----
good = subprocess.run(
    [sys.executable, "main.py", "export", "--fixture", str(FIXTURE), "--out", str(GOOD_CSV)],
    cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
)
assert good.returncode == 0, f"预期 exit 0,实际 {good.returncode}:\n{good.stdout}\n{good.stderr}"
assert "校验通过" in good.stdout
csv_path = GOOD_CSV  # --out 以 .csv 结尾 → to_csv_excel 原样落盘
assert csv_path.is_file(), f"CSV 未生成: {csv_path}"
with csv_path.open(encoding="utf-8-sig", newline="") as fh:
    reader = csv.reader(fh)
    table = list(reader)
header = table[0]
body = table[1:]
assert header == ["room_id", "session_date", "time", "gmv_min"], header
assert len(body) == 61, f"行数 {len(body)} != 61"
body_rows = [dict(zip(header, r)) for r in body]
zeros = [r["time"] for r in body_rows if float(r["gmv_min"]) == 0.0]
assert sorted(zeros) == ["07-01 19:00", "07-01 19:07", "07-01 19:30", "07-01 20:00"], zeros
assert all(r["room_id"] == "123456789" and r["session_date"] == "2025-07-01" for r in body_rows[:3])
print("POSITIVE-CLI: CSV 表头/61 行/0 分钟保留/元信息列 全部断言通过")

# BOM 检查
raw = csv_path.read_bytes()
assert raw.startswith(b"\xef\xbb\xbf"), "缺少 UTF-8 BOM"
print("BOM-CLI: UTF-8 BOM present [ok]")

# ---- 负向(t6 repair):时间列混用 MM-DD 字符串与 epoch 秒 → exit3 干净拒绝,无 traceback ----
mixed_payload = json.loads(FIXTURE.read_text(encoding="utf-8-sig"))
mixed_payload["rows"][30]["time"] = 1751365800  # epoch 秒(aware)混入 MM-DD 字符串(naive)
MIXED_JSON = OUT / "mixed_time.json"
MIXED_CSV = OUT / "mixed_time.csv"
MIXED_JSON.write_text(json.dumps(mixed_payload, ensure_ascii=False), encoding="utf-8")
mixed = subprocess.run(
    [sys.executable, "main.py", "export", "--fixture", str(MIXED_JSON), "--out", str(MIXED_CSV)],
    cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
)
assert mixed.returncode == 3, f"预期 exit 3,实际 {mixed.returncode}:\n{mixed.stdout}\n{mixed.stderr}"
assert "Traceback" not in mixed.stderr and "Traceback" not in mixed.stdout, "出现 traceback 崩溃"
assert "拒绝输出" in mixed.stderr
assert not pathlib.Path(str(MIXED_CSV) + ".csv").exists() and not MIXED_CSV.exists()
print("MIXED-TIME-CLI: 混用时间被干净拒绝(exit3,无 traceback,无输出文件)")
