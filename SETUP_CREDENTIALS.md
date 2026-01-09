# Polymarket API Credentials 配置指南

## 概述

Polymarket 使用 **Polygon (Matic) 钱包私钥**进行身份验证，而不是传统的 API Key/Secret 方式。

## 配置步骤

### 1. 获取 Polygon 钱包私钥

您需要一个 Polygon 网络上的钱包。有以下几种方式：

#### 方式 A: 使用现有 MetaMask 钱包

1. 打开 MetaMask
2. 点击账户详情
3. 导出私钥（Export Private Key）
4. 输入密码确认
5. 复制私钥（64位十六进制字符串，不含 `0x` 前缀）

#### 方式 B: 创建新钱包（推荐用于测试）

```bash
# 使用 Python 生成新钱包
python3 << 'EOF'
from eth_account import Account
import secrets

# 生成新账户
priv = secrets.token_hex(32)
acct = Account.from_key(priv)

print(f"私钥: {priv}")
print(f"地址: {acct.address}")
print("\n⚠️  请妥善保管私钥，不要分享给任何人！")
EOF
```

### 2. 配置 .env 文件

编辑 `/Users/yunxuanhan/Documents/workspace/ai/Polymarket-TradeBot/agents-main/.env`:

```bash
# Polygon 钱包私钥（64位十六进制，不含0x前缀）
POLYGON_WALLET_PRIVATE_KEY=your_64_character_hex_private_key_here

# 确保 Paper Trading 开启（测试模式）
PAPER_TRADING=true
```

**示例：**

```bash
POLYGON_WALLET_PRIVATE_KEY=1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef
```

### 3. 资金要求

- **Paper Trading 模式**：不需要真实资金
- **Live Trading 模式**：
  - 钱包需要有 USDC（在 Polygon 网络上）
  - 需要少量 MATIC 用于 gas 费用

### 4. 验证配置

运行以下命令验证配置：

```bash
cd /Users/yunxuanhan/Documents/workspace/ai/Polymarket-TradeBot/agents-main
python3 << 'EOF'
import os
from dotenv import load_dotenv
from eth_account import Account

load_dotenv()

key = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")

if not key or key == "your_private_key":
    print("❌ 私钥未配置")
elif len(key) != 64:
    print(f"❌ 私钥长度错误: {len(key)} (应为64)")
else:
    try:
        acct = Account.from_key(key)
        print(f"✅ 私钥有效")
        print(f"钱包地址: {acct.address}")
    except Exception as e:
        print(f"❌ 私钥格式错误: {e}")
EOF
```

## 安全注意事项

⚠️ **重要安全提示：**

1. **永远不要分享私钥**
2. **不要将 .env 文件提交到 Git**（已在 .gitignore 中）
3. **测试时使用 Paper Trading 模式**
4. **生产环境使用专用钱包，不要存放大量资金**

## 故障排除

### 错误: "Non-hexadecimal digit found"

- 私钥必须是64位十六进制字符（0-9, a-f）
- 不要包含 `0x` 前缀
- 不要包含空格或其他字符

### 错误: "No orderbook exists"

- 这是正常的，部分市场没有活跃的 orderbook
- Bot 会自动跳过这些市场

### Bot 没有交易

- 套利机会非常罕见
- 尝试启用动量策略：`MOMENTUM_ENABLED=true`
- 或者配置 Copy Trading 跟随盈利交易者

## 下一步

配置完成后，运行：

```bash
cd /Users/yunxuanhan/Documents/workspace/ai/Polymarket-TradeBot/agents-main
python -m agents.arbitrage.main
```

查看日志确认连接成功。
