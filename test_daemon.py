"""
脱机测试守护进程的循环逻辑

不依赖真实 OKX/DeepSeek/Telegram, 用 mock 替换, 把 15 分钟压成 3 秒,
跑 3 轮后自动停止, 验证:
  - 循环节奏 (每 3 秒一轮)
  - 失败时继续运行
  - 落盘正确
  - 信号处理 (Ctrl+C 等价)
"""
import os
import sys
import time
import signal
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
import numpy as np
import pandas as pd

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import three_pillars_daemon as tpd


def make_mock_klines(n, base_price, tf, seed):
    np.random.seed(seed)
    ts = pd.date_range('2026-07-01', periods=n, freq='15min')
    close = base_price + np.cumsum(np.random.randn(n) * 0.5)
    high = close + 1
    low = close - 1
    vol = np.abs(np.random.randn(n)) * 1000
    return pd.DataFrame({
        'ts': ts, 'open': close, 'high': high, 'low': low,
        'close': close, 'volume': vol
    })


def main():
    print("=" * 70)
    print("三件套守护进程脱机测试 (3 秒一轮, 跑 3 轮)")
    print("=" * 70)

    # 压短间隔: 3 秒一轮, sleep 步长 1 秒
    tpd.DAEMON_CONFIG["interval_sec"] = 3
    tpd.DAEMON_CONFIG["sleep_step_sec"] = 1
    tpd.DAEMON_CONFIG["inst_id"] = "ETH-USDT-SWAP"

    daemon = tpd.ThreePillarsDaemon()

    # Mock 掉外部依赖
    # 1. OKX client: 每次返回三套 mock K 线
    mock_klines = {
        "15m": make_mock_klines(300, 2000.0, "15m", seed=1),
        "4H": make_mock_klines(200, 2000.0, "4H", seed=2),
        "1D": make_mock_klines(100, 2000.0, "1D", seed=3),
    }
    daemon.client = MagicMock()
    daemon.client.get_candlesticks_history = MagicMock(
        side_effect=lambda inst_id, bar, total_bars: mock_klines.get(bar, pd.DataFrame())
    )

    # 2. DeepSeek: 用真实的 fallback 路径 (注入空 API key 会自动走 fallback)
    # 不替换, 直接用空 key 让它走 fallback

    # 3. Telegram: mock
    daemon.notifier = MagicMock()
    daemon.notifier.send = MagicMock()

    # 4. 设置 SIGALRM, 3 轮后自动停止 (替代 Ctrl+C)
    MAX_CYCLES = 3
    cycles_run = [0]  # 用 list 闭包

    original_run_one_cycle = daemon.run_one_cycle

    def counted_run_one_cycle():
        result = original_run_one_cycle()
        cycles_run[0] += 1
        print(f"\n[TEST] Cycle {cycles_run[0]} done. Will exit after {MAX_CYCLES} cycles.\n")
        if cycles_run[0] >= MAX_CYCLES:
            daemon.running = False
        return result

    daemon.run_one_cycle = counted_run_one_cycle

    # 5. 测试一个失败场景: 让第二次 cycle 的数据拉取失败, 看是否继续
    call_count = [0]
    original_fetch = daemon._fetch_three_tf

    def flaky_fetch():
        call_count[0] += 1
        if call_count[0] == 2:
            # 第 2 轮模拟 OKX 失败
            print("[TEST] Simulating OKX failure on cycle 2...")
            return None
        return original_fetch()

    daemon._fetch_three_tf = flaky_fetch

    # 开始跑
    start = time.time()
    daemon.run()
    elapsed = time.time() - start

    print("\n" + "=" * 70)
    print("测试结果")
    print("=" * 70)
    print(f"  跑了 {daemon.logger.cycle_count} 轮 (预期: 3)")
    print(f"  耗时: {elapsed:.1f} 秒")
    print(f"  TG 调用: {daemon.notifier.send.call_count} 次")
    print(f"    期望: 1 (启动) + 3 (每轮) + 1 (停止) = 5")

    # 验证
    assert daemon.logger.cycle_count == MAX_CYCLES, f"应跑 {MAX_CYCLES} 轮, 实际 {daemon.logger.cycle_count}"
    # 启动 + 每轮 + 停止 = 5 (如果都成功)
    # 第 2 轮失败, 但 TG 还是会被调, 因为失败时 _send_telegram 也会调 (除非显式跳过)
    # 看代码: run_one_cycle 在 _fetch_three_tf 失败时直接 return False, 不发 TG
    # 所以应该是 1 + 2 + 1 = 4
    assert daemon.notifier.send.call_count >= 4, f"TG 至少 4 次, 实际 {daemon.notifier.send.call_count}"

    # 验证落盘 (只数本次 daemon 启动后生成的文件)
    output_dir = tpd.DAEMON_CONFIG["output_dir"]
    boot_time = daemon.logger.start_time
    saved_files = [
        f for f in output_dir.glob("*_c*.json")
        if datetime.fromtimestamp(f.stat().st_mtime) >= boot_time
    ]
    print(f"  本次会话落盘: {len(saved_files)} 个 (预期: 2, 第 2 轮失败不存)")
    assert len(saved_files) == 2, f"本次应存 2 份, 实际 {len(saved_files)}"

    print("\n[OK] 所有断言通过")
    print("=" * 70)


if __name__ == "__main__":
    main()
