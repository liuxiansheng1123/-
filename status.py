"""
状态查询: 看 daemon 健康状况 + 最近的 tick + 持仓
用法: python status.py
"""
import sys, json, time
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))
LOG_DIR = ROOT / "data" / "monitor"


def main():
    print("=" * 60)
    print("ETH 实时监控状态")
    print("=" * 60)

    # 1. 最近 daemon 日志
    log_files = sorted(LOG_DIR.glob("monitor_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not log_files:
        print("ERR: 没找到 daemon 日志, daemon 没在跑")
        return
    latest = log_files[0]
    print(f"\n[Daemon 日志] {latest.name}")

    # daemon 是否活着: 检查 ticks.jsonl 最后修改时间
    tick_path = LOG_DIR / "ticks.jsonl"
    daemon_alive = False
    last_tick_age = None
    if tick_path.exists():
        last_tick_age = (time.time() - tick_path.stat().st_mtime)
        daemon_alive = last_tick_age < 30

    # 找最近的 HB / START / STOP
    lines = latest.read_text(encoding="utf-8").splitlines()
    last_hb = None
    last_start = None
    last_stop = None
    last_align = None
    last_fill = None
    last_exit = None
    for ln in lines:
        if "[HB]" in ln:
            last_hb = ln
        elif "[START]" in ln:
            last_start = ln
        elif "[END]" in ln or "[STOP]" in ln:
            last_stop = ln
        elif "[ALIGN]" in ln:
            last_align = ln
        elif "[TICK-FILL]" in ln:
            last_fill = ln
        elif "[TICK-EXIT]" in ln:
            last_exit = ln

    if last_start:
        print(f"  启动: {last_start}")
    if last_stop:
        print(f"  停止: {last_stop}")
    if last_hb:
        print(f"  因子心跳: {last_hb}")

    if daemon_alive:
        print(f"  状态: <b>存活</b> (TICK {last_tick_age:.0f} 秒前)")
    elif last_stop:
        print(f"  状态: 已停止")
    else:
        print(f"  状态: 异常 (TICK {last_tick_age:.0f} 秒无数据)")

    if last_align:
        print(f"\n[最近对齐] {last_align}")
    if last_fill:
        print(f"[最近成交] {last_fill}")
    if last_exit:
        print(f"[最近平仓] {last_exit}")

    # 2. Tick 数据
    tick_path = LOG_DIR / "ticks.jsonl"
    if tick_path.exists():
        tick_lines = tick_path.read_text(encoding="utf-8").splitlines()
        print(f"\n[Tick 数据] {len(tick_lines)} 行")
        if tick_lines:
            last_tick = json.loads(tick_lines[-1])
            print(f"  最新价: ${last_tick['last']}")
            print(f"  时间: {last_tick['ts']}")
            # 5 分钟内的 tick 数
            now = datetime.now()
            recent = 0
            for ln in tick_lines[-300:]:
                t = json.loads(ln)
                ts = datetime.fromisoformat(t["ts"])
                if (now - ts).total_seconds() < 300:
                    recent += 1
            print(f"  最近 5 分钟: {recent} 个 tick ({recent/300:.1f}/秒)")

    # 3. 虚拟交易状态
    state_path = ROOT / "data" / "paper_trader_state.json"
    if state_path.exists():
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        print(f"\n[虚拟交易]")
        print(f"  余额: ${state.get('balance', 0):.2f}")
        print(f"  已实现盈亏: ${state.get('realized_pnl', 0):+.2f}")
        print(f"  持仓: {len(state.get('positions', []))}")
        for p in state.get("positions", []):
            print(f"    - {p['side']} {p['size']}张 @ ${p['entry_price']:.2f} (sl=${p['sl']:.2f}, tp=${p['tp']:.2f})")
        print(f"  挂单: {len(state.get('pending_orders', []))}")
        for o in state.get("pending_orders", []):
            print(f"    - {o['side']} {o['size']}张 @ ${o['price']:.2f}")
        print(f"  已关闭交易: {len(state.get('closed_trades', []))}")
        if state.get("closed_trades"):
            total_pnl = sum(t["net_pnl"] for t in state["closed_trades"])
            wins = sum(1 for t in state["closed_trades"] if t["net_pnl"] > 0)
            print(f"    胜率: {wins}/{len(state['closed_trades'])} = {wins/len(state['closed_trades'])*100:.1f}%")
            print(f"    总净盈亏: ${total_pnl:+.2f}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()