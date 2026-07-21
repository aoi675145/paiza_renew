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
        print(f"  {fname}")
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
            print("  TG done")
    except Exception as e:
        print(f"  TG fail: {e}")


# ── reCAPTCHA audio solver ──


def switch_to_frame(driver, url_contains):
    for frame in driver.find_elements(By.TAG_NAME, "iframe"):
        try:
            driver.switch_to.frame(frame)
            if url_contains in driver.current_url:
                return True
            driver.switch_to.parent_frame()
        except Exception:
            driver.switch_to.parent_frame()
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
    print("  \U0001f6e1\ufe0f \u70b9\u51fb reCAPTCHA \u590d\u9009\u6846...")
    driver.switch_to.default_content()
    if switch_to_frame(driver, "anchor"):
        try:
            driver.find_element(By.ID, "recaptcha-anchor").click()
        except Exception:
            pass
        driver.switch_to.parent_frame()


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
            print(f"  \U0001f50a {len(resp.content)} \u5b57\u8282")
            p = tempfile.mktemp(suffix=".mp3")
            with open(p, "wb") as f:
                f.write(resp.content)
            return p
        except Exception as e:
            print(f"  \u26a0\ufe0f \u97f3\u9891\u4e0b\u8f7d\u5931\u8d25: {e}")
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
        print(f"  \u26a0\ufe0f \u8bed\u97f3\u8bc6\u522b\u5931\u8d25: {e}")
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
    print("  \U0001f3a7 \u97f3\u9891\u9a8c\u8bc1\u7801\u7834\u89e3\u4e2d...")
    time.sleep(3)
    for i in range(MAX_CAPTCHA):
        if is_recaptcha_solved(driver):
            print("  \u2705 \u9a8c\u8bc1\u7801\u5df2\u901a\u8fc7\uff01")
            return True
        if i == 0:
            click_recaptcha_checkbox(driver)
            time.sleep(3)
            snap(driver, "checkbox")
            if is_recaptcha_solved(driver):
                print("  \u2705 \u590d\u9009\u6846\u5df2\u901a\u8fc7\u9a8c\u8bc1\u7801\uff01")
                return True
        if not switch_to_frame(driver, "bframe"):
            time.sleep(3)
            continue
        is_audio = driver.execute_script(
            "return document.querySelector('#audio-response') !== null"
        )
        driver.switch_to.parent_frame()
        if not is_audio:
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
        print(f"  \U0001f50d \u8bc6\u522b\u7ed3\u679c: [{text}]")
        fill_and_verify(driver, text)
        time.sleep(5)
        if is_recaptcha_solved(driver):
            print("  \u2705 \u9a8c\u8bc1\u7801\u5df2\u901a\u8fc7\uff01")
            return True
        print("  \u26a0\ufe0f \u7b54\u6848\u9519\u8bef\uff0c\u91cd\u8bd5...")
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
        log.warning(f"API {method} {path}: {e.code}")
        if e.code == 404:
            return {}
        return None


def get_containers():
    return api_call("GET", "/api/containers")


