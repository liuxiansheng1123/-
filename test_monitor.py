"""
monitor_daemon.py 静态验证:
- 语法检查
- 导入检查
- 跑 1 个 cycle (含数学审计)
- 不进入 while 循环
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "src"))

# 1. 导入测试
print("[1] 导入 monitor_daemon 模块...")
import monitor_daemon as md
print(f"  OK. TRIGGER_CONFIG={md.TRIGGER_CONFIG}")

# 2. 创建一个 daemon 但不 run
print("\n[2] 创建 MonitorDaemon 实例...")
daemon = md.MonitorDaemon()
print(f"  OK. Session={daemon.logger.session_id}")
print(f"  Log file: {daemon.logger.log_file}")

# 3. 跑一次 cycle
print("\n[3] 跑 1 次 cycle...")
daemon._run_one_cycle()

# 4. 检查日志
print("\n[4] 检查日志文件...")
log = daemon.logger.log_file
content = log.read_text(encoding="utf-8")
lines = content.splitlines()
print(f"  日志行数: {len(lines)}")
print(f"  最后 5 行:")
for ln in lines[-5:]:
    print(f"    {ln}")

# 5. 检查 JSONL
print("\n[5] 检查 JSONL 信号文件...")
csv = daemon.logger.csv_file
lines = csv.read_text(encoding="utf-8").splitlines()
print(f"  JSONL 行数: {len(lines)}")
# 统计
primary_count = sum(1 for l in lines if '"primary"' in l)
factor_count = sum(1 for l in lines if '"factor"' in l)
print(f"  primary 记录: {primary_count}")
print(f"  factor 记录: {factor_count}")

print("\n[OK] 静态验证通过")
