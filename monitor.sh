#!/bin/bash
# Polymarket Arbitrage Bot 监控脚本
# Usage: ./monitor.sh [command]
# Commands: status, log, stop, restart

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/bot.log"
PID_FILE="$SCRIPT_DIR/bot.pid"

case "${1:-status}" in
    status)
        echo "========================================"
        echo "Polymarket Arbitrage Bot 状态"
        echo "========================================"
        if pgrep -f "agents.arbitrage.main" > /dev/null; then
            PID=$(pgrep -f "agents.arbitrage.main")
            echo "✅ Bot 运行中 (PID: $PID)"
            echo ""
            echo "最近日志:"
            tail -5 "$LOG_FILE" 2>/dev/null || echo "日志文件不存在"
        else
            echo "❌ Bot 未运行"
        fi
        ;;
    
    log)
        echo "实时日志 (Ctrl+C 退出):"
        tail -f "$LOG_FILE"
        ;;
    
    stop)
        if pgrep -f "agents.arbitrage.main" > /dev/null; then
            pkill -f "agents.arbitrage.main"
            echo "✅ Bot 已停止"
        else
            echo "❌ Bot 未运行"
        fi
        ;;
    
    restart)
        $0 stop
        sleep 2
        cd "$SCRIPT_DIR"
        nohup python -m agents.arbitrage.main > bot.log 2>&1 &
        echo $! > "$PID_FILE"
        echo "✅ Bot 已重启 (PID: $!)"
        ;;
    
    summary)
        echo "========================================"
        echo "交易汇总"
        echo "========================================"
        grep -E "(P&L|Positions|Trades|Win Rate)" "$LOG_FILE" | tail -10
        ;;
    
    *)
        echo "用法: $0 [status|log|stop|restart|summary]"
        echo ""
        echo "  status  - 查看 Bot 状态"
        echo "  log     - 实时查看日志"
        echo "  stop    - 停止 Bot"
        echo "  restart - 重启 Bot"
        echo "  summary - 查看交易汇总"
        ;;
esac