def delete_container(cid):
    result = api_call("DELETE", f"/api/containers/{cid}")
    if result is None:
        print(f"  \u26a0\ufe0f \u5220\u9664\u53ef\u80fd\u5931\u8d25: {cid[:12]}")
    else:
        print(f"  \u2705 \u5df2\u5220\u9664: {cid[:12]}")


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

        stdin2, stdout2, stderr2 = client.exec_command("which python3 || which python || echo 'no python'")
        py = stdout2.read().decode().strip()
        print(f"  \U0001f4e1 VPS Python: {py}")

        if py == 'no python':
            print("  \U0001f4e1 Python \u672a\u5b89\u88c5\uff0c\u81ea\u52a8\u5b89\u88c5...")
            client.exec_command("apk add python3 2>/dev/null || apt-get install -y python3 2>/dev/null || true")
            time.sleep(8)
            stdin2, stdout2, stderr2 = client.exec_command("which python3 || which python || echo 'no python'")
            py = stdout2.read().decode().strip()
            print(f"  \U0001f4e1 \u5b89\u88c5\u5b8c\u6210: {py}")

        client.exec_command(f"{py} -c 'import requests' 2>/dev/null || pip3 install requests 2>/dev/null || {py} -m pip install requests 2>/dev/null || true")
        time.sleep(3)

        renew_script = Path(__file__).resolve().parent / "paiza_renew.py"
        if renew_script.exists():
            script_content = renew_script.read_text()
            stdin_s, stdout_s, stderr_s = client.exec_command(f"cat > /root/paiza_renew.py")
            stdin_s.write(script_content)
            stdin_s.flush()
            stdin_s.channel.shutdown_write()
            time.sleep(2)
            print("  \U0001f4e1 \u5df2\u4e0a\u4f20 paiza_renew.py")

        client.exec_command("pkill -f 'paiza_renew.py' 2>/dev/null || true")
        time.sleep(1)
        client.exec_command(f"nohup sh -c 'export $(cat /root/paiza_config.txt | xargs) && {py} /root/paiza_renew.py' > /root/paiza_renew.log 2>&1 &")
        time.sleep(5)

        stdin3, stdout3, stderr3 = client.exec_command("cat /root/paiza_renew.log 2>/dev/null | tail -10")
        vps_log = stdout3.read().decode().strip()
        if vps_log:
            print(f"  \U0001f4e1 \u4fdd\u6d3b\u65e5\u5fd7: {vps_log[:200]}")
        else:
            stdin4, stdout4, stderr4 = client.exec_command("ps aux | grep -v grep | grep paiza_renew || true")
            running = stdout4.read().decode().strip()
            if running:
                print("  \u2705 \u4fdd\u6d3b\u5df2\u542f\u52a8")

        # Try SSH into container from VPS to install Nezha agent
        if container_ip and container_ssh_port:
            try:
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
                    print(f"  \U0001f4e1 \u5bb9\u5668 SSH \u5b89\u88c5\u8f93\u51fa: {install_out[:200]}")
            except Exception as e2:
                print(f"  \u26a0\ufe0f \u5bb9\u5668 SSH \u5b89\u88c5\u5931\u8d25: {e2}")

        client.close()
        print("  \u2705 VPS \u914d\u7f6e\u5df2\u66f4\u65b0\uff0c\u4fdd\u6d3b\u5df2\u91cd\u542f")
        return True
    except ImportError:
        print("  \u26a0\ufe0f paramiko \u672a\u5b89\u88c5\uff0c\u4f7f\u7528 subprocess...")
        import subprocess
        cmd = f"""sshpass -p '{vps_pass}' ssh -o StrictHostKeyChecking=no root@{vps_host} -p {vps_port} 'cat > /root/paiza_config.txt << EOF
PAIZA_CONTAINER_ID={cid}
PAIZA_CONTAINER_TOKEN={token}
PAIZA_JWT={JWT_TOKEN}
EOF
pkill -f "python3 paiza_renew.py" 2>/dev/null || true
nohup python3 /root/paiza_renew.py > /root/paiza_renew.log 2>&1 &'"""
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        print(f"  \U0001f4e1 VPS: {result.stdout[-100:]} {result.stderr[-100:]}")
        return True
    except Exception as e:
        print(f"  \u274c VPS \u66f4\u65b0\u5931\u8d25: {e}")
        return False


# ── Main ──

