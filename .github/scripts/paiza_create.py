#!/usr/bin/env python3
"""
PaizaCloud Container Creator
Works on GitHub Actions (Ubuntu) with Chrome + NopeCHA.
Creates new container via reCAPTCHA solving, then SSH updates VPS config.
"""
import os, re, sys, time, json, urllib.request, urllib.parse, subprocess
from seleniumbase import SB

PAIZA_BASE   = "https://paiza.cloud"
JWT_TOKEN    = os.environ.get("PAIZA_TOKEN") or "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJfaWQiOiI2YTViYTFmMGIxNTE1MzY2ODM4ODczMDgiLCJyb2xlIjoidXNlciIsInNpZ25BdCI6MTc4NDQ4NTA3NjQxNCwiaWF0IjoxNzg0NDg1MDc2fQ.zp4rx4wkDvpeFe6O8YLSCQeuZVGYmz2uAO3T5XWhtJM"
TG_BOT       = os.environ.get("TG_BOT", "")
VPS_HOST     = os.environ.get("VPS_HOST", "31.22.111.169")
VPS_PASS     = os.environ.get("VPS_PASS", "A*yS7Y5U^7yZmLdv")
LOCAL_PROXY  = ""

_tg = [x.strip() for x in TG_BOT.split(",")] if TG_BOT else []
TG_CHAT_ID = _tg[0] if len(_tg) > 0 else ""
TG_TOKEN   = _tg[1] if len(_tg) > 1 else ""


def now_str():
    import datetime
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def log(msg):
    print(f"{now_str()} - {msg}", flush=True)

def send_tg(msg):
    if not TG_TOKEN: return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": TG_CHAT_ID, "text": msg}).encode()
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15): log("TG sent")
    except Exception as e: log(f"TG fail: {e}")

def find_solver_path():
    base = os.path.abspath("./nopecha")
    if os.path.exists(os.path.join(base, "manifest.json")): return base
    for entry in os.listdir(base):
        sub = os.path.join(base, entry)
        if os.path.isdir(sub) and os.path.exists(os.path.join(sub, "manifest.json")): return sub
    raise FileNotFoundError("NopeCha not found")

def wait_recaptcha(sb, timeout=180):
    log("Waiting for NopeCha to solve reCAPTCHA...")
    for i in range(timeout * 2):
        tok = sb.execute_script(
            "return (document.querySelector('textarea[name=\"g-recaptcha-response\"]')||{}).value||''"
        )
        if tok and len(tok) > 20:
            log("reCAPTCHA solved")
            return tok
        time.sleep(0.5)
    raise TimeoutError(f"reCAPTCHA timeout ({timeout}s)")

