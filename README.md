# PaizaCloud 容器自动创建

自动创建 PaizaCloud 免费容器、安装哪吒探针并保持存活。


## 环境变量

请在 GitHub 仓库的 `Settings` -> `Secrets and variables` -> `Actions` 中配置以下 Secrets。

| 变量名 | 必填 | 说明 |
| :--- | :---: | :--- |
| `NEZHA_SERVER` | ✅ | 哪吒探针服务器地址，格式 `域名:端口` |
| `NEZHA_KEY` | ✅ | 哪吒探针 Client Secret |
| `NZ_UUID` | ✅ | 哪吒探针客户端 UUID |
| `ARGO_DOMAIN` | ❌ | Argo 隧道域名（可选，不填则纯安装哪吒） |
| `ARGO_AUTH` | ❌ | Argo 隧道认证 Token（可选） |
| `PAIZA_JWT` | ✅ | PaizaCloud 账号 JWT Token |
| `VPS` | ✅ | VPS 连接信息，格式 `IP,端口,密码` |
| `TG_BOT` | ❌ | Telegram 通知，格式 `chat_id,bot_token`；未填写时不发通知 |


## 注意事项

- **VPS 需要有 Python 环境**：脚本会自动尝试安装（`apk add python3` / `apt install python3`），但如果系统没有 `apk` 或 `apt` 会失败
- **容器 4 小时限制**：`paiza_renew.py` 每 60 秒发 keepalive 防止空闲超时；满 4 小时自动触发 GitHub Actions workflow 重建容器
- **Secrets 不全则 workflow 报错**：`NEZHA_SERVER` / `NEZHA_KEY` / `NZ_UUID` / `PAIZA_JWT` / `VPS` 缺一不可


## 工作流程

```
GitHub Actions 手动触发 / 接近 4h 时自动触发
  → Selenium 浏览器自动化
  → 音频 reCAPTCHA 破解
  → 删除旧容器 / 创建新容器
  → 终端交互安装哪吒探针（sudo -i 提权 + 安装脚本 + nohup 后台运行）
  → SSH 到 VPS，写入新容器 ID 和 Token 到 /root/paiza_config.txt
  → 上传最新 paiza_renew.py，重启保活脚本
  → TG 通知
```

## 自动续期流程

```
VPS 保活脚本检测到运行时间 ≥ 4 小时
  → 通过 GitHub API 触发 workflow_dispatch
  → 脚本退出（exit 0）
  → GitHub Actions 创建新容器
  → 新容器部署完毕，VPS 保活脚本自动启动
```
