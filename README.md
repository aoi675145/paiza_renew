# PaizaCloud 容器自动创建

自动创建 PaizaCloud 免费容器、安装哪吒探针并保持存活。


## 环境变量

请在 GitHub 仓库的 `Settings` -> `Secrets and variables` -> `Actions` 中配置以下 Secrets。

| 变量名 | 必填 | 说明 |
| :--- | :---: | :--- |
| `NEZHA_SERVER` | ✅ | 哪吒探针服务器地址，格式 `域名:端口` |
| `NEZHA_KEY` | ✅ | 哪吒探针 Client Secret |
| `NZ_UUID` | ✅ | 哪吒探针客户端 UUID |
| `PAIZA_JWT` | ✅ | PaizaCloud 账号 JWT Token |
| `VPS` | ✅ | VPS 连接信息，格式 `IP,端口,密码` |
| `TG_BOT` | ❌ | Telegram 通知，格式 `chat_id,bot_token`；未填写时不发通知 |


## 注意事项

- **VPS 需要有 Python 环境**：脚本会自动尝试安装（`apk add python3` / `apt install python3`），但如果系统没有 `apk` 或 `apt` 会失败
- **VPS 配置文件自动更新**：每次创建新容器后，脚本会自动 SSH 到 VPS，将新容器的 ID 和 Token 写入 `/root/paiza_config.txt`，同时上传最新的 `paiza_renew.py` 并重启保活。VPS 始终保活最新容器
- **容器 4 小时限制**：`paiza_renew.py` 每 60 秒发 keepalive 防止空闲超时；满 4 小时自动快照恢复重置计时。VPS 失联则容器会被回收
- **Secrets 不全则 workflow 报错**：`NEZHA_SERVER` / `NEZHA_KEY` / `NZ_UUID` / `PAIZA_JWT` / `VPS` 缺一不可


## 工作流程

```
GitHub Actions 手动触发
  → Selenium 浏览器自动化
  → 音频 reCAPTCHA 破解
  → 创建容器
  → 终端交互安装哪吒探针（sudo -i 提权 + 安装脚本 + nohup 后台运行）
  → VPS 保活配置更新
  → TG 通知
```

