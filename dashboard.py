#!/usr/bin/env python3
"""
Polymarket Arbitrage Bot 实时监控面板
显示关键运行数据和统计信息
"""

import os
import re
import time
import subprocess
from datetime import datetime
from collections import defaultdict

LOG_FILE = os.path.join(os.path.dirname(__file__), "bot.log")

def parse_log():
    """解析日志文件获取关键数据"""
    stats = {
        "start_time": None,
        "last_scan": None,
        "total_scans": 0,
        "markets_found": 0,
        "tradable_markets": 0,
        "signals": [],
        "trades": 0,
        "pnl": 0.0,
        "positions": 0,
        "errors": 0,
        "status": "未知"
    }
    
    if not os.path.exists(LOG_FILE):
        return stats
    
    try:
        with open(LOG_FILE, "r") as f:
            lines = f.readlines()
        
        for line in lines:
            # 解析时间戳
            if "PolyArbBot - INFO - Starting" in line:
                match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                if match:
                    stats["start_time"] = match.group(1)
            
            # 扫描统计
            if "Scanning for tradable markets" in line:
                stats["total_scans"] += 1
                match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                if match:
                    stats["last_scan"] = match.group(1)
            
            # 市场统计
            if "tradable markets out of" in line:
                match = re.search(r"(\d+) tradable markets out of (\d+)", line)
                if match:
                    stats["tradable_markets"] = int(match.group(1))
                    stats["markets_found"] = int(match.group(2))
            
            # 交易信号
            if "Entry signal" in line or "Exit signal" in line:
                stats["signals"].append(line.strip())
            
            # P&L
            if "Total P&L:" in line:
                match = re.search(r"\$([+-]?\d+\.?\d*)", line)
                if match:
                    stats["pnl"] = float(match.group(1))
            
            # 仓位
            if "Open Positions:" in line:
                match = re.search(r":\s*(\d+)", line)
                if match:
                    stats["positions"] = int(match.group(1))
            
            # 错误
            if "ERROR" in line or "Exception" in line:
                stats["errors"] += 1
        
        stats["status"] = "运行中" if stats["total_scans"] > 0 else "启动中"
        
    except Exception as e:
        stats["status"] = f"错误: {e}"
    
    return stats

def check_process():
    """检查 Bot 进程是否运行"""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "agents.arbitrage.main"],
            capture_output=True, text=True
        )
        pids = result.stdout.strip().split("\n")
        return [p for p in pids if p]
    except:
        return []

def display_dashboard():
    """显示监控面板"""
    os.system("clear" if os.name != "nt" else "cls")
    
    pids = check_process()
    stats = parse_log()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 计算运行时长
    uptime = "N/A"
    if stats["start_time"]:
        try:
            start = datetime.strptime(stats["start_time"], "%Y-%m-%d %H:%M:%S")
            delta = datetime.now() - start
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime = f"{hours}h {minutes}m {seconds}s"
        except:
            pass
    
    print("=" * 60)
    print("   🤖 POLYMARKET ARBITRAGE BOT - 实时监控面板")
    print("=" * 60)
    print(f"  刷新时间: {now}")
    print("-" * 60)
    
    # 进程状态
    if pids:
        print(f"  ✅ 进程状态: 运行中 (PID: {', '.join(pids)})")
    else:
        print("  ❌ 进程状态: 已停止")
    
    print(f"  📊 运行时长: {uptime}")
    print("-" * 60)
    
    # 市场扫描
    print("  📈 市场扫描")
    print(f"      扫描次数: {stats['total_scans']}")
    print(f"      发现市场: {stats['markets_found']}")
    print(f"      可交易市场: {stats['tradable_markets']}")
    print(f"      最后扫描: {stats['last_scan'] or 'N/A'}")
    print("-" * 60)
    
    # 交易统计
    print("  💰 交易统计")
    print(f"      交易信号: {len(stats['signals'])}")
    print(f"      开仓数量: {stats['positions']}")
    pnl_color = "🟢" if stats['pnl'] >= 0 else "🔴"
    print(f"      盈亏: {pnl_color} ${stats['pnl']:+.2f}")
    print("-" * 60)
    
    # 策略状态
    print("  🎯 策略状态")
    print("      套利策略: ✅ 启用 (阈值: 0.3%)")
    print("      动量策略: ✅ 启用 (阈值: 1.5%)")
    print("      复制交易: ✅ 启用 (2 个交易者)")
    print("-" * 60)
    
    # 最近信号
    if stats['signals']:
        print("  📢 最近信号 (最后3个)")
        for signal in stats['signals'][-3:]:
            print(f"      {signal[-80:]}")
        print("-" * 60)
    
    # 错误统计
    if stats['errors'] > 0:
        print(f"  ⚠️  错误数量: {stats['errors']}")
        print("-" * 60)
    
    print("  按 Ctrl+C 退出监控")
    print("=" * 60)

def main():
    """主监控循环"""
    print("启动实时监控面板...")
    try:
        while True:
            display_dashboard()
            time.sleep(5)  # 每5秒刷新
    except KeyboardInterrupt:
        print("\n监控已退出")

if __name__ == "__main__":
    main()
