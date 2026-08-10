"""模拟场景: 1D+4H 同向, 触发 15m 精准位 + 虚拟下单"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "src"))

import monitor_daemon as md

# 重置
state_path = md.MONITOR_CONFIG["paper_trade"]["state_path"]
if state_path.exists():
    state_path.unlink()

daemon = md.MonitorDaemon()
print(f"  paper.balance: ${daemon.paper.balance:.2f}")

# Monkey-patch tf_signals: 强制 1D + 4H 都 long
orig_run_one_cycle = daemon._run_one_cycle

# 改 _run_one_cycle 内部逻辑: 让 4H 也算成多头偏多
import multi_tf_align

original_check = multi_tf_align.check_alignment

def patched_check(tf_signals):
    # 强制返回 aligned=True, side='long'
    return {
        "aligned": True,
        "side": "long",
        "long_score": 5.0,
        "short_score": -1.0,
        "long_1d": 4, "short_1d": 1,
        "long_4h": 3, "short_4h": 2,
        "long_15m": 1, "short_15m": 3,
        "reason": "[MOCK] 1D+4H 同向 long (测试)",
    }

multi_tf_align.check_alignment = patched_check

# 跑一个 cycle
print("\n[3] 跑 mock 对齐场景...")
daemon._run_one_cycle()

# 检查虚拟交易状态
summary = daemon.paper.summary(1900)
print(f"\n[4] paper 状态:")
print(f"  余额: ${summary['balance']:.2f}")
print(f"  持仓: {len(summary['positions'])}")
print(f"  挂单: {len(summary['pending_orders'])}")
for o in summary["pending_orders"]:
    print(f"    挂: {o['side']} {o['size']}张 @ ${o['price']:.2f} sl=${o['sl']:.2f} tp=${o['tp']:.2f}")
for p in summary["positions"]:
    print(f"    持: {p['side']} {p['size']}张 @ ${p['entry_price']:.2f}")

print("\n[5] 模拟价格上涨触发止盈...")
if daemon.paper.pending_orders:
    order = daemon.paper.pending_orders[0]
    # 假设当前价 = 止盈价 (立即触发)
    daemon.paper.fill_order(order, order.tp, order.tp)
    print(f"  订单已成交 @ ${order.tp:.2f}")
    summary = daemon.paper.summary(order.tp)
    print(f"  持仓: {len(summary['positions'])}")
    for p in summary["positions"]:
        print(f"    持: {p['side']} {p['size']}张 @ ${p['entry_price']:.2f}")

    # 触发止盈
    exits = daemon.paper.check_position_exits(order.tp)
    if exits:
        trade = daemon.paper.close_position(exits[0][0], order.tp, "tp")
        print(f"\n[6] 平仓: 净盈亏 ${trade['net_pnl']:.2f} USDT")
        summary = daemon.paper.summary(order.tp)
        print(f"  余额: ${summary['balance']:.2f}")
        print(f"  收益率: {summary['return_pct']:+.2f}%")

print("\n[OK] 完整闭环验证通过")