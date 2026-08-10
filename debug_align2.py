"""debug: 用 mock 数据强制 1D+4H 同向, 看 15m 精准位是否对"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from multi_tf_align import check_alignment, make_aligned_decision, compute_15m_precision


# Mock 因子信号: 1D + 4H 都是 long, 15m 是 short
mock_signals = {
    "1D": [
        {"factor_name": f"d{i}", "side": "long", "confidence": 0.5, "entry_price": 1800, "current_price": 1900}
        for i in range(5)
    ],
    "4H": [
        {"factor_name": f"h{i}", "side": "long", "confidence": 0.5, "entry_price": 1850, "current_price": 1900}
        for i in range(4)
    ],
    "15m": [
        {"factor_name": f"m{i}", "side": "short", "confidence": 0.5, "entry_price": 1910, "current_price": 1905}
        for i in range(3)
    ],
}

print("=== Mock: 1D+4H 都 long, 15m short ===")
align = check_alignment(mock_signals)
print(f"  aligned: {align['aligned']}")
print(f"  side: {align['side']}")
print(f"  reason: {align['reason']}")

# Mock 15m K 线
ts = pd.date_range(end=pd.Timestamp.now(), periods=300, freq="15min")
prices = 1900 + np.cumsum(np.random.randn(300) * 2)
df_15m = pd.DataFrame({
    "ts": ts,
    "open": prices + np.random.randn(300) * 0.5,
    "high": prices + np.abs(np.random.randn(300)) * 2,
    "low": prices - np.abs(np.random.randn(300)) * 2,
    "close": prices,
    "volume": np.random.rand(300) * 100,
})
print(f"\n=== 15m 数据: {len(df_15m)} 根, last_close={df_15m['close'].iloc[-1]:.2f} ===")

# 算 15m 精准位
precision = compute_15m_precision(df_15m, align["side"])
print(f"\n=== 15m 精准位 (side={align['side']}) ===")
for k, v in precision.items():
    print(f"  {k}: {v}")

# 完整决策
decision = make_aligned_decision(mock_signals, df_15m)
print(f"\n=== 完整决策 ===")
for k, v in decision.items():
    print(f"  {k}: {v}")

# 数学闭环校验
if decision.get("entry_price"):
    entry, sl, tp = decision["entry_price"], decision["stop_loss"], decision["take_profit"]
    cur = decision["current_price"]
    actual_gap = (entry - cur) / cur * 100
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    rr = reward / risk
    print(f"\n=== 数学自检 ===")
    print(f"  声明 gap: {decision['gap_pct']:.3f}%")
    print(f"  实际 gap: {actual_gap:.3f}%")
    print(f"  闭环: {'OK' if abs(decision['gap_pct'] - actual_gap) < 0.1 else 'FAIL'}")
    print(f"  声明 RR: {decision['risk_reward_ratio']:.3f}")
    print(f"  实际 RR: {rr:.3f}")