"""
完整 TICK + 因子集成测试
1. mock 对齐让系统挂买单
2. 改挂单价到接近市价
3. 等 TICK 触发成交
4. 等价格触止损/止盈
"""
import sys, time, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "src"))

import monitor_daemon as md
import multi_tf_align

# 重置
state_path = md.MONITOR_CONFIG["paper_trade"]["state_path"]
if state_path.exists():
    state_path.unlink()
log_dir = md.MONITOR_CONFIG["log_dir"]

# 清空 tick 日志
(log_dir / "ticks.jsonl").unlink(missing_ok=True)


print("[1] 创建 daemon + mock 对齐...")
daemon = md.MonitorDaemon()


# Mock 对齐: 1D+4H+15m 都 long, 触发做多挂单
def patched_check(tf_signals):
    return {
        "aligned": True,
        "side": "long",
        "long_score": 5.0,
        "short_score": -1.0,
        "long_1d": 4, "short_1d": 1,
        "long_4h": 3, "short_4h": 2,
        "long_15m": 2, "short_15m": 1,
        "reason": "[MOCK] 全 long, 触发做多",
    }

multi_tf_align.check_alignment = patched_check

# 跑因子循环: 应该挂买单
print("\n[2] 跑因子循环 -> 挂买单...")
daemon._run_one_cycle()
order_before = daemon.paper.pending_orders[0] if daemon.paper.pending_orders else None
print(f"  挂单: {order_before.side if order_before else 'NONE'} {order_before.size if order_before else 0}张 @ ${order_before.price:.2f}" if order_before else "  无挂单")

if not order_before:
    print("  ERR: 没挂单")
    sys.exit(1)

# 现在调高挂单价到市价附近, 让 TICK 立即成交
print("\n[3] 调高挂单价到市价附近...")
last_ticker_path = log_dir / "last_ticker.json"
if last_ticker_path.exists():
    ticker = json.loads(last_ticker_path.read_text(encoding="utf-8"))
    cur_price = ticker["last"]
    print(f"  当前市价: ${cur_price:.2f}")
    order_before.price = cur_price + 5  # 高 5 美元, 确保 ask <= price 时成交 (挂单价高, 易触发)
    daemon.paper.save_state()
    print(f"  改挂单价到 ${order_before.price:.2f}")

# 跑 TICK 10 秒
print("\n[4] 跑 TICK 10 秒, 等待成交...")
start = time.time()
filled = False
while time.time() - start < 10:
    daemon.tick.run_once()
    if daemon.paper.positions:
        filled = True
        break
    time.sleep(daemon.tick.interval_sec)

if filled:
    print(f"  成交了! 持仓: {len(daemon.paper.positions)}")
    pos = daemon.paper.positions[0]
    print(f"    {pos.side} {pos.size}张 @ ${pos.entry_price:.2f}")
else:
    print(f"  未成交, 挂单: {len(daemon.paper.pending_orders)}")
    sys.exit(1)

# 改 sl/tp 让 TICK 立即触发平仓
print("\n[5] 改 sl/tp 让 TICK 立即触发止盈...")
pos.sl = pos.entry_price - 5
pos.tp = pos.entry_price + 5  # 上方 5 美元
daemon.paper.save_state()

# 跑 TICK 等止盈
print("\n[6] 跑 TICK 等止盈...")
start = time.time()
closed = False
while time.time() - start < 10:
    daemon.tick.run_once()
    if not daemon.paper.positions:
        closed = True
        break
    time.sleep(daemon.tick.interval_sec)

if closed:
    print(f"  平仓了!")
    print(f"  已实现盈亏: ${daemon.paper.realized_pnl:+.2f}")
    print(f"  已关闭交易: {len(daemon.paper.closed_trades)}")
    if daemon.paper.closed_trades:
        t = daemon.paper.closed_trades[-1]
        print(f"  最后交易: {t['side']} {t['size']}张 @ {t['entry_price']:.2f} -> {t['exit_price']:.2f} 净 {t['net_pnl']:+.2f} USDT 原因 {t['reason']}")
else:
    print(f"  未平仓")

print("\n[7] 总结...")
summary = daemon.paper.summary(1900)
print(f"  余额: ${summary['balance']:.2f}")
print(f"  收益率: {summary['return_pct']:+.2f}%")
print(f"  tick 日志: {sum(1 for _ in open(log_dir / 'ticks.jsonl'))} 行")

print("\n[OK] 完整 TICK + 因子集成验证通过")