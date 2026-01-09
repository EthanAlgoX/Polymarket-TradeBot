"""
日志配置模块 - 将交易信息和日志保存到本地文件夹

日志目录结构:
logs/
├── trades/       # 交易记录
├── signals/      # 交易信号
├── daily/        # 每日汇总
└── bot_YYYYMMDD.log  # 每日运行日志
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path

# 日志根目录
LOG_ROOT = Path(__file__).parent / "logs"
TRADES_DIR = LOG_ROOT / "trades"
SIGNALS_DIR = LOG_ROOT / "signals"
DAILY_DIR = LOG_ROOT / "daily"

# 确保目录存在
for d in [LOG_ROOT, TRADES_DIR, SIGNALS_DIR, DAILY_DIR]:
    d.mkdir(parents=True, exist_ok=True)


def setup_file_logging(logger_name: str = None) -> logging.Logger:
    """
    配置文件日志
    
    每日日志文件: logs/bot_YYYYMMDD.log
    """
    today = datetime.now().strftime("%Y%m%d")
    log_file = LOG_ROOT / f"bot_{today}.log"
    
    # 文件处理器
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    ))
    
    # 获取或创建 logger
    logger = logging.getLogger(logger_name)
    logger.addHandler(file_handler)
    
    return logger


def save_trade(trade_data: dict) -> str:
    """
    保存交易记录到 logs/trades/
    
    Args:
        trade_data: 交易数据字典
        
    Returns:
        保存的文件路径
    """
    now = datetime.now()
    filename = f"trade_{now.strftime('%Y%m%d_%H%M%S')}.json"
    filepath = TRADES_DIR / filename
    
    trade_data["saved_at"] = now.isoformat()
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(trade_data, f, indent=2, ensure_ascii=False)
    
    return str(filepath)


def save_signal(signal_data: dict) -> str:
    """
    保存交易信号到 logs/signals/
    
    Args:
        signal_data: 信号数据字典
        
    Returns:
        保存的文件路径
    """
    now = datetime.now()
    filename = f"signal_{now.strftime('%Y%m%d_%H%M%S')}.json"
    filepath = SIGNALS_DIR / filename
    
    signal_data["saved_at"] = now.isoformat()
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(signal_data, f, indent=2, ensure_ascii=False)
    
    return str(filepath)


def save_daily_summary(summary_data: dict) -> str:
    """
    保存每日汇总到 logs/daily/
    
    Args:
        summary_data: 汇总数据字典
        
    Returns:
        保存的文件路径
    """
    today = datetime.now().strftime("%Y%m%d")
    filename = f"summary_{today}.json"
    filepath = DAILY_DIR / filename
    
    summary_data["date"] = today
    summary_data["saved_at"] = datetime.now().isoformat()
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2, ensure_ascii=False)
    
    return str(filepath)


def get_today_trades() -> list:
    """获取今日所有交易记录"""
    today = datetime.now().strftime("%Y%m%d")
    trades = []
    
    for f in TRADES_DIR.glob(f"trade_{today}_*.json"):
        with open(f, "r", encoding="utf-8") as file:
            trades.append(json.load(file))
    
    return sorted(trades, key=lambda x: x.get("saved_at", ""))


def get_today_signals() -> list:
    """获取今日所有交易信号"""
    today = datetime.now().strftime("%Y%m%d")
    signals = []
    
    for f in SIGNALS_DIR.glob(f"signal_{today}_*.json"):
        with open(f, "r", encoding="utf-8") as file:
            signals.append(json.load(file))
    
    return sorted(signals, key=lambda x: x.get("saved_at", ""))


# 初始化日志配置
def init_logging():
    """初始化所有日志配置"""
    # 根 logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # 添加文件处理器
    setup_file_logging()
    
    # 记录启动
    logging.info("=" * 50)
    logging.info("日志系统初始化完成")
    logging.info(f"日志目录: {LOG_ROOT}")
    logging.info("=" * 50)


if __name__ == "__main__":
    # 测试日志功能
    init_logging()
    
    # 测试保存交易
    test_trade = {
        "type": "BUY",
        "market": "test_market",
        "price": 0.55,
        "size": 10,
        "pnl": 0.0
    }
    path = save_trade(test_trade)
    print(f"交易已保存: {path}")
    
    # 测试保存信号
    test_signal = {
        "type": "ENTRY",
        "market": "test_market",
        "confidence": 0.85,
        "reason": "Momentum breakout"
    }
    path = save_signal(test_signal)
    print(f"信号已保存: {path}")
    
    print("\n日志系统测试完成!")
