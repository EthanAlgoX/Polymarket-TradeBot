#!/usr/bin/env python3
"""
交易复盘分析工具

功能:
- 分析历史交易数据
- 计算胜率、盈亏、平均持仓时间等
- 生成策略优化建议
- 导出报告

Usage:
    python analyze_trades.py [--date YYYYMMDD] [--report]
"""

import os
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Any

# 日志目录
LOG_ROOT = Path(__file__).parent / "agents" / "arbitrage" / "logs"
TRADES_DIR = LOG_ROOT / "trades"
SIGNALS_DIR = LOG_ROOT / "signals"
DAILY_DIR = LOG_ROOT / "daily"


def load_trades(date: str = None) -> List[Dict]:
    """加载交易记录"""
    trades = []
    
    if date:
        pattern = f"trade_{date}_*.json"
    else:
        pattern = "trade_*.json"
    
    for f in TRADES_DIR.glob(pattern):
        try:
            with open(f, "r", encoding="utf-8") as file:
                trades.append(json.load(file))
        except Exception as e:
            print(f"Error loading {f}: {e}")
    
    return sorted(trades, key=lambda x: x.get("timestamp", ""))


def load_signals(date: str = None) -> List[Dict]:
    """加载交易信号"""
    signals = []
    
    if date:
        pattern = f"signal_{date}_*.json"
    else:
        pattern = "signal_*.json"
    
    for f in SIGNALS_DIR.glob(pattern):
        try:
            with open(f, "r", encoding="utf-8") as file:
                signals.append(json.load(file))
        except Exception as e:
            print(f"Error loading {f}: {e}")
    
    return sorted(signals, key=lambda x: x.get("timestamp", ""))


def analyze_trades(trades: List[Dict]) -> Dict:
    """分析交易数据"""
    if not trades:
        return {"total": 0, "message": "无交易记录"}
    
    stats = {
        "total_trades": len(trades),
        "successful": 0,
        "failed": 0,
        "total_pnl": 0.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "avg_execution_time_ms": 0.0,
        "by_type": defaultdict(int),
        "by_market": defaultdict(int),
        "by_hour": defaultdict(int),
        "max_profit": 0.0,
        "max_loss": 0.0,
        "win_rate": 0.0
    }
    
    total_exec_time = 0.0
    winning = 0
    losing = 0
    
    for trade in trades:
        # 成功/失败统计
        if trade.get("success"):
            stats["successful"] += 1
        else:
            stats["failed"] += 1
        
        # 按类型统计
        trade_type = trade.get("type", "UNKNOWN")
        stats["by_type"][trade_type] += 1
        
        # 按市场统计
        market = trade.get("market_id", "unknown")[:20]
        stats["by_market"][market] += 1
        
        # 按小时统计
        ts = trade.get("timestamp", "")
        if ts:
            try:
                hour = datetime.fromisoformat(ts).hour
                stats["by_hour"][hour] += 1
            except:
                pass
        
        # 执行时间
        exec_time = trade.get("execution_time_ms", 0)
        total_exec_time += exec_time
        
        # 盈亏统计
        pnl = trade.get("pnl", 0)
        stats["total_pnl"] += pnl
        
        if pnl > 0:
            winning += 1
            stats["max_profit"] = max(stats["max_profit"], pnl)
        elif pnl < 0:
            losing += 1
            stats["max_loss"] = min(stats["max_loss"], pnl)
    
    # 计算平均值
    stats["avg_execution_time_ms"] = total_exec_time / len(trades)
    
    # 计算胜率
    total_closed = winning + losing
    stats["win_rate"] = (winning / total_closed * 100) if total_closed > 0 else 0
    
    return stats


def analyze_signals(signals: List[Dict]) -> Dict:
    """分析信号数据"""
    if not signals:
        return {"total": 0, "message": "无信号记录"}
    
    stats = {
        "total_signals": len(signals),
        "entry_signals": 0,
        "exit_signals": 0,
        "avg_confidence": 0.0,
        "by_reason": defaultdict(int),
        "by_market": defaultdict(int)
    }
    
    total_confidence = 0
    
    for signal in signals:
        signal_type = signal.get("type", "UNKNOWN")
        if signal_type == "ENTRY":
            stats["entry_signals"] += 1
        elif signal_type == "EXIT":
            stats["exit_signals"] += 1
        
        # 置信度
        conf = signal.get("confidence", 0)
        total_confidence += conf
        
        # 按原因统计
        reason = signal.get("reason", "unknown")[:30]
        stats["by_reason"][reason] += 1
        
        # 按市场统计
        market = signal.get("market_id", "unknown")[:20]
        stats["by_market"][market] += 1
    
    stats["avg_confidence"] = total_confidence / len(signals) if signals else 0
    
    return stats


