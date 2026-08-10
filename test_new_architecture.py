"""
完整验证: 新架构 (L1/L2/L3 + DeepSeek强制15分钟)
"""
import sys, time, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "src"))

import monitor_daemon as md
import multi_tf_align as mta

# 重置状态
state_path = md.MONITOR_CONFIG["paper_trade"]["state_path"]
if state_path.exists():
    state_path.unlink()
(log_dir := md.MONITOR_CONFIG["log_dir"] / "ticks.jsonl").unlink(missing_ok=True)

print("[1] 创建 daemon...")
daemon = md.MonitorDaemon()
print(f"  interval_sec: {md.MONITOR_CONFIG['interval_sec']}s (因子)")
print(f"  ds_interval_sec: {md.MONITOR_CONFIG['ds_interval_sec']}s (DeepSeek)")
print(f"  tick_interval_sec: {md.MONITOR_CONFIG['tick_interval_sec']}s (TICK)")

print("\n[2] 测试多周期对齐 (L1/L2/L3)...")

# Case A: 全对齐 (L1)
mock_l1 = {
    "1D": [{"side": "long", "confidence": 0.5}] * 4,
    "4H": [{"side": "long", "confidence": 0.5}] * 3,
    "15m": [{"side": "long", "confidence": 0.5}] * 2,
}
align_l1 = mta.check_multi_level_alignment(mock_l1)
print(f"  L1 对齐: {align_l1['level']} {align_l1['side']} (score={align_l1['score']})")

# Case B: 4H+15m 对齐 (L2)
mock_l2 = {
    "1D": [{"side": "long", "confidence": 0.5}] * 2,
    "4H": [{"side": "short", "confidence": 0.5}] * 3,
    "15m": [{"side": "short", "confidence": 0.5}] * 3,
}
align_l2 = mta.check_multi_level_alignment(mock_l2)
print(f"  L2 对齐: {align_l2['level']} {align_l2['side']} (score={align_l2['score']})")

# Case C: 纯15m (L3)
mock_l3 = {
    "1D": [{"side": "long", "confidence": 0.5}] * 2,
    "4H": [{"side": "long", "confidence": 0.5}] * 2,
    "15m": [{"side": "long", "confidence": 0.5}] * 4,
}
align_l3 = mta.check_multi_level_alignment(mock_l3)
print(f"  L3 对齐: {align_l3['level']} {align_l3['side']} (score={align_l3['score']})")

# Case D: 全空 (none)
mock_none = {
    "1D": [{"side": "long", "confidence": 0.5}] * 2,
    "4H": [{"side": "short", "confidence": 0.5}] * 2,
    "15m": [{"side": "long", "confidence": 0.5}] * 1,
}
align_none = mta.check_multi_level_alignment(mock_none)
print(f"  None: {align_none['level']} {align_none['side']} ({align_none['alignment_reason']})")

print("\n[3] 测试 DeepSeek prompt 构建...")
import pandas as pd, numpy as np
ts = pd.date_range(end=pd.Timestamp.now(), periods=300, freq="15min")
prices = 1900 + np.cumsum(np.random.randn(300) * 2)
df_15m = pd.DataFrame({
    "ts": ts, "open": prices + 0.5, "high": prices + 2,
    "low": prices - 2, "close": prices, "volume": np.random.rand(300) * 100,
})
mock_sig = {
    "1D": [{"side": "long", "confidence": 0.5, "factor_name": "MACD", "entry_price": 1900, "sl": 1880, "tp": 1940, "rr": 2.0}],
    "4H": [{"side": "long", "confidence": 0.5, "factor_name": "RSI", "entry_price": 1895, "sl": 1875, "tp": 1935, "rr": 2.0}],
    "15m": [{"side": "long", "confidence": 0.5, "factor_name": "ATR", "entry_price": 1900, "sl": 1885, "tp": 1940, "rr": 2.0}],
}
align_mock = mta.make_aligned_decision(mock_sig, df_15m)
print(f"  对齐结果: level={align_mock['level']} {align_mock['side']} ep={align_mock['entry_price']:.2f}")
ds_input = mta.prepare_ds_input(mock_sig, align_mock, df_15m)
user_prompt = mta.build_ds_user_prompt(ds_input)
print(f"  DeepSeek prompt 长度: {len(user_prompt)} 字符")
print(f"  因子数据条目: {len(ds_input['factor_data'])}")
print(f"  K线条目: {len(ds_input['klines_15m_last10'])}")

print("\n[4] 跑因子循环...")
daemon._run_one_cycle()
print(f"  因子循环完成")

print("\n[5] 检查日志...")
log_files = sorted((md.ROOT / "data/monitor").glob("monitor_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
if log_files:
    lines = log_files[0].read_text(encoding="utf-8").splitlines()
    print(f"  最新日志 {log_files[0].name} ({len(lines)} 行):")
    for ln in lines[-10:]:
        print(f"    {ln}")

print("\n[6] 模拟 DeepSeek 强制调用...")
daemon._call_deepseek_forced()

print("\n[7] 检查 TICK 状态...")
tick_path = log_dir
if tick_path.exists():
    n = sum(1 for _ in open(tick_path))
    print(f"  TICK 行数: {n}")
    last = json.loads(list(open(tick_path))[-1])
    print(f"  最新: ${last['last']} bid={last['bid']} ask={last['ask']}")

print("\n[OK] 新架构验证通过")