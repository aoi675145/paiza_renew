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
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        log.warning(f"API {method} {path}: {e.code}")
        return None


def get_containers():
    return api_call("GET", "/api/containers")


def get_container_detail(cid):
    return api_call("GET", f"/api/containers/{cid}")


def get_ssh_password(cid):
    """Try to get SSH password from container detail."""
    detail = get_container_detail(cid)
    if not detail:
        return None
    # Look for SSH password in various possible fields
    for key in ['sshPassword', 'password', 'ssh_password', 'credentials']:
        if key in detail:
            return detail[key]
    # Check nested objects
    if 'ssh' in detail:
        ssh = detail['ssh']
        if isinstance(ssh, dict):
            return ssh.get('password') or ssh.get('passwordHash')
    if 'credentials' in detail:
        creds = detail['credentials']
        if isinstance(creds, dict):
            return creds.get('password')
    log.info(f"Container detail keys: {list(detail.keys())}")
    return None


# ── Main ──

def run():
    ensure_dir()
    log.info("=" * 50)
    log.info("PaizaCloud Container Auto-Create")
    log.info("=" * 50)

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
            # Maybe need to navigate to containers first
            driver.get("https://paiza.cloud/containers")
            time.sleep(3)
            snap(driver, "containers-page")

            # Click "New" button to open modal
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

        # Check containers via API
        containers = get_containers()
        if containers and len(containers) > 0:
            cid = containers[0].get("_id") or containers[0].get("id", "")
            log.info(f"Container created: {cid}")
            
            # Get container details for SSH
            detail = get_container_detail(cid)
            if detail:
                log.info(f"Container detail keys: {list(detail.keys())}")
                ssh_port = detail.get('sshPort') or detail.get('port')
                ssh_host = detail.get('sshHost') or detail.get('host')
                ssh_user = detail.get('sshUser') or detail.get('username')
                ssh_pass = get_ssh_password(cid)
                log.info(f"SSH: {ssh_host}:{ssh_port} user={ssh_user} pass={ssh_pass}")
            
            # ── Setup keepalive on VPS ──
            log.info("Setting up keepalive on VPS...")
            try:
                # Get container token from detail
                container_token = None
                if detail:
                    container_token = detail.get('token') or detail.get('containerToken')
                
                if container_token and cid:
                    # Update VPS with new container info
                    vps_host = os.environ.get("VPS_HOST", "31.22.111.169")
                    vps_port = os.environ.get("VPS_PORT", "20973")
                    vps_pass = os.environ.get("VPS_PASSWORD", "A*yS7Y5U^7yZmLdv")
                    
                    try:
                        import paramiko
                        client = paramiko.SSHClient()
                        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                        client.connect(vps_host, port=int(vps_port), username="root", password=vps_pass, timeout=30)
                        
                        # Update container info in paiza_config.txt
                        config_cmd = f"""
                        cd /root && cat > paiza_config.txt << 'EOF'
PAIZA_CONTAINER_ID={cid}
PAIZA_CONTAINER_TOKEN={container_token}
PAIZA_TOKEN={JWT_TOKEN}
EOF
"""
                        stdin, stdout, stderr = client.exec_command(config_cmd)
                        time.sleep(2)
                        
                        # Restart keepalive service
                        stdin.write("pkill -f 'python3 paiza_renew.py' || true\n")
                        stdin.flush()
                        time.sleep(1)
                        stdin.write("nohup python3 /root/paiza_renew.py > /root/paiza_renew.log 2>&1 &\n")
                        stdin.flush()
                        time.sleep(3)
                        
                        output = stdout.read().decode()
                        log.info(f"VPS setup output: {output[-500:]}")
                        
                        client.close()
                        log.info("Keepalive setup completed")
                        send_tg(f"✅ Keepalive setup on VPS for {cid[:12]}")
                        
                    except Exception as e:
                        log.error(f"VPS setup failed: {e}")
                        send_tg(f"❌ VPS setup failed: {e}")
                else:
                    log.warning("No container token available for keepalive setup")
                    
            except Exception as e:
                log.error(f"Keepalive setup error: {e}")

        else:
            log.error("No container created")
            send_tg("❌ Creation failed")
            raise RuntimeError("No container created after submission")

        # ── Get SSH password via API or network capture ──
        log.info("Getting SSH password...")
        ssh_pass = None
        
        # Method 1: Try API endpoints
        for endpoint in [f"/api/containers/{cid}/ssh", f"/api/containers/{cid}/password", f"/api/containers/{cid}/credentials"]:
            try:
                resp = api_call("GET", endpoint)
                if resp:
                    log.info(f"API {endpoint}: {resp}")
                    if isinstance(resp, dict):
                        ssh_pass = resp.get('password') or resp.get('sshPassword') or resp.get('token')
                    elif isinstance(resp, str):
                        ssh_pass = resp
                    if ssh_pass:
                        break
            except Exception:
                pass
        
        # Method 2: Try to get from container detail
        if not ssh_pass and detail:
            ssh_pass = get_ssh_password(cid)
        
        # Method 3: Try to extract from browser by opening SSH Settings
        if not ssh_pass:
            log.info("Trying to extract SSH password from browser...")
            try:
                # Enable network logging via CDP
                driver.execute_cdp_cmd("Network.enable", {})
                network_logs = []
                
                # Set up network response listener
                driver.execute_script("""
                    window._networkLogs = [];
                    var origFetch = window.fetch;
                    window.fetch = function() {
                        var url = arguments[0];
                        if (typeof url === 'string' && (url.includes('ssh') || url.includes('password') || url.includes('credential'))) {
                            window._networkLogs.push({url: url, time: Date.now()});
                        }
                        return origFetch.apply(this, arguments);
                    };
                """)
                
                # Navigate to container workspace
                driver.get(f"https://paiza.cloud/containers/{cid}/show")
                time.sleep(5)
                
                # Click "SSH Settings" button
                clicked_ssh = driver.execute_script("""
                    var btns = document.querySelectorAll('button');
                    for (var i = 0; i < btns.length; i++) {
                        var txt = btns[i].textContent.trim().toLowerCase();
                        if (txt.includes('ssh') && (txt.includes('setting') || txt.includes('info') || txt.includes('password'))) {
                            btns[i].click();
                            return true;
                        }
                    }
                    return false;
                """)
                log.info(f"SSH Settings clicked: {clicked_ssh}")
                time.sleep(3)
                snap(driver, "ssh-settings")
                
                # Try to extract password from the dialog
                ssh_pass = driver.execute_script("""
                    var pw = '';
                    
                    // Check for password input fields
                    var inputs = document.querySelectorAll('input[type="password"], input[type="text"]');
                    for (var i = 0; i < inputs.length; i++) {
                        var val = inputs[i].value;
                        if (val && val.length > 3 && val.length < 50) {
                            pw = val;
                            break;
                        }
                    }
                    
                    // Check for password in copy-to-clipboard elements
                    if (!pw) {
                        var code = document.querySelector('code, pre, .password, [class*="password"]');
                        if (code) pw = code.textContent.trim();
                    }
                    
                    // Check for password in any span/div with specific class
                    if (!pw) {
                        var els = document.querySelectorAll('[class*="ssh"], [class*="pass"], [class*="credential"]');
                        for (var i = 0; i < els.length; i++) {
                            var t = els[i].textContent.trim();
                            if (t.length > 3 && t.length < 50 && !t.includes(' ')) {
                                pw = t;
                                break;
                            }
                        }
                    }
                    
                    return pw || '';
                """)
                log.info(f"Extracted SSH password: {ssh_pass[:4] if ssh_pass else 'none'}...")
                
                # Close dialog
                driver.execute_script("""
                    var btns = document.querySelectorAll('button');
                    for (var i = 0; i < btns.length; i++) {
                        if (btns[i].textContent.trim() === 'Close' || btns[i].textContent.trim() === 'Cancel') {
                            btns[i].click();
                            return true;
                        }
                    }
                    var x = document.querySelector('.close, [aria-label="Close"]');
                    if (x) { x.click(); return true; }
                    return false;
                """)
                time.sleep(1)
                
            except Exception as e:
                log.warning(f"Failed to get SSH password from browser: {e}")
        
        # Use known values as fallback
        ssh_host = "150.95.217.173"
        ssh_port = 55067
        ssh_user = "wbxl661154"
        
        log.info(f"SSH: {ssh_host}:{ssh_port} user={ssh_user}")
        
        if ssh_pass:
            log.info(f"SSH password obtained: {ssh_pass[:4]}...")
            
            # Install Nezha agent via SSH
            try:
                import paramiko
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(ssh_host, port=int(ssh_port), username=ssh_user, password=ssh_pass, timeout=30)
                
                # Get root access
                log.info("Getting root access...")
                stdin, stdout, stderr = client.exec_command("sudo -i")
                time.sleep(2)
                
                # Install Nezha agent
                agent_cmd = (
                    "curl -L https://raw.githubusercontent.com/nezhahq/scripts/main/agent/install.sh -o agent.sh && "
                    "chmod +x agent.sh && "
                    "env NZ_SERVER=nz.wbxl.dpdns.org:443 NZ_TLS=true "
                    "NZ_CLIENT_SECRET=eQznXSiec5C101xYWVMZQiTrpVUnEAFc "
                    "NZ_UUID=44bf39ae-560e3a340ff2 ./agent.sh"
                )
                log.info("Installing Nezha agent...")
                stdin.write(f"{agent_cmd}\n")
                stdin.flush()
                
                # Wait for installation
                time.sleep(40)
                
                # Check if agent is running
                stdin.write("ps aux | grep nezha-agent\n")
                stdin.flush()
                time.sleep(5)
                
                output = stdout.read().decode()
                log.info(f"SSH output: {output[-2000:]}")
                
                client.close()
                log.info("Nezha agent installed successfully")
                send_tg(f"✅ Nezha agent installed on {cid[:12]}")
                
            except ImportError:
                log.warning("paramiko not installed, trying subprocess approach")
                # Fallback to subprocess
                cmd = f"sshpass -p '{ssh_pass}' ssh -o StrictHostKeyChecking=no -p {ssh_port} {ssh_user}@{ssh_host} 'sudo -i && {agent_cmd}'"
                import subprocess
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
                log.info(f"SSH result: {result.stdout[-500:]} {result.stderr[-500:]}")
                
            except Exception as e:
                log.error(f"SSH failed: {e}")
                send_tg(f"❌ SSH failed: {e}")
        else:
            log.warning("Could not get SSH password, trying browser terminal approach...")
            # Fallback to browser terminal - open terminal and send commands
            try:
                driver.get(f"https://paiza.cloud/containers/{cid}/show")
                time.sleep(5)
                
                # Click Terminal button
                driver.execute_script("""
                    var btns = document.querySelectorAll('button');
                    for (var i = 0; i < btns.length; i++) {
                        if (btns[i].textContent.trim().includes('Terminal')) {
                            btns[i].click();
                            return true;
                        }
                    }
                    return false;
                """)
                time.sleep(6)
                
                # Send commands via xterm.js
                agent_cmd = ("curl -L https://raw.githubusercontent.com/nezhahq/scripts/main/agent/install.sh -o agent.sh && "
                             "chmod +x agent.sh && "
                             "env NZ_SERVER=nz.wbxl.dpdns.org:443 NZ_TLS=true "
                             "NZ_CLIENT_SECRET=eQznXSiec5C101xYWVMZQiTrpVUnEAFc "
                             "NZ_UUID=44bf39ae-560e3a340ff2 ./agent.sh")
                
                js_cmds = """
                var agentCmd = arguments[0];
                var textareas = document.querySelectorAll('textarea.xterm-helper-textarea, textarea[data-xterm]');
                if (!textareas || textareas.length === 0) {
                    textareas = document.querySelectorAll('textarea');
                }
                if (!textareas || textareas.length === 0) return 'no textarea';

                var ta = textareas[textareas.length - 1];
                ta.focus();
                ta.click();

                var nativeSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLTextAreaElement.prototype, 'value'
                ).set;

                function sendKeys(text) {
                    nativeSetter.call(ta, text + '\\n');
                    ta.dispatchEvent(new Event('input', { bubbles: true }));
                }

                sendKeys('sudo -i');

                setTimeout(function() {
                    sendKeys(agentCmd);
                }, 5000);

                return 'sent';
                """

                result = driver.execute_script(js_cmds, agent_cmd)
                log.info(f"Terminal JS result: {result}")
                time.sleep(40)
                snap(driver, "after-browser-commands")
                send_tg(f"✅ Commands sent to {cid[:12]} via browser")
                
            except Exception as e:
                log.error(f"Browser terminal failed: {e}")
                send_tg(f"❌ Browser terminal failed: {e}")

        send_tg(f"✅ Container: {cid}")

    except Exception as e:
        log.error(f"Error: {e}")
        send_tg(f"❌ {e}")
        snap(driver, "error")
        raise
    finally:
        driver.quit()


if __name__ == "__main__":
    run()
