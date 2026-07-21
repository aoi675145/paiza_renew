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

- **必填 Secrets**：`NEZHA_SERVER` / `NEZHA_KEY` / `NZ_UUID` / `PAIZA_JWT` / `VPS` 五个缺一不可，`TG_BOT` 可选
- **VPS 重装**：只需更新 `VPS` Secret（IP、端口、密码），下次运行会自动装 Python、上传保活脚本并启动
- **容器 4h 限制**：VPS 保活脚本每分钟发 keepalive，防止空闲超时；满 4 小时后自动快照恢复，重置计时
- **哪吒配置**：存在 GitHub Secrets 中，代码无硬编码
- **运行环境**：工作流在 GitHub Actions 上执行，依赖 WARP（用于绕过 Google 音频 reCAPTCHA 下载限制）


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