def run():
    ensure_dir()
    print("")
    print("#########################")
    print("   PaizaCloud \u5bb9\u5668\u81ea\u52a8\u521b\u5efa")
    print("#########################")
    print("")

    # Step 0: Delete any existing containers
    print("  \U0001f5d1\ufe0f \u68c0\u67e5\u5e76\u5220\u9664\u65e7\u5bb9\u5668...")
    try:
        existing = get_containers()
        if existing and isinstance(existing, list):
            for c in existing:
                old_id = c.get("_id") or c.get("id", "")
                old_short = c.get("containerIdShort", "") or old_id[:12]
                print(f"  \U0001f5d1\ufe0f \u5220\u9664\u65e7\u5bb9\u5668: {old_short}")
                delete_container(old_id)
                time.sleep(2)
    except Exception as e:
        print(f"  \u26a0\ufe0f \u5220\u9664\u6b65\u9aa4\u5931\u8d25: {e}")

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
        print("  \U0001f310 \u8bbe\u7f6e Cookie \u5e76\u6253\u5f00\u521b\u5efa\u9875\u9762...")
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd("Network.setCookie", {
            "name": "token",
            "value": JWT_TOKEN,
            "domain": "paiza.cloud",
            "path": "/",
        })
        driver.get("https://paiza.cloud/containers?new=true")
        time.sleep(5)
        snap(driver, "create-page")

        if "/en/" in driver.current_url and "containers" not in driver.current_url:
            print("  \u26a0\ufe0f \u88ab\u91cd\u5b9a\u5411\uff0c\u5c1d\u8bd5\u5176\u4ed6\u65b9\u5f0f...")
            driver.get("https://paiza.cloud/containers")
            time.sleep(3)
            snap(driver, "containers-page")
            for sel in ["New", "Create", "\u4f5c\u6210", "+"]:
                try:
                    el = driver.find_element(By.XPATH, f"//*[text()='{sel}']")
                    if el.is_displayed():
                        el.click()
                        time.sleep(3)
                        break
                except Exception:
                    continue

        snap(driver, "before-captcha")

        try:
            name_in = driver.find_element(By.CSS_SELECTOR, "input[name='name']")
            if name_in.is_displayed():
                name_in.send_keys("auto-" + datetime.now().strftime("%m%d%H%M"))
        except Exception:
            pass

        print("  \U0001f6e1\ufe0f \u89e3\u51b3 reCAPTCHA \u9a8c\u8bc1\u7801...")
        solve_recaptcha(driver)
        snap(driver, "captcha-solved")

        print("  \U0001f680 \u63d0\u4ea4\u521b\u5efa\u4efb\u52a1...")
        for sel in ["//*[text()='New Server']", "button[type='submit']", "//*[text()='Create']"]:
            try:
                if sel.startswith("//"):
                    el = driver.find_element(By.XPATH, sel)
                else:
                    el = driver.find_element(By.CSS_SELECTOR, sel)
                if el.is_displayed():
                    el.click()
                    break
            except Exception:
                continue

        time.sleep(15)
        snap(driver, "after-submit")

        containers = get_containers()
        if not containers or len(containers) == 0:
            raise RuntimeError("提交后未创建容器")
        cid = containers[0].get("_id") or containers[0].get("id", "")
        print(f"\n  \u2705 \u5bb9\u5668\u5df2\u521b\u5efa: {cid[:12]}\n")

        detail = api_call("GET", f"/api/containers/{cid}")
        token = None
        container_ip = None
        container_ssh_port = None
        if detail:
            token = detail.get("token") or detail.get("containerToken")
            container_ssh_port = detail.get("sshPort")
            history = detail.get("containerHistory") or []
            if history:
                container_ip = history[0].get("containerNodeIP")

        if not token:
            print("  \u26a0\ufe0f API \u672a\u627e\u5230 Token\uff0c\u5c1d\u8bd5\u4ece\u6d4f\u89c8\u5668\u83b7\u53d6...")
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

        # ── Open terminal and install ──
        NEZHA_CMD = (f"NAME=paiza UUID={NZ_UUID} ARGO_DOMAIN={ARGO_DOMAIN} "
                     f"ARGO_AUTH={ARGO_AUTH} "
                     f"NEZHA_SERVER={NEZHA_SERVER} NEZHA_KEY={NEZHA_KEY} "
                     f"bash <(curl -Ls https://main.ssss.nyc.mn/sb.sh)")
        NEZHA_CMD = os.environ.get("NEZHA_CMD", NEZHA_CMD)

        print("  \U0001f4bb \u6253\u5f00\u7ec8\u7aef\uff0c\u5b89\u88c5 sb.sh...")
        try:
            driver.get(f"https://paiza.cloud/containers/{cid}/show")
            time.sleep(5)
            snap(driver, "workspace")

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
            time.sleep(8)
            snap(driver, "terminal-opened")

            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script("""
                    var term = document.querySelector('div.terminal');
                    if (!term) return false;
                    return term.querySelector('textarea') !== null;
                """)
            )

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

            def type_text_js(text):
                driver.execute_script("""
                    var ta = arguments[0];
                    var cmd = arguments[1];
                    ta.focus();
                    ta.select();
                    ta.value = cmd;
                    ta.dispatchEvent(new Event('input', {bubbles: true}));
                    ['keydown','keypress','keyup'].forEach(function(type) {
                        ta.dispatchEvent(new KeyboardEvent(type, {
                            key: 'Enter', code: 'Enter', keyCode: 13,
                            which: 13, bubbles: true, cancelable: true
                        }));
                    });
                """, term_ta, text)

            def type_text_sendkeys(text):
                term_ta.send_keys(text + "\n")

            time.sleep(3)
            print("  \U0001f512 sudo -i \u63d0\u6743...")
            type_text_js("sudo -i")
            term_ta.send_keys("\n")
            time.sleep(5)
            snap(driver, "after-sudo")

            print("  \U0001f916 \u53d1\u9001\u5b89\u88c5\u547d\u4ee4...")
            type_text_js(NEZHA_CMD)
            time.sleep(3)
            type_text_sendkeys(NEZHA_CMD)

            terminal_ok = False
            for attempt in range(24):
                time.sleep(5)
                output_check = driver.execute_script("""
                    var div = document.querySelector('div.terminal');
                    if (!div) return 'no-terminal-div';
                    var rows = div.querySelectorAll('.xterm-rows span, .xterm-rows div');
                    if (rows.length > 0) {
                        return Array.from(rows).map(function(r) { return r.textContent; }).join('').slice(-1000);
                    }
                    return (div.innerText || div.textContent || '').slice(-1000);
                """)
                if "Running done!" in output_check:
                    terminal_ok = True
                    break
            snap(driver, "after-nezha-install")

            if terminal_ok:
                print("  \u2705 sb.sh \u5b89\u88c5\u5b8c\u6210\uff01")
            else:
                print("  \u26a0\ufe0f \u5b89\u88c5\u547d\u4ee4\u5df2\u53d1\u9001\uff0c\u4f46\u672a\u786e\u8ba4\u5b8c\u6210")
            nezha_status = "\u2705 \u5df2\u5b89\u88c5" if terminal_ok else "\u26a0\ufe0f \u547d\u4ee4\u5df2\u53d1\u9001"
        except Exception as e:
            print(f"  \u274c \u7ec8\u7aef\u4ea4\u4e92\u5931\u8d25: {e}")
            nezha_status = "\u274c \u7ec8\u7aef\u5f02\u5e38"

        # Update VPS
        if token:
            print("  \U0001f4e1 \u66f4\u65b0 VPS \u4fdd\u6d3b\u914d\u7f6e...")
            vps_ok = update_vps_config(cid, token, container_ip, container_ssh_port)
        else:
            print("  \u274c \u65e0 Token\uff0c\u65e0\u6cd5\u66f4\u65b0 VPS")
            vps_ok = False

        ok_em = "\u2705"; no_em = "\u274c"
        fail = "\u521b\u5efa\u5931\u8d25"
        running = "\u8fd0\u884c\u4e2d"; deploy_fail = "\u90e8\u7f72\u5931\u8d25"
        lines = [
            "\U0001f3ae PaizaCloud \u91cd\u5efa",
            f"\u23f0 \u901a\u77e5\u65f6\u95f4\uff1a{bj_now()}",
            f"\U0001f4ca \u5bb9\u5668\uff1a{ok_em if cid else no_em} {cid[:12] if cid else fail}",
            f"\U0001f4ca \u542f\u52a8\u811a\u672c\uff1a{nezha_status}",
            f"\U0001f4ca \u4fdd\u6d3b\uff1a{ok_em + ' ' + running if vps_ok else no_em + ' ' + deploy_fail}",
        ]
        send_tg("\n".join(lines))

        print(f"\n  \U0001f3c1 \u5168\u90e8\u5b8c\u6210\uff01")
        print(f"  \U0001f517 {driver.current_url}")

    except Exception as e:
        print(f"\n  \u274c \u8fd0\u884c\u5931\u8d25: {e}")
        lines = [
            "\U0001f3ae PaizaCloud \u274c",
            f"\u23f0 \u901a\u77e5\u65f6\u95f4\uff1a{bj_now()}",
            f"\u274c {e}",
        ]
        send_tg("\n".join(lines))
        snap(driver, "error")
        raise
    finally:
        driver.quit()


if __name__ == "__main__":
    run()
