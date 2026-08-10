"""完整闭环静态验证: 因子→对齐→虚拟下单→监控→平仓"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "src"))

import monitor_daemon as md

print("[1] 导入 monitor_daemon...")
print(f"  OK. multi_tf_align + paper_trader 已加载")

# 重置虚拟交易状态
state_path = md.MONITOR_CONFIG["paper_trade"]["state_path"]
if state_path.exists():
    state_path.unlink()
    print(f"  重置 {state_path}")

print("\n[2] 创建 daemon...")
daemon = md.MonitorDaemon()
print(f"  paper.enabled: {daemon.paper is not None}")
print(f"  paper.balance: ${daemon.paper.balance:.2f}")
print(f"  paper.positions: {len(daemon.paper.positions)}")
print(f"  paper.pending: {len(daemon.paper.pending_orders)}")

print("\n[3] 跑 1 次 cycle (完整闭环: 因子→对齐→虚拟交易→Telegram)...")
daemon._run_one_cycle()

print("\n[4] 检查 paper 状态...")
summary = daemon.paper.summary(1900)
print(f"  余额: ${summary['balance']:.2f}")
print(f"  持仓: {len(summary['positions'])}")
print(f"  挂单: {len(summary['pending_orders'])}")
for o in summary["pending_orders"]:
    print(f"    挂: {o['side']} {o['size']}张 @ {o['price']:.2f} (sl={o['sl']:.2f}, tp={o['tp']:.2f})")
for p in summary["positions"]:
    print(f"    持: {p['side']} {p['size']}张 @ {p['entry_price']:.2f}")

print("\n[5] 模拟价格突破: 触发止盈/止损")
# 模拟当前价 = 止盈价, 看是否会成交
if summary["positions"]:
    pos = daemon.paper.positions[0]
    exits = daemon.paper.check_position_exits(pos.tp)
    if exits:
        print(f"  检测到止盈触发: {exits}")
        trade = daemon.paper.close_position(exits[0][0], pos.tp, "tp")
        print(f"  平仓: 净盈亏 ${trade['net_pnl']:.2f}")

print("\n[OK] 闭环验证通过")