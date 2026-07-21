#!/usr/bin/env python3
import os, re, sys, time, json, base64, html, tempfile, random, uuid, urllib.request, urllib.parse
sys.stdout.reconfigure(line_buffering=True)
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

JWT_TOKEN = os.environ.get("PAIZA_JWT", "")

NEZHA_SERVER = os.environ.get("NEZHA_SERVER", "")
NEZHA_KEY = os.environ.get("NEZHA_KEY", "")
NZ_UUID = os.environ.get("NZ_UUID", "")
ARGO_DOMAIN = os.environ.get("ARGO_DOMAIN", "")
ARGO_AUTH = os.environ.get("ARGO_AUTH", "")

TG_BOT = os.environ.get("TG_BOT", "")
_tg = [x.strip() for x in TG_BOT.split(",")] if TG_BOT else []
TG_CHAT_ID = _tg[0] if len(_tg) > 0 else ""
TG_TOKEN   = _tg[1] if len(_tg) > 1 else ""

MAX_CAPTCHA = 8
SCRIPT_DIR = Path(__file__).resolve().parent
SCREEN_DIR = SCRIPT_DIR / "screenshots"
snap_index = 0


def ensure_dir():
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)


def snap(driver, name):
    global snap_index
    try:
        snap_index += 1
        ts = datetime.now().strftime("%H%M%S")
        fname = f"{ts}-{snap_index:03d}-{name}.png"
        path = str(SCREEN_DIR / fname)
        driver.save_screenshot(path)
        log.info(f"📸 {fname}")
        return path
    except Exception:
        return None


def bj_now():
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M")

def send_tg(msg):
    if not TG_TOKEN:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": TG_CHAT_ID, "text": msg}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15):
            log.info("📨 TG done")
    except Exception as e:
        log.warning(f"TG fail: {e}")


# ── reCAPTCHA audio solver (adapted for Selenium) ──


def switch_to_frame(driver, url_contains):
    for frame in driver.find_elements(By.TAG_NAME, "iframe"):
        try:
            driver.switch_to.frame(frame)
            if url_contains in driver.current_url:
                return True
            driver.switch_to.parent_frame()
        except Exception:
            driver.switch_to.parent_frame()
    # Try recursive through all frames
    driver.switch_to.default_content()
    for window in driver.window_handles:
        driver.switch_to.window(window)
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        for f in frames:
            try:
                src = f.get_attribute("src") or ""
                if url_contains in src:
                    driver.switch_to.frame(f)
                    return True
            except Exception:
                pass
    return False


def is_recaptcha_solved(driver):
    driver.switch_to.default_content()
    try:
        token = driver.execute_script(
            "return document.querySelector('textarea#g-recaptcha-response')?.value || ''"
        )
        if token and len(token) > 30:
            return True
    except Exception:
        pass
    try:
        # Check via recaptcha frames
        for frame in driver.find_elements(By.TAG_NAME, "iframe"):
            try:
                src = frame.get_attribute("src") or ""
                if "anchor" in src:
                    driver.switch_to.frame(frame)
                    checked = driver.execute_script(
                        "return document.querySelector('#recaptcha-anchor')?.getAttribute('aria-checked') === 'true'"
                    )
                    driver.switch_to.parent_frame()
                    if checked:
                        return True
            except Exception:
                driver.switch_to.parent_frame()
                continue
    except Exception:
        pass
    return False


def click_recaptcha_checkbox(driver):
    log.info("点击 reCAPTCHA 复选框...")
    driver.switch_to.default_content()
    if switch_to_frame(driver, "anchor"):
        try:
            driver.find_element(By.ID, "recaptcha-anchor").click()
            log.info("复选框已点击")
        except Exception as e:
            log.warning(f"复选框点击失败: {e}")
        driver.switch_to.parent_frame()
    else:
        log.warning("未找到 anchor 框架")


