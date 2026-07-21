# PaizaCloud 容器自动创建

自动创建 PaizaCloud 免费容器、安装哪吒探针并保持存活。


## 环境变量

请在 GitHub 仓库的 `Settings` -> `Secrets and variables` -> `Actions` 中配置以下 Secrets。

| 变量名 | 必填 | 说明 |
| :--- | :---: | :--- |
| `NEZHA_SERVER` | ✅ | 哪吒探针服务器地址，格式 `域名:端口` |
| `NEZHA_KEY` | ✅ | 哪吒探针 Client Secret |
| `NZ_UUID` | ✅ | 哪吒探针客户端 UUID |
| `TG_BOT` | ❌ | Telegram 通知，格式 `chat_id,bot_token`；未填写时不发通知 |


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

## 手动触发

```bash
curl -X POST \
  -H "Authorization: token 你的GH_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/repos/aoi675145/paiza_renew/actions/workflows/paiza-create.yml/dispatches \
  -d '{"ref":"main"}'
```
