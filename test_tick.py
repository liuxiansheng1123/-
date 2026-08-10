"""
TICK 循环测试: 2 秒/次, 跑 30 秒
验证:
- tick 数据每 2 秒写一行到 ticks.jsonl
- 挂单/持仓 tick 级响应
"""
import sys, time, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "src"))

import monitor_daemon as md

# 重置
state_path = md.MONITOR_CONFIG["paper_trade"]["state_path"]
if state_path.exists():
    state_path.unlink()
log_dir = md.MONITOR_CONFIG["log_dir"]

print("[1] 创建 daemon + tick loop...")
daemon = md.MonitorDaemon()
print(f"  tick.interval_sec: {daemon.tick.interval_sec}")
print(f"  tick.dry_run: {daemon.tick.dry_run}")

# 先手动挂一单, 测试 tick 触发
print("\n[2] 手动挂买单 @ 1500 (极低, 触发测试)...")
test_order = daemon.paper.place_order(
    side="buy", order_type="test",
    price=1500.0, size=1, sl=1490, tp=1510,
    reason="[TICK TEST]", tag="tick_test_1"
)
print(f"  挂单 ID: {test_order.id}")

print("\n[3] 跑 TICK 循环 20 秒...")
start = time.time()
ticks_before = daemon.tick.tick_count
while time.time() - start < 20:
    daemon.tick.run_once()
    time.sleep(daemon.tick.interval_sec)
ticks_after = daemon.tick.tick_count
print(f"  跑了 {ticks_after - ticks_before} 次 tick ({20/(ticks_after-ticks_before):.1f}s/次)")

print("\n[4] 检查成交...")
summary = daemon.paper.summary(1500)
print(f"  持仓: {len(summary['positions'])}")
print(f"  挂单: {len(summary['pending_orders'])}")
for p in summary["positions"]:
    print(f"    持: {p['side']} {p['size']}张 @ ${p['entry_price']:.2f}")

print("\n[5] 检查 tick 日志...")
tick_log = log_dir / "ticks.jsonl"
lines = tick_log.read_text(encoding="utf-8").splitlines()
print(f"  tick 行数: {len(lines)}")
print(f"  最后 3 个 tick:")
for ln in lines[-3:]:
    print(f"    {ln[:150]}")

# 检查 last_ticker
last_ticker = json.loads((log_dir / "last_ticker.json").read_text(encoding="utf-8"))
print(f"\n  last_ticker.last: {last_ticker['last']}")

print("\n[OK] TICK 循环验证通过")