def switch_to_audio(driver):
    if not switch_to_frame(driver, "bframe"):
        return False
    try:
        already = driver.execute_script(
            "return document.querySelector('#audio-response') !== null && "
            "document.querySelector('#audio-response').offsetParent !== null"
        )
        if already:
            driver.switch_to.parent_frame()
            return True
    except Exception:
        pass
    for _ in range(3):
        try:
            btn = driver.find_element(By.ID, "recaptcha-audio-button")
            btn.click()
            time.sleep(2)
            is_audio = driver.execute_script(
                "return document.querySelector('#audio-response') !== null"
            )
            driver.switch_to.parent_frame()
            if is_audio:
                return True
        except Exception:
            try:
                driver.execute_script(
                    "document.querySelector('#recaptcha-audio-button')?.click()"
                )
                time.sleep(2)
                is_audio = driver.execute_script(
                    "return document.querySelector('#audio-response') !== null"
                )
                driver.switch_to.parent_frame()
                if is_audio:
                    return True
            except Exception:
                pass
        driver.switch_to.parent_frame()
        time.sleep(2)
    return False


def get_audio_url(driver):
    if not switch_to_frame(driver, "bframe"):
        return None
    for _ in range(10):
        try:
            url = driver.execute_script("""return (function() {
                var link = document.querySelector('.rc-audiochallenge-tdownload-link');
                if (link) { var h = link.getAttribute('href'); if (h && h.length > 10) return h; }
                var audio = document.querySelector('#audio-source');
                if (audio) { var s = audio.getAttribute('src'); if (s && s.length > 10) return s; }
                return null;
            })()""")
            if url:
                driver.switch_to.parent_frame()
                return html.unescape(url)
        except Exception:
            pass
        time.sleep(1)
    driver.switch_to.parent_frame()
    return None


def download_audio(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.google.com/",
    }
    urls = [url]
    if "recaptcha.net" in url:
        urls.append(url.replace("recaptcha.net", "www.google.com"))
    elif "google.com" in url:
        urls.append(url.replace("www.google.com", "recaptcha.net"))
    for u in urls:
        try:
            import requests as r
            resp = r.get(u, headers=headers, timeout=30)
            resp.raise_for_status()
            if len(resp.content) < 1000:
                continue
            log.info(f"音频: {len(resp.content)} 字节")
            p = tempfile.mktemp(suffix=".mp3")
            with open(p, "wb") as f:
                f.write(resp.content)
            return p
        except Exception as e:
            log.warning(f"音频下载失败: {e}")
    return None


def recognize_audio(mp3_path):
    try:
        import speech_recognition as sr
        from pydub import AudioSegment
        wav = mp3_path.replace(".mp3", ".wav")
        AudioSegment.from_mp3(mp3_path).export(wav, format="wav")
        rec = sr.Recognizer()
        with sr.AudioFile(wav) as src:
            text = rec.recognize_google(rec.record(src))
        try:
            os.remove(wav)
        except Exception:
            pass
        return text
    except Exception as e:
        log.warning(f"语音识别失败: {e}")
        return None


def fill_and_verify(driver, text):
    if not switch_to_frame(driver, "bframe"):
        return False
    try:
        el = driver.find_element(By.ID, "audio-response")
        el.clear()
        el.send_keys(text)
    except Exception:
        driver.switch_to.parent_frame()
        return False
    time.sleep(random.uniform(0.5, 1.5))
    try:
        driver.find_element(By.ID, "recaptcha-verify-button").click()
    except Exception:
        pass
    driver.switch_to.parent_frame()
    return True


def reload_challenge(driver):
    if switch_to_frame(driver, "bframe"):
        try:
            driver.find_element(By.ID, "recaptcha-reload-button").click()
            time.sleep(2)
        except Exception:
            pass
        driver.switch_to.parent_frame()


