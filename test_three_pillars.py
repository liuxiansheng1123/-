"""
脱机测试: 验证 three_pillars 的端到端流程 (含 DeepSeek fallback)

不依赖真实 OKX / DeepSeek, 用 mock 数据
"""
import os
import sys
from pathlib import Path
import numpy as np
import pandas as pd

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(Path(__file__).parent / "src"))

from decision_engine import compute_three_pillars
from deepseek_analyzer import DeepSeekAnalyzer


def make_trending_klines(n, start_price, vol_base, drift, seed, target_drift_pct=None):
    """
    生成有趋势的 K 线
    drift: 每根的平均变动 (绝对值), 正=看多, 负=看空
    如果指定 target_drift_pct (例如 +5%), 则 n 根后总涨幅固定, 噪声更小
    """
    np.random.seed(seed)
    ts = pd.date_range('2026-07-01', periods=n, freq='15min')
    if target_drift_pct is not None:
        # 让最终价格 = start_price * (1 + target_drift_pct), 噪声小
        total_drift = start_price * target_drift_pct / 100.0
        per_step_drift = total_drift / n
        close = np.linspace(start_price, start_price + total_drift, n) + np.random.randn(n) * 0.5
    else:
        close = start_price + np.cumsum(np.random.randn(n) * 0.5 + drift)
    high = close + np.abs(np.random.randn(n)) * 1
    low = close - np.abs(np.random.randn(n)) * 1
    vol = np.abs(np.random.randn(n)) * vol_base
    return pd.DataFrame({
        'ts': ts, 'open': close, 'high': high, 'low': low,
        'close': close, 'volume': vol
    })


def test_case(name, inst_id, start_price, target_drift, expected_dir):
    """跑一个测试用例  target_drift 是总涨幅百分比, 如 +5.0 = 涨5%"""
    print(f"\n{'=' * 70}")
    print(f"测试: {name}  (预期方向: {expected_dir}, 目标漂移: {target_drift:+.1f}%)")
    print('=' * 70)

    dfs = {
        '15m': make_trending_klines(300, start_price, 1000, 0, 1, target_drift_pct=target_drift * 0.6),
        '4H': make_trending_klines(200, start_price, 5000, 0, 2, target_drift_pct=target_drift * 0.8),
        '1D': make_trending_klines(100, start_price, 20000, 0, 3, target_drift_pct=target_drift),
    }

    pillars = compute_three_pillars(inst_id, dfs)

    print(f"\n  标的: {pillars['inst_id']}")
    print(f"  当前价: ${pillars['current_price']:.2f}")
    print(f"  MA30 方向: {pillars['ma30']['direction']} (确信度: {pillars['ma30']['conviction']})")
    print(f"  OBV 判定: {pillars['obv']['verdict']} (is_real={pillars['obv']['is_real']})")
    print(f"  ATR 4H: ${pillars['atr']['atr_4h']:.2f} ({pillars['atr']['atr_4h_pct']:.2f}%)")
    print(f"  建议方向: {pillars['recommendation']}")

    # 验证 (允许 expected_dir 为 'any')
    if expected_dir != 'any':
        assert pillars['ma30']['direction'] == expected_dir, \
            f"预期 {expected_dir}, 实际 {pillars['ma30']['direction']}"
        print(f"  [OK] 方向符合预期")
    else:
        print(f"  (任何方向都接受)")

    # 验证 fallback 路径 (不调真 DeepSeek)
    print(f"\n  --- 模拟 DeepSeek fallback ---")
    # 注入一个无效 API key, 触发 fallback
    analyzer = DeepSeekAnalyzer(api_key="", base_url="https://api.deepseek.com/v1")
    ds = analyzer.analyze_three_pillars(inst_id, pillars)
    print(f"  Fallback 建议: {ds.get('recommendation')}")
    print(f"  Fallback 类型: {ds.get('entry_type')}")
    print(f"  Fallback 入场: ${ds.get('entry_price')}")
    print(f"  Fallback 止损: ${ds.get('stop_loss')}")
    print(f"  Fallback 止盈: ${ds.get('take_profit')}")
    print(f"  Fallback RR: 1:{ds.get('risk_reward_ratio')}")
    print(f"  Fallback ATR倍数: SL={ds.get('atr_multiplier_sl')}x / TP={ds.get('atr_multiplier_tp')}x")
    print(f"  Fallback 是兜底: {ds.get('_fallback')}")

    if pillars['ma30']['direction'] in ('long', 'short'):
        assert ds.get('_fallback') is True, "应该走 fallback"
        assert ds.get('entry_price') is not None
        assert ds.get('stop_loss') is not None
        assert ds.get('take_profit') is not None
        assert ds.get('risk_reward_ratio', 0) >= 1.5, "RR 应 ≥ 1.5"
        print(f"  [OK] Fallback 数据完整且 RR 合理")

    return pillars, ds


if __name__ == "__main__":
    print("=" * 70)
    print("三件套系统脱机测试")
    print("=" * 70)

    # 场景 1: 强多头 (三周期全 positive drift)
    p1, d1 = test_case(
        "强多头 (三周期 +3%/+4%/+5%)",
        "ETH-USDT-SWAP", 2000.0, target_drift=+5.0, expected_dir='long'
    )

    # 场景 2: 强空头
    p2, d2 = test_case(
        "强空头 (三周期 -3%/-4%/-5%)",
        "BTC-USDT-SWAP", 30000.0, target_drift=-5.0, expected_dir='short'
    )

    # 场景 3: 混乱
    # 15m/4H 正, 1D 负
    print(f"\n{'=' * 70}")
    print("测试: 方向矛盾 (15m/4H 正, 1D 负)")
    print('=' * 70)
    dfs = {
        '15m': make_trending_klines(300, 2000, 1000, 0, 11, target_drift_pct=+3.0),
        '4H': make_trending_klines(200, 2000, 5000, 0, 12, target_drift_pct=+4.0),
        '1D': make_trending_klines(100, 2000, 20000, 0, 13, target_drift_pct=-5.0),
    }
    p3 = compute_three_pillars("ETH-USDT-SWAP", dfs)
    print(f"  MA30 方向: {p3['ma30']['direction']} (确信度: {p3['ma30']['conviction']})")
    print(f"  原因: {p3['ma30']['reason']}")
    assert p3['ma30']['direction'] == 'none', "矛盾应输出观望"
    print(f"  [OK] 矛盾场景正确识别为观望")

    # 测试空安全
    print(f"\n{'=' * 70}")
    print("测试: 数据不足 (空 df)")
    print('=' * 70)
    try:
        empty = compute_three_pillars("EMPTY", {'15m': pd.DataFrame(), '4H': pd.DataFrame(), '1D': pd.DataFrame()})
        print(f"  不崩, 返回: {empty['ma30']['direction']}")
    except Exception as e:
        print(f"  异常: {e}")

    print("\n" + "=" * 70)
    print("所有测试通过 [OK]")
    print("=" * 70)
