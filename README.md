# PaizaCloud Free Container Auto-Renew

自动保活 + 自动续期 PaizaCloud 免费容器，突破 4 小时运行限制 + 24 小时生命周期自动重建。

## 功能

- **自动保活**：每 60 秒调用 keepalive API，防止 3 分钟空闲超时
- **自动续期**：每 5 分钟调用 activeKeepalive API，防止 20 分钟空闲超时
- **4h 自动恢复**：容器满 4 小时被自动 suspend 后，检测到挂起状态 → 等 Docker 清理 → 从快照恢复，重置 4h 计时
- **24h 自动重建**：24 小时总寿命到期后，自动调用 SeleniumBase + NopeCHA 过 reCAPTCHA 创建新容器

## 原理

PaizaCloud 免费容器限制：

| 限制 | 值 | 处理方式 |
|------|-----|----------|
| `keepaliveTimeout` | 3 分钟 | keepalive API 保活 |
| `activeKeepaliveTimeout` | 20 分钟 | activeKeepalive API 保活 |
| `maxTimeout` | 4 小时 | 快照恢复重置容器创建时间 |
| `runnableTimeAllocated` | 24 小时 | 自动销毁重建 |

脚本调用 PaizaCloud 内部 API `GET /api/containers/:id/keepalive` 和 `GET /api/containers/:id/activeKeepalive` 模拟用户活跃，防止前两种超时。

4 小时到点时：容器被 suspend → 脚本检测到 → 等 60s 让 Docker 清理旧容器 → 从最新快照恢复 → 新 Docker 容器启动，4h 计时重置。

24 小时到点时：容器 expired → 脚本检测到 → 调用 `paiza_create.py`（SeleniumBase + NopeCHA 浏览器自动化）→ 过 reCAPTCHA 创建新容器 → 创建快照 → 写入新配置 → 重启续期脚本。

## 文件说明

| 文件 | 用途 |
|------|------|
| `paiza_renew.py` | 主脚本：保活 + 4h 快照恢复 + 24h 检测触发重建 |
| `paiza_create.py` | 辅助脚本：SeleniumBase + NopeCha 自动创建新容器 |
| `paiza_config.txt` | 容器配置（请勿外传） |

## 部署

### 前提

- 一台 Linux VPS（Alpine 或 Ubuntu 等均可）
- PaizaCloud 免费容器
- （可选，用于 24h 自动重建）Chrome 浏览器环境 + NopeCHA 插件

### 1. 获取容器信息

登录 [PaizaCloud](https://paiza.cloud)，从浏览器开发者工具中获取：

- **容器 ID**：URL 路径中的 ID 部分
- **容器 Token**：页面请求中的 `container_token` 参数

### 2. 创建快照

新容器 snapshots 为 0，必须先创建快照：

```bash
curl -X POST "https://paiza.cloud/api/containers/YOUR_CONTAINER_ID/snapshots?container_token=YOUR_TOKEN"
```

验证：

```bash
curl -s "https://paiza.cloud/api/containers/YOUR_CONTAINER_ID?container_token=YOUR_TOKEN" | python3 -c "import sys,json; c=json.load(sys.stdin); print(f'Snapshots: {len(c.get(\"snapshots\",[]))}')"
```

输出 `Snapshots: 1` 即成功。

### 3. 上传脚本到 VPS

```bash
scp paiza_renew.py root@你的VPSIP:/root/paiza_renew.py
scp paiza_create.py root@你的VPSIP:/root/paiza_create.py
```

### 4. 安装依赖

```bash
# Alpine
apk add python3 py3-requests

# Ubuntu/Debian
apt install python3 python3-requests

# pip（如果系统包管理器没有）
pip install requests
```

创建脚本需要额外安装（在有 Chrome 的环境上运行）：

```bash
pip install seleniumbase
```

### 5. 配置并启动

```bash
# 写入环境变量
export PAIZA_CONTAINER_ID=YOUR_CONTAINER_ID
export PAIZA_CONTAINER_TOKEN=YOUR_TOKEN
# 如果启用 24h 自动重建：
export PAIZA_TOKEN=YOUR_JWT_TOKEN

# 后台运行
nohup python3 /root/paiza_renew.py > /root/paiza_renew.log 2>&1 &
```

### 6. 验证

```bash
tail -f /root/paiza_renew.log
```

看到类似输出即成功：

```
2026-07-19 06:34:13 - INFO - === PaizaCloud Auto-Renew Started ===
2026-07-19 06:34:14 - INFO - running containerId:783f16e73d0a
```

## 24h 自动重建（高级配置）

需要在有 Chrome 浏览器的环境运行 `paiza_create.py`，并准备好 NopeCHA 插件：

### GitHub Actions 方式

参考 `paiza-create.yml` 配置 GitHub Actions 工作流，每月运行2次。

### VPS 直接运行

如果 VPS 有 Chrome：

```bash
apk add chromium chromium-chromedriver
pip install seleniumbase
# 下载 NopeCha 插件
wget -q "https://github.com/NopeCHALLC/nopecha-extension/releases/latest/download/chromium.zip"
unzip chromium.zip -d nopecha
rm chromium.zip
# 运行创建脚本
PAIZA_TOKEN=YOUR_JWT python3 /root/paiza_create.py
```

## 查看日志

```bash
tail -f /root/paiza_renew.log
```

## 注意事项

- 脚本需要一直运行，停止后容器会在 3-20 分钟内被挂起
- 快照恢复会短暂中断服务（约 15-30 秒）
- 24h 自动重建需要 `paiza_create.py` 和环境变量 `PAIZA_TOKEN`
- reCAPTCHA 依赖 NopeCHA 插件，需要 Chorme 环境