def solve_recaptcha(driver):
    log.info("🎧 音频验证码破解中...")
    time.sleep(3)
    for i in range(MAX_CAPTCHA):
        log.info(f"--- 第 {i+1}/{MAX_CAPTCHA} 次尝试 ---")
        if is_recaptcha_solved(driver):
            log.info("验证码已通过！")
            return True
        if i == 0:
            click_recaptcha_checkbox(driver)
            time.sleep(3)
            snap(driver, "checkbox")
            if is_recaptcha_solved(driver):
                log.info("复选框已通过验证码！")
                return True
        if not switch_to_frame(driver, "bframe"):
            log.info("未找到 bframe，等待中...")
            time.sleep(3)
            continue
        is_audio = driver.execute_script(
            "return document.querySelector('#audio-response') !== null"
        )
        driver.switch_to.parent_frame()
        if not is_audio:
            log.info("切换到音频模式...")
            switch_to_audio(driver)
            time.sleep(2)
        snap(driver, f"audio-{i+1}")
        audio_url = get_audio_url(driver)
        if not audio_url:
            reload_challenge(driver)
            continue
        mp3 = download_audio(audio_url)
        if not mp3:
            reload_challenge(driver)
            time.sleep(3)
            continue
        text = recognize_audio(mp3)
        try:
            os.remove(mp3)
        except Exception:
            pass
        if not text:
            reload_challenge(driver)
            time.sleep(3)
            continue
        log.info(f"识别结果: [{text}]")
        fill_and_verify(driver, text)
        time.sleep(5)
        if is_recaptcha_solved(driver):
            log.info("验证码已通过！")
            return True
        log.warning("答案错误，重试...")
        reload_challenge(driver)
        time.sleep(3)
    raise RuntimeError("验证码尝试次数耗尽")


# ── API ──

