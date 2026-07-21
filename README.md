# PaizaCloud Free Container Auto-Create + Nezha Agent

PaizaCloud 免费容器全自动创建 + 自动安装哪吒探针 + 保活。

## 功能

- **全自动创建容器**：Selenium 浏览器自动化 + 音频 reCAPTCHA 破解
- **自动安装哪吒探针**：终端交互自动执行安装命令（`sudo -i` 提权 → 安装脚本 → `nohup` 后台运行）
- **自动保活**：VPS 每分钟调用 keepalive API，防止空闲超时
- **TG 通知**：创建成功/失败通知

## 流程

```
GitHub Actions 触发 → Selenium 打开 PaizaCloud →
Audio reCAPTCHA 破解（SpeechRecognition + pydub + WARP）→
提交创建容器 → 打开终端 →
sudo -i 提权 → 安装哪吒探针 → nohup 启动 agent →
更新 VPS 保活配置 → 完成
```

## 文件说明

| 文件 | 用途 |
|------|------|
| `paiza_create.py` | 主脚本：Selenium 自动化 + reCAPTCHA 音频破解 + 终端交互 + VPS 保活配置 |
| `paiza_renew.py` | VPS 保活脚本：每 60s 调用 keepalive API 防止容器超时 |
| `.github/workflows/paiza-create.yml` | GitHub Actions 工作流：WARP + 依赖安装 + 运行创建脚本 |

## 关键技术

### Audio reCAPTCHA 破解

- 使用 `SpeechRecognition` + `pydub` 将 Google reCAPTCHA 音频题转为文字
- 需要 WARP（Cloudflare Warp）绕过 Google 音频下载的 IP 限制
- 支持自动重试（最多 8 次）

### 终端交互

- 使用 Selenium `execute_script` 直接设置 textarea value + dispatch `input` 事件
- 同时使用 `send_keys` 作为备用（trusted 键盘事件）
- 先 `sudo -i` 提权，再执行安装命令

### 哪吒探针

- 安装方式：直接 curl 官方 install.sh + nohup 后台运行（Docker 容器无 systemd）

## 部署 VPS 保活

```bash
# 配置
echo "PAIZA_CONTAINER_ID=你的容器ID
PAIZA_CONTAINER_TOKEN=你的容器Token
PAIZA_TOKEN=你的JWTToken" > /root/paiza_config.txt

# 后台运行
export $(cat /root/paiza_config.txt | xargs)
nohup python3 /root/paiza_renew.py > /root/paiza_renew.log 2>&1 &
```

## 手动触发创建

```bash
# 提交到 GitHub 触发 workflow
git push

# 或手动触发
curl -X POST \
  -H "Authorization: token GH_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/repos/aoi675145/paiza_renew/actions/workflows/paiza-create.yml/dispatches \
  -d '{"ref":"main"}'
```
