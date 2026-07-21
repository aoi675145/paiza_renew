#!/usr/bin/env python3
import os, re, sys, time, json, base64, html, tempfile, random, uuid, urllib.request, urllib.parse
sys.stdout.reconfigure(line_buffering=True)
import logging
from pathlib import Path
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

JWT_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJfaWQiOiI2YTViYTFmMGIxNTE1MzY2ODM4ODczMDgiLCJyb2xlIjoidXNlciIsInNpZ25BdCI6MTc4NDQ4NTA3NjQxNCwiaWF0IjoxNzg0NDg1MDc2fQ.zp4rx4wkDvpeFe6O8YLSCQeuZVGYmz2uAO3T5XWhtJM"

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
    log.info("Click reCAPTCHA checkbox...")
    driver.switch_to.default_content()
    if switch_to_frame(driver, "anchor"):
        try:
            driver.find_element(By.ID, "recaptcha-anchor").click()
            log.info("Checkbox clicked")
        except Exception as e:
            log.warning(f"Checkbox click failed: {e}")
        driver.switch_to.parent_frame()
    else:
        log.warning("No anchor frame found")


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
            log.info(f"Audio: {len(resp.content)} bytes")
            p = tempfile.mktemp(suffix=".mp3")
            with open(p, "wb") as f:
                f.write(resp.content)
            return p
        except Exception as e:
            log.warning(f"Audio dl fail: {e}")
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
        log.warning(f"Recognize fail: {e}")
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
    log.info("🎧 Solving reCAPTCHA via audio...")
    time.sleep(3)
    for i in range(MAX_CAPTCHA):
        log.info(f"--- Attempt {i+1}/{MAX_CAPTCHA} ---")
        if is_recaptcha_solved(driver):
            log.info("Solved!")
            return True
        if i == 0:
            click_recaptcha_checkbox(driver)
            time.sleep(3)
            snap(driver, "checkbox")
            if is_recaptcha_solved(driver):
                log.info("Solved by checkbox!")
                return True
        if not switch_to_frame(driver, "bframe"):
            log.info("No bframe, waiting...")
            time.sleep(3)
            continue
        is_audio = driver.execute_script(
            "return document.querySelector('#audio-response') !== null"
        )
        driver.switch_to.parent_frame()
        if not is_audio:
            log.info("Switching to audio...")
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
        log.info(f"Recognized: [{text}]")
        fill_and_verify(driver, text)
        time.sleep(5)
        if is_recaptcha_solved(driver):
            log.info("Solved!")
            return True
        log.warning("Wrong, retry...")
        reload_challenge(driver)
        time.sleep(3)
    raise RuntimeError("Max captcha attempts")


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
        # 204 No Content is success for DELETE
        if e.code == 404:
            return {}
        return None


def get_containers():
    return api_call("GET", "/api/containers")


def delete_container(cid):
    result = api_call("DELETE", f"/api/containers/{cid}")
    if result is None:
        log.warning(f"Delete may have failed for {cid[:12]}")
    else:
        log.info(f"Deleted: {cid[:12]}")


def update_vps_config(cid, token):
    vps_host = os.environ.get("VPS_HOST") or "31.22.111.169"
    vps_port = int(os.environ.get("VPS_PORT") or "20973")
    vps_pass = os.environ.get("VPS_PASSWORD") or "A*yS7Y5U^7yZmLdv"
    try:
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(vps_host, port=vps_port, username="root", password=vps_pass, timeout=30)

        config = f"""PAIZA_CONTAINER_ID={cid}
PAIZA_CONTAINER_TOKEN={token}
PAIZA_TOKEN={JWT_TOKEN}
"""
        stdin, stdout, stderr = client.exec_command(f"cat > /root/paiza_config.txt")
        stdin.write(config)
        stdin.flush()
        stdin.channel.shutdown_write()
        time.sleep(2)

        # Check if python3 exists, fallback to python
        stdin2, stdout2, stderr2 = client.exec_command("which python3 || which python || echo 'no python'")
        py = stdout2.read().decode().strip()
        log.info(f"VPS python: {py}")

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
            log.info("Uploaded paiza_renew.py to VPS")

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
            log.info(f"Keepalive running: {bool(running)}")
            if running:
                log.info(f"PID: {running[:100]}")

        client.close()
        log.info("VPS config updated, keepalive restarted")
        send_tg(f"✅ VPS keepalive updated for {cid[:12]}")
        return True
    except ImportError:
        log.warning("paramiko not installed, trying subprocess...")
        import subprocess
        cmd = f"""sshpass -p '{vps_pass}' ssh -o StrictHostKeyChecking=no root@{vps_host} -p {vps_port} 'cat > /root/paiza_config.txt << EOF
PAIZA_CONTAINER_ID={cid}
PAIZA_CONTAINER_TOKEN={token}
PAIZA_TOKEN={JWT_TOKEN}
EOF
pkill -f "python3 paiza_renew.py" 2>/dev/null || true
nohup python3 /root/paiza_renew.py > /root/paiza_renew.log 2>&1 &'"""
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        log.info(f"VPS subprocess: {result.stdout[-200:]} {result.stderr[-200:]}")
        return True
    except Exception as e:
        log.error(f"VPS update failed: {e}")
        return False


# ── Main ──

