#!/usr/bin/env python3
"""
CTREND 加密货币趋势因子交易系统
快速启动入口

使用:
    python run.py                    # 正常运行
    python run.py --predict-only     # 仅计算预测
    python run.py --train-only       # 仅训练模型
"""

import sys
from pathlib import Path

# 添加 src 到路径
SRC_DIR = Path(__file__).parent / "src"
sys.path.insert(0, str(SRC_DIR))

from main import main

if __name__ == "__main__":
    main()