def api_call(method, path, data=None):
    url = f"https://paiza.cloud{path}"
    headers = {
        "Authorization": f"Bearer {JWT_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        log.warning(f"API 请求失败 {method} {path}: {e.code}")
        # 204 No Content is success for DELETE
        if e.code == 404:
            return {}
        return None


def get_containers():
    return api_call("GET", "/api/containers")


def delete_container(cid):
    result = api_call("DELETE", f"/api/containers/{cid}")
    if result is None:
        log.warning(f"删除可能失败: {cid[:12]}")
    else:
        log.info(f"已删除: {cid[:12]}")


def update_vps_config(cid, token, container_ip=None, container_ssh_port=None):
    _vps = (os.environ.get("VPS") or "").split(",")
    vps_host = _vps[0] if len(_vps) > 0 else ""
    vps_port = int(_vps[1]) if len(_vps) > 1 else 0
    vps_pass = _vps[2] if len(_vps) > 2 else ""
    try:
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(vps_host, port=vps_port, username="root", password=vps_pass, timeout=30)

        config = f"""PAIZA_CONTAINER_ID={cid}
PAIZA_CONTAINER_TOKEN={token}
PAIZA_JWT={JWT_TOKEN}
"""
        stdin, stdout, stderr = client.exec_command(f"cat > /root/paiza_config.txt")
        stdin.write(config)
        stdin.flush()
        stdin.channel.shutdown_write()
        time.sleep(2)

        # Check if python3 exists, fallback to python
        stdin2, stdout2, stderr2 = client.exec_command("which python3 || which python || echo 'no python'")
        py = stdout2.read().decode().strip()
        log.info(f"VPS Python: {py}")

        # Auto-install python if missing
        if py == 'no python':
            log.info("Python not found on VPS, installing...")
            client.exec_command("apk add python3 2>/dev/null || apt-get install -y python3 2>/dev/null || true")
            time.sleep(8)
            stdin2, stdout2, stderr2 = client.exec_command("which python3 || which python || echo 'no python'")
            py = stdout2.read().decode().strip()
            log.info(f"VPS Python after install: {py}")

        # Ensure requests module is installed
        client.exec_command(f"{py} -c 'import requests' 2>/dev/null || pip3 install requests 2>/dev/null || {py} -m pip install requests 2>/dev/null || true")
        time.sleep(3)

        # Upload latest paiza_renew.py to VPS
        renew_script = Path(__file__).resolve().parent / "paiza_renew.py"
        if renew_script.exists():
            script_content = renew_script.read_text()
            stdin_s, stdout_s, stderr_s = client.exec_command(f"cat > /root/paiza_renew.py")
            stdin_s.write(script_content)
            stdin_s.flush()
            stdin_s.channel.shutdown_write()
            time.sleep(2)
            log.info("已上传 paiza_renew.py 到 VPS")

        client.exec_command("pkill -f 'paiza_renew.py' 2>/dev/null || true")
        time.sleep(1)
        # Source config file as env vars before starting
        client.exec_command(f"nohup sh -c 'export $(cat /root/paiza_config.txt | xargs) && {py} /root/paiza_renew.py' > /root/paiza_renew.log 2>&1 &")
        time.sleep(5)

        # Check logs for errors
        stdin3, stdout3, stderr3 = client.exec_command("cat /root/paiza_renew.log 2>/dev/null | tail -10")
        vps_log = stdout3.read().decode().strip()
        if vps_log:
            log.info(f"VPS log: {vps_log}")
        else:
            # Check if process is running
            stdin4, stdout4, stderr4 = client.exec_command("ps aux | grep -v grep | grep paiza_renew || true")
            running = stdout4.read().decode().strip()
            log.info(f"保活运行中: {bool(running)}")
            if running:
                log.info(f"PID: {running[:100]}")

        # Try SSH into container from VPS to install Nezha agent
        if container_ip and container_ssh_port:
            try:
                # Ensure sshpass is available on VPS
                client.exec_command("which sshpass 2>/dev/null || (apk add sshpass 2>/dev/null || apt-get install sshpass -y 2>/dev/null || true)")
                time.sleep(2)
                NEZHA_CMD = (f"NAME=paiza UUID={NZ_UUID} ARGO_DOMAIN={ARGO_DOMAIN} "
                             f"ARGO_AUTH={ARGO_AUTH} "
                             f"NEZHA_SERVER={NEZHA_SERVER} NEZHA_KEY={NEZHA_KEY} "
                             f"bash <(curl -Ls https://main.ssss.nyc.mn/sb.sh)")
                install_cmd = f'sshpass -p "{token}" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 root@{container_ip} -p {container_ssh_port} "{NEZHA_CMD}" 2>&1 || true'
                stdin_i, stdout_i, stderr_i = client.exec_command(install_cmd, timeout=120)
                install_out = stdout_i.read().decode().strip()[-500:]
                if install_out:
                    log.info(f"容器 SSH 安装输出: {install_out}")
            except Exception as e2:
                log.warning(f"容器 SSH 安装失败: {e2}")

        client.close()
        log.info("VPS 配置已更新，保活已重启")
        return True
    except ImportError:
        log.warning("paramiko 未安装，尝试 subprocess...")
        import subprocess
        cmd = f"""sshpass -p '{vps_pass}' ssh -o StrictHostKeyChecking=no root@{vps_host} -p {vps_port} 'cat > /root/paiza_config.txt << EOF
PAIZA_CONTAINER_ID={cid}
PAIZA_CONTAINER_TOKEN={token}
PAIZA_JWT={JWT_TOKEN}
EOF
pkill -f "python3 paiza_renew.py" 2>/dev/null || true
nohup python3 /root/paiza_renew.py > /root/paiza_renew.log 2>&1 &'"""
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        log.info(f"VPS 子进程: {result.stdout[-200:]} {result.stderr[-200:]}")
        return True
    except Exception as e:
        log.error(f"VPS 更新失败: {e}")
        return False


# ── Main ──

def run():
    ensure_dir()
    log.info("=" * 50)
    log.info("PaizaCloud 容器自动创建")
    log.info("=" * 50)

    # Step 0: Delete any existing containers
    log.info("检查旧容器并清理...")
    try:
        existing = get_containers()
        if existing and isinstance(existing, list):
            for c in existing:
                old_id = c.get("_id") or c.get("id", "")
                old_short = c.get("containerIdShort", "") or old_id[:12]
                log.info(f"删除旧容器: {old_id} ({old_short})")
                delete_container(old_id)
                time.sleep(2)
    except Exception as e:
        log.warning(f"删除步骤失败（非致命）: {e}")

    opts = Options()
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-infobars")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=opts)
    driver.set_window_size(1920, 1080)

    try:
        # Set cookie via CDP before navigation
        log.info("通过 CDP 设置 Cookie...")
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd("Network.setCookie", {
            "name": "token",
            "value": JWT_TOKEN,
            "domain": "paiza.cloud",
            "path": "/",
        })
        log.info("Cookie 已设置")

        # Navigate to create page
        log.info("打开创建页面 /containers?new=true ...")
        driver.get("https://paiza.cloud/containers?new=true")
        time.sleep(5)
        log.info(f"当前 URL: {driver.current_url}")
        snap(driver, "create-page")

        # Check if redirected
        if "/en/" in driver.current_url and "containers" not in driver.current_url:
            log.warning("被重定向到首页，尝试其他方式...")
            driver.get("https://paiza.cloud/containers")
            time.sleep(3)
            snap(driver, "containers-page")
            for sel in ["New", "Create", "作成", "+"]:
                try:
                    el = driver.find_element(By.XPATH, f"//*[text()='{sel}']")
                    if el.is_displayed():
                        el.click()
                        log.info(f"点击了 '{sel}' 按钮")
                        time.sleep(3)
                        break
                except Exception:
                    continue

        snap(driver, "before-captcha")

        # Fill name if visible
        try:
            name_in = driver.find_element(By.CSS_SELECTOR, "input[name='name']")
            if name_in.is_displayed():
                name_in.send_keys("auto-" + datetime.now().strftime("%m%d%H%M"))
                log.info("容器名称已填写")
        except Exception:
            pass

        # Solve reCAPTCHA
        solve_recaptcha(driver)
        snap(driver, "captcha-solved")

        # Submit
        log.info("提交创建...")
        for sel in ["//*[text()='New Server']", "button[type='submit']", "//*[text()='Create']"]:
            try:
                if sel.startswith("//"):
                    el = driver.find_element(By.XPATH, sel)
                else:
                    el = driver.find_element(By.CSS_SELECTOR, sel)
                if el.is_displayed():
                    el.click()
                    log.info(f"点击了: {sel}")
                    break
            except Exception:
                continue

        time.sleep(15)
        snap(driver, "after-submit")

        # Check container created
        containers = get_containers()
        if not containers or len(containers) == 0:
            raise RuntimeError("提交后未创建容器")
        cid = containers[0].get("_id") or containers[0].get("id", "")
        log.info(f"容器已创建: {cid}")

        # Get container token from detail
        detail = api_call("GET", f"/api/containers/{cid}")
        token = None
        container_ip = None
        container_ssh_port = None
        if detail:
            token = detail.get("token") or detail.get("containerToken")
            log.info(f"容器 Token: {token[:20] if token else 'N/A'}...")
            # Extract container SSH info
            container_ssh_port = detail.get("sshPort")
            history = detail.get("containerHistory") or []
            if history:
                container_ip = history[0].get("containerNodeIP")
            log.info(f"容器 SSH: {container_ip}:{container_ssh_port}")

        if not token:
            log.warning("API 中未找到 Token，尝试从浏览器获取...")
            driver.get(f"https://paiza.cloud/containers/{cid}/show")
            time.sleep(3)
            token = driver.execute_script("""
                var meta = document.querySelector('meta[name=csrf-token]');
                return meta ? meta.getAttribute('content') : '';
            """)
            if not token:
                token = driver.execute_script("""
                    return window.__INITIAL_STATE__ ? (window.__INITIAL_STATE__.containerToken || '') : '';
                """)

        log.info(f"最终 Token: {token[:20] if token else 'N/A'}...")

        # ── Open terminal and install Nezha agent ──
        NEZHA_CMD = (f"NAME=paiza UUID={NZ_UUID} ARGO_DOMAIN={ARGO_DOMAIN} "
                     f"ARGO_AUTH={ARGO_AUTH} "
                     f"NEZHA_SERVER={NEZHA_SERVER} NEZHA_KEY={NEZHA_KEY} "
                     f"bash <(curl -Ls https://main.ssss.nyc.mn/sb.sh)")
        NEZHA_CMD = os.environ.get("NEZHA_CMD", NEZHA_CMD)

        log.info("打开终端，安装哪吒探针...")
        try:
            driver.get(f"https://paiza.cloud/containers/{cid}/show")
            time.sleep(5)
            snap(driver, "workspace")

            # Click Terminal button using Angular controller
            clicked = driver.execute_script("""
                var scope = angular.element('.container-menu').scope();
                if (scope && scope.wmController) {
                    scope.wmController.openTerminal();
                    return 'angular';
                }
                var btns = document.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {
                    if (btns[i].textContent.trim().toLowerCase().includes('terminal')) {
                        btns[i].click();
                        return 'dom';
                    }
                }
                return false;
            """)
            log.info(f"终端打开方式: {clicked}")
            time.sleep(8)
            snap(driver, "terminal-opened")

            # Wait for terminal element to exist with content
            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script("""
                    var term = document.querySelector('div.terminal');
                    if (!term) return false;
                    return term.querySelector('textarea') !== null;
                """)
            )
            log.info("终端文本区域已就绪")

            # Find terminal textarea
            term_ta = None
            for sel in ["div.terminal textarea", "textarea[autocapitalize='none']", "div.terminal > textarea"]:
                try:
                    term_ta = driver.find_element(By.CSS_SELECTOR, sel)
                    if term_ta and term_ta.is_displayed():
                        break
                except Exception:
                    pass
            if not term_ta:
                try:
                    term_ta = driver.find_element(By.CSS_SELECTOR, "div.terminal textarea")
                except Exception:
                    pass
            log.info(f"终端文本区域找到: {term_ta is not None}")

            # Reliable key sender: dispatch input event via JS
            def type_text_js(text):
                driver.execute_script("""
                    var ta = arguments[0];
                    var cmd = arguments[1];
                    ta.focus();
                    ta.select();
                    ta.value = cmd;
                    ta.dispatchEvent(new Event('input', {bubbles: true}));
                    // Dispatch Enter key to execute command
                    ['keydown','keypress','keyup'].forEach(function(type) {
                        ta.dispatchEvent(new KeyboardEvent(type, {
                            key: 'Enter', code: 'Enter', keyCode: 13,
                            which: 13, bubbles: true, cancelable: true
                        }));
                    });
                """, term_ta, text)
                log.info(f"JS 输入: {text[:50]}...")

            def type_text_sendkeys(text):
                term_ta.send_keys(text + "\n")
                log.info(f"send_keys 输入: {text[:50]}...")

            # Wait for shell then become root
            time.sleep(3)
            log.info("发送 sudo -i 提权...")
            type_text_js("sudo -i")
            # Also send Enter via send_keys (trusted event, xterm.js may ignore JS KeyboardEvent)
            term_ta.send_keys("\n")
            time.sleep(5)
            snap(driver, "after-sudo")
            log.info("发送哪吒安装命令...")
            type_text_js(NEZHA_CMD)
            time.sleep(3)
            type_text_sendkeys(NEZHA_CMD)
            time.sleep(30)
            snap(driver, "after-nezha-install")

            # Read back terminal rendered text to verify
            output_check = driver.execute_script("""
                var div = document.querySelector('div.terminal');
                if (!div) return 'no-terminal-div';
                var rows = div.querySelectorAll('.xterm-rows span, .xterm-rows div');
                if (rows.length > 0) {
                    return Array.from(rows).map(function(r) { return r.textContent; }).join('').slice(-500);
                }
                return (div.innerText || div.textContent || '').slice(-500);
            """)
            log.info(f"终端输出: {output_check[:200]}")

            log.info("sb.sh 安装完成")
            # sb.sh handles everything (Argo tunnel + Nezha agent), just check output is non-empty
            terminal_ok = f"ArgoDomain:{ARGO_DOMAIN}" in output_check
            nezha_status = "✅ 已安装" if terminal_ok else "⚠️ 命令已发送"
        except Exception as e:
            log.warning(f"终端交互失败: {e}")
            terminal_ok = False
            nezha_status = f"❌ 终端异常"

        # Update VPS
        if token:
            vps_ok = update_vps_config(cid, token, container_ip, container_ssh_port)
        else:
            log.error("无法更新 VPS - 无 Token")
            vps_ok = False

        # ── Consolidated notification ──
        lines = [
            "🎮 PaizaCloud 重建",
            f"⏰ 通知时间：{bj_now()}",
            f"📊 容器：{'✅' if cid else '❌'} {cid[:12] if cid else '创建失败'}",
            f"📊 哪吒：{nezha_status}",
            f"📊 保活：{'✅ 运行中' if vps_ok else '❌ 部署失败'}",
        ]
        send_tg("\n".join(lines))

    except Exception as e:
        log.error(f"Error: {e}")
        lines = [
            "🎮 PaizaCloud ❌",
            f"⏰ 通知时间：{bj_now()}",
            f"❌ {e}",
        ]
        send_tg("\n".join(lines))
        snap(driver, "error")
        raise
    finally:
        driver.quit()


if __name__ == "__main__":
    run()