def run():
    ensure_dir()
    log.info("=" * 50)
    log.info("PaizaCloud Container Auto-Create")
    log.info("=" * 50)

    # Step 0: Delete any existing containers
    log.info("Checking for old containers to delete...")
    try:
        existing = get_containers()
        if existing and isinstance(existing, list):
            for c in existing:
                old_id = c.get("_id") or c.get("id", "")
                old_short = c.get("containerIdShort", "") or old_id[:12]
                log.info(f"Deleting old container: {old_id} ({old_short})")
                delete_container(old_id)
                time.sleep(2)
    except Exception as e:
        log.warning(f"Delete step failed (non-fatal): {e}")

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
        log.info("Setting cookie via CDP...")
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd("Network.setCookie", {
            "name": "token",
            "value": JWT_TOKEN,
            "domain": "paiza.cloud",
            "path": "/",
        })
        log.info("Cookie set")

        # Navigate to create page
        log.info("Opening /containers?new=true ...")
        driver.get("https://paiza.cloud/containers?new=true")
        time.sleep(5)
        log.info(f"URL: {driver.current_url}")
        snap(driver, "create-page")

        # Check if redirected
        if "/en/" in driver.current_url and "containers" not in driver.current_url:
            log.warning("Redirected to home, trying approach...")
            driver.get("https://paiza.cloud/containers")
            time.sleep(3)
            snap(driver, "containers-page")
            for sel in ["New", "Create", "作成", "+"]:
                try:
                    el = driver.find_element(By.XPATH, f"//*[text()='{sel}']")
                    if el.is_displayed():
                        el.click()
                        log.info(f"Clicked '{sel}' button")
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
                log.info("Name filled")
        except Exception:
            pass

        # Solve reCAPTCHA
        solve_recaptcha(driver)
        snap(driver, "captcha-solved")

        # Submit
        log.info("Submitting...")
        for sel in ["//*[text()='New Server']", "button[type='submit']", "//*[text()='Create']"]:
            try:
                if sel.startswith("//"):
                    el = driver.find_element(By.XPATH, sel)
                else:
                    el = driver.find_element(By.CSS_SELECTOR, sel)
                if el.is_displayed():
                    el.click()
                    log.info(f"Clicked: {sel}")
                    break
            except Exception:
                continue

        time.sleep(15)
        snap(driver, "after-submit")

        # Check container created
        containers = get_containers()
        if not containers or len(containers) == 0:
            raise RuntimeError("No container created after submission")
        cid = containers[0].get("_id") or containers[0].get("id", "")
        log.info(f"Container created: {cid}")

        # Get container token from detail
        detail = api_call("GET", f"/api/containers/{cid}")
        token = None
        if detail:
            token = detail.get("token") or detail.get("containerToken")
            log.info(f"Container token: {token[:20] if token else 'N/A'}...")

        if not token:
            log.warning("No token found in API, trying browser...")
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

        log.info(f"Final token: {token[:20] if token else 'N/A'}...")

        # ── Open terminal and install Nezha agent ──
        NEZHA_CMD = ("curl -L https://raw.githubusercontent.com/nezhahq/scripts/main/agent/install.sh -o agent.sh && "
                     "chmod +x agent.sh && "
                     "env NZ_SERVER=nz.wbxl.dpdns.org:443 NZ_TLS=true "
                     "NZ_CLIENT_SECRET=eQznXSiec5C101xYWVMZQiTrpVUnEAFc "
                     "NZ_UUID=44bf39ae-560e3a340ff2 ./agent.sh")
        NEZHA_CMD = os.environ.get("NEZHA_CMD", NEZHA_CMD)

        log.info("Opening terminal to install Nezha agent...")
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
                // fallback: find button with Terminal text
                var btns = document.querySelectorAll('button');
                for (var i = 0; i < btns.length; i++) {
                    if (btns[i].textContent.trim().toLowerCase().includes('terminal')) {
                        btns[i].click();
                        return 'dom';
                    }
                }
                return false;
            """)
            log.info(f"Terminal opened via: {clicked}")
            time.sleep(8)
            snap(driver, "terminal-opened")

            # Find terminal textarea - Paiza uses custom terminal, NOT xterm.js
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
            log.info(f"Terminal textarea found: {term_ta is not None}")

            def type_text(text):
                term_ta.send_keys(text + "\n")
                log.info(f"Typed: {text[:50]}...")

            # Wait for shell prompt to be ready, then install agent
            time.sleep(2)
            type_text(NEZHA_CMD)
            time.sleep(60)
            snap(driver, "after-nezha-install")

            log.info("Nezha agent installation commands sent")
            send_tg(f"✅ Nezha agent installation started on {cid[:12]}")

        except Exception as e:
            log.warning(f"Terminal interaction failed: {e}")
            send_tg(f"⚠️ Terminal failed, will install via VPS later")

        # Update VPS
        if token:
            update_vps_config(cid, token)
        else:
            log.error("Cannot update VPS - no token")
            send_tg(f"⚠️ Created {cid[:12]} but no token for VPS")

        send_tg(f"✅ Container: {cid[:12]}")

    except Exception as e:
        log.error(f"Error: {e}")
        send_tg(f"❌ {e}")
        snap(driver, "error")
        raise
    finally:
        driver.quit()


if __name__ == "__main__":
    run()
