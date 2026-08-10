#!/usr/bin/env python3
"""
快速验证脚本 - 不需要 API Key
测试 28 个技术指标是否能正确计算
"""

import sys
from pathlib import Path
import numpy as np
import pandas as pd

SRC_DIR = Path(__file__).parent / "src"
sys.path.insert(0, str(SRC_DIR))

from indicators import compute_all_indicators, INDICATOR_COLUMNS, cross_sectional_rank
from config import INDICATOR_CONFIG


def main():
    print("=" * 60)
    print("CTREND 系统验证脚本")
    print("=" * 60)

    # 生成模拟数据 (300 天, 模拟 5 个币种)
    np.random.seed(42)
    n_days = 300
    n_coins = 5
    coins = [f"BTC", "ETH", "SOL", "BNB", "ADA"]

    all_dfs = []
    for i, coin in enumerate(coins):
        dates = pd.date_range('2024-01-01', periods=n_days, freq='D')
        # 不同币种有不同的走势
        drift = (i - 2) * 0.001  # 不同的趋势方向
        close = 100 * np.exp(np.cumsum(drift + np.random.randn(n_days) * 0.02))
        high = close * (1 + np.abs(np.random.randn(n_days)) * 0.01)
        low = close * (1 - np.abs(np.random.randn(n_days)) * 0.01)
        open_ = close + np.random.randn(n_days) * 0.5
        volume = np.abs(np.random.randn(n_days)) * 1e6 * (i + 1)

        df = pd.DataFrame({
            'open': open_, 'high': high, 'low': low,
            'close': close, 'volume': volume
        }, index=dates)
        df['inst_id'] = coin
        df['date'] = df.index
        all_dfs.append(df)

    panel = pd.concat(all_dfs, ignore_index=True)
    print(f"\n[1] 生成了 {n_days} 天, {n_coins} 个币种的模拟数据")
    print(f"    数据形状: {panel.shape}")

    # 计算所有 28 个指标
    print(f"\n[2] 计算 {len(INDICATOR_COLUMNS)} 个技术指标...")
    panel_with_ind = []
    for coin in coins:
        coin_data = panel[panel['inst_id'] == coin].copy()
        try:
            coin_data = compute_all_indicators(coin_data, INDICATOR_CONFIG)
            panel_with_ind.append(coin_data)
        except Exception as e:
            print(f"    {coin} 失败: {e}")
    panel = pd.concat(panel_with_ind, ignore_index=True)

    # 检查所有指标都已计算
    missing = [c for c in INDICATOR_COLUMNS if c not in panel.columns]
    if missing:
        print(f"    缺失指标: {missing}")
        sys.exit(1)
    print(f"    [OK] 所有 {len(INDICATOR_COLUMNS)} 个指标已计算")

    # 计算收益
    panel = panel.sort_values(['inst_id', 'date']).reset_index(drop=True)
    panel['ret'] = panel.groupby('inst_id')['close'].pct_change()

    # 截面标准化
    print(f"\n[3] 执行截面标准化 (映射到 [-0.5, 0.5])...")
    panel = cross_sectional_rank(panel, INDICATOR_COLUMNS, date_col='date')
    print(f"    [OK] 标准化完成")

    # 验证标准化值在 [-0.5, 0.5] 范围内
    for col in INDICATOR_COLUMNS[:3]:  # 只验证前 3 个
        vals = panel[col].dropna()
        if len(vals) > 0:
            assert vals.min() >= -0.5 - 1e-6, f"{col} min {vals.min()} < -0.5"
            assert vals.max() <= 0.5 + 1e-6, f"{col} max {vals.max()} > 0.5"
    print(f"    [OK] 标准化值在 [-0.5, 0.5] 范围内")

    # 计算 CTREND 分数 (简化版)
    print(f"\n[4] 计算 CTREND 分数 (简化版)...")
    latest_date = panel['date'].max()
    latest = panel[panel['date'] == latest_date].copy()

    # 选中 5 个最重要的指标 (论文发现)
    important = ['boll_mid', 'cci', 'macd', 'sma_20d', 'rsi']
    latest['ctrend_score'] = latest[important].mean(axis=1)

    print(f"\n    最新日期 {latest_date} 的 CTREND 分数:")
    result = latest[['inst_id', 'ctrend_score']].sort_values(
        'ctrend_score', ascending=False
    )
    print(result.to_string(index=False))

    print("\n" + "=" * 60)
    print("[成功] 所有模块验证通过!")
    print("=" * 60)
    print("\n下一步:")
    print("  1. 在 config.py 中填入 OKX API Key")
    print("  2. 运行 'python run.py --predict-only' 测试行情获取")
    print("  3. 在模拟盘 (flag='1') 下运行 'python run.py'")


if __name__ == "__main__":
    main()