def generate_recommendations(trade_stats: Dict, signal_stats: Dict) -> List[str]:
    """生成策略优化建议"""
    recommendations = []
    
    # 基于交易统计
    if trade_stats.get("total_trades", 0) == 0:
        recommendations.append("📊 尚无交易记录，建议继续运行收集数据")
    else:
        # 胜率分析
        win_rate = trade_stats.get("win_rate", 0)
        if win_rate < 50:
            recommendations.append(f"⚠️ 胜率较低 ({win_rate:.1f}%)，建议提高入场阈值")
        elif win_rate > 70:
            recommendations.append(f"✅ 胜率良好 ({win_rate:.1f}%)，可以适当增加仓位")
        
        # 失败率分析
        failed = trade_stats.get("failed", 0)
        total = trade_stats.get("total_trades", 1)
        fail_rate = failed / total * 100
        if fail_rate > 20:
            recommendations.append(f"⚠️ 执行失败率较高 ({fail_rate:.1f}%)，检查网络和API连接")
        
        # 执行时间分析
        avg_exec = trade_stats.get("avg_execution_time_ms", 0)
        if avg_exec > 1000:
            recommendations.append(f"⚠️ 执行延迟较高 ({avg_exec:.0f}ms)，考虑优化网络")
    
    # 基于信号统计
    if signal_stats.get("total_signals", 0) > 0:
        avg_conf = signal_stats.get("avg_confidence", 0)
        if avg_conf < 0.7:
            recommendations.append(f"📈 平均置信度较低 ({avg_conf:.2f})，策略选择较保守")
    
    if not recommendations:
        recommendations.append("✅ 策略表现良好，继续监控")
    
    return recommendations


def print_report(date: str = None):
    """打印分析报告"""
    print("=" * 60)
    print("📊 POLYMARKET 交易复盘分析报告")
    print("=" * 60)
    print(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if date:
        print(f"分析日期: {date}")
    print("-" * 60)
    
    # 加载数据
    trades = load_trades(date)
    signals = load_signals(date)
    
    # 分析
    trade_stats = analyze_trades(trades)
    signal_stats = analyze_signals(signals)
    
    # 交易统计
    print("\n📈 交易统计")
    print(f"  总交易数: {trade_stats.get('total_trades', 0)}")
    print(f"  成功: {trade_stats.get('successful', 0)}")
    print(f"  失败: {trade_stats.get('failed', 0)}")
    print(f"  胜率: {trade_stats.get('win_rate', 0):.1f}%")
    print(f"  总盈亏: ${trade_stats.get('total_pnl', 0):.2f}")
    print(f"  平均执行时间: {trade_stats.get('avg_execution_time_ms', 0):.0f}ms")
    
    # 按类型分布
    if trade_stats.get("by_type"):
        print("\n  按类型分布:")
        for t, count in trade_stats["by_type"].items():
            print(f"    - {t}: {count}")
    
    # 信号统计
    print("\n📢 信号统计")
    print(f"  总信号数: {signal_stats.get('total_signals', 0)}")
    print(f"  入场信号: {signal_stats.get('entry_signals', 0)}")
    print(f"  出场信号: {signal_stats.get('exit_signals', 0)}")
    print(f"  平均置信度: {signal_stats.get('avg_confidence', 0):.2f}")
    
    # 优化建议
    print("\n💡 策略优化建议")
    recommendations = generate_recommendations(trade_stats, signal_stats)
    for rec in recommendations:
        print(f"  {rec}")
    
    print("\n" + "=" * 60)
    
    # 保存日报
    summary = {
        "date": date or datetime.now().strftime("%Y%m%d"),
        "trade_stats": dict(trade_stats),
        "signal_stats": dict(signal_stats),
        "recommendations": recommendations
    }
    
    # 清理 defaultdict 以便 JSON 序列化
    for key in ["by_type", "by_market", "by_hour", "by_reason"]:
        if key in summary["trade_stats"]:
            summary["trade_stats"][key] = dict(summary["trade_stats"][key])
        if key in summary["signal_stats"]:
            summary["signal_stats"][key] = dict(summary["signal_stats"][key])
    
    summary_file = DAILY_DIR / f"summary_{summary['date']}.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    
    print(f"📁 日报已保存: {summary_file}")


def main():
    parser = argparse.ArgumentParser(description="交易复盘分析工具")
    parser.add_argument("--date", help="分析日期 (YYYYMMDD)", default=None)
    parser.add_argument("--report", action="store_true", help="生成完整报告")
    args = parser.parse_args()
    
    # 确保目录存在
    for d in [LOG_ROOT, TRADES_DIR, SIGNALS_DIR, DAILY_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    
    print_report(args.date)


if __name__ == "__main__":
    main()