def api_create_container(name, recaptcha_token):
    url = f"{PAIZA_BASE}/api/containers"
    body = json.dumps({
        "types": [], "type": "",
        "name": name,
        "plan": "free",
        "region": "tokyo",
        "g-recaptcha-response": recaptcha_token
    }).encode()
    headers = {"Authorization": f"Bearer {JWT_TOKEN}", "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())

def get_container_token(cid):
    if not JWT_TOKEN: return ""
    url = f"{PAIZA_BASE}/api/containers/{cid}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {JWT_TOKEN}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode()).get("containerToken", "")

def create_snapshot(cid, ctoken):
    url = f"{PAIZA_BASE}/api/containers/{cid}/snapshots?container_token={ctoken}"
    req = urllib.request.Request(url, data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status == 200

def ssh_vps(cid, ctoken):
    if not VPS_HOST or not VPS_PASS:
        log("No VPS_HOST/VPS_PASS set, skipping SSH update")
        return False
    log(f"Updating VPS {VPS_HOST}...")
    cmds = f"""
kill $(pgrep -f paiza_renew) 2>/dev/null
cat > /root/paiza-renew.env << 'CFG'
PAIZA_CONTAINER_ID={cid}
PAIZA_CONTAINER_TOKEN={ctoken}
CFG
sed -i 's/^PAIZA_CONTAINER_ID=.*/PAIZA_CONTAINER_ID={cid}/' /root/paiza_renew.py 2>/dev/null || true
sed -i 's/^PAIZA_CONTAINER_TOKEN=.*/PAIZA_CONTAINER_TOKEN={ctoken}/' /root/paiza_renew.py 2>/dev/null || true
nohup python3 /root/paiza_renew.py > /root/paiza_renew.log 2>&1 &
echo "VPS updated: {cid}"
"""
    try:
        proc = subprocess.run(
            ["sshpass", "-p", VPS_PASS, "ssh", "-o", "StrictHostKeyChecking=no",
             "-p", "20973", f"root@{VPS_HOST}", cmds],
            capture_output=True, text=True, timeout=30
        )
        log(f"SSH stdout: {proc.stdout[:200]}")
        if proc.returncode != 0:
            log(f"SSH stderr: {proc.stderr[:200]}")
        return proc.returncode == 0
    except FileNotFoundError:
        log("sshpass not found, install: apt install sshpass")
        return False
    except Exception as e:
        log(f"SSH fail: {e}")
        return False

def write_local_config(cid, ctoken):
    path = os.path.join(os.path.dirname(__file__), "paiza-renew.env")
    with open(path, "w") as f:
        f.write(f"PAIZA_CONTAINER_ID={cid}\nPAIZA_CONTAINER_TOKEN={ctoken}\n")
    log(f"Local config written: {path}")

def main():
    log("=== PaizaCloud Container Auto-Create ===")
    if not JWT_TOKEN:
        log("ERROR: PAIZA_TOKEN not set")
        sys.exit(1)

    solver_path = find_solver_path()
    log(f"NopeCha: {solver_path}")

    log("Starting browser...")
    with SB(uc=False, test=True, extension_dir=solver_path,
            proxy=LOCAL_PROXY or None) as sb:
        log("Browser ready")

        if LOCAL_PROXY:
            try:
                sb.open("https://api.ipify.org/?format=json")
                ip_text = re.sub(r'(\d+\.\d+\.\d+\.)\d+', r'\1xx', sb.get_text('body'))
                log(f"IP: {ip_text}")
            except: pass

        log("Injecting auth...")
        sb.open(PAIZA_BASE)
        sb.driver.add_cookie({"name": "token", "value": JWT_TOKEN, "domain": ".paiza.cloud"})
        sb.driver.add_cookie({"name": "paiza-user-set-domain-cookie", "value": "ok", "domain": ".paiza.cloud"})

        log("Opening creation page...")
        sb.open(f"{PAIZA_BASE}/containers/new")
        time.sleep(5)
        sb.save_screenshot("debug_01_page_loaded.png")

        try:
            iframe = sb.wait_for_element_present('iframe[src*="recaptcha"]', timeout=15)
            log(f"reCAPTCHA iframe found")
        except Exception as e:
            log(f"reCAPTCHA iframe NOT found: {e}")
            sb.save_screenshot("debug_02_no_recaptcha.png")
        time.sleep(3)
        sb.save_screenshot("debug_03_before_wait.png")

        try: recaptcha_token = wait_recaptcha(sb, timeout=180)
        except TimeoutError:
            log("reCAPTCHA timeout")
            sb.save_screenshot("recaptcha_timeout.png")
            send_tg("PaizaCloud: reCAPTCHA timeout")
            sys.exit(1)

        suggested_name = sb.execute_script(
            "return document.querySelector('input[name=\"name\"]')?.value||''"
        )

    log(f"Token: {len(recaptcha_token)}c")
    name = suggested_name or f"wbxl661154-{int(time.time()) % 10000}"
    log(f"Name: {name}")

    log("Creating container...")
    try: result = api_create_container(name, recaptcha_token)
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        log(f"API fail ({e.code}): {err[:300]}")
        send_tg(f"PaizaCloud create fail: {e.code}")
        sys.exit(1)

    cid = result.get("_id", "")
    ctoken = result.get("containerToken", "")
    if not cid: log("ERROR: no ID"); sys.exit(1)
    if not ctoken: ctoken = get_container_token(cid)

    log(f"Created: {cid}")

    log("Creating snapshot...")
    try:
        create_snapshot(cid, ctoken)
        log("Snapshot ok")
    except Exception as e: log(f"Snapshot fail: {e}")

    write_local_config(cid, ctoken)
    ssh_vps(cid, ctoken)
    send_tg(f"PaizaCloud container: {cid[:12]}...")
    log(f"RESULT: {cid}|{ctoken}")
    log("=== Done ===")

if __name__ == "__main__":
    main()
