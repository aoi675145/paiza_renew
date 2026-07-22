#!/usr/bin/env python3
import os, sys, time, logging, requests
from datetime import datetime, timezone
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('paiza_renew')
BASE_URL = 'https://paiza.cloud'
CONTAINER_ID = os.getenv('PAIZA_CONTAINER_ID', '')
CONTAINER_TOKEN = os.getenv('PAIZA_CONTAINER_TOKEN', '')
JWT_TOKEN = os.getenv('PAIZA_JWT', '')
RESTORE_THRESHOLD = int(os.getenv('PAIZA_RESTORE_THRESHOLD', '13500'))
SUSPEND_WAIT = int(os.getenv('PAIZA_SUSPEND_WAIT', '15'))
POST_RESTORE_WAIT = int(os.getenv('PAIZA_POST_RESTORE_WAIT', '60'))
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GITHUB_REPO = 'aoi675145/paiza_renew'

def api_call(method, path, **kwargs):
    params = kwargs.pop('params', {}); params['container_token'] = CONTAINER_TOKEN
    return requests.request(method, f'{BASE_URL}{path}', params=params, timeout=kwargs.pop('timeout', 30), **kwargs)

def keepalive_short():
    try: return api_call('GET', f'/api/containers/{CONTAINER_ID}/keepalive').status_code == 200
    except: return False

def keepalive_long():
    try: return api_call('GET', f'/api/containers/{CONTAINER_ID}/activeKeepalive').status_code == 200
    except: return False

def get_container():
    resp = api_call('GET', f'/api/containers/{CONTAINER_ID}'); resp.raise_for_status(); return resp.json()

def get_last_snapshot(container):
    snaps = container.get('snapshots', [])
    if not snaps: return None
    snaps.sort(key=lambda s: s.get('createdAt', ''), reverse=True); return snaps[0]

def get_uptime(container):
    created_str = container.get('containerCreatedAt', '')
    if not created_str: return 0
    created = datetime.fromisoformat(created_str.replace('Z', '+00:00'))
    return (datetime.now(timezone.utc) - created).total_seconds()

def suspend_container():
    logger.info('Suspending container...')
    resp = api_call('POST', f'/api/containers/{CONTAINER_ID}/suspend', json={})
    if resp.status_code in (200, 204): logger.info('Container suspended'); return True
    if resp.status_code == 500 and 'not running' in resp.text: logger.info('Already suspended'); return True
    logger.error(f'Suspend failed: {resp.status_code}'); return False

def restore_snapshot(sid):
    logger.info(f'Restoring snapshot {sid}')
    r = api_call('POST', f'/api/containers/{CONTAINER_ID}/snapshots/{sid}/restore', json={}, timeout=120)
    if r.status_code == 200: logger.info('Restore OK'); return True
    logger.error(f'Restore failed: {r.status_code} {r.text[:200]}'); return False

def do_renew():
    logger.warning('Starting renew cycle (pre-emptive reset at ~3h45m)...')
    container = get_container()
    snapshot = get_last_snapshot(container)
    if not snapshot:
        logger.info('No snapshots, suspending to create one...')
        suspend_container(); time.sleep(SUSPEND_WAIT)
        container = get_container(); snapshot = get_last_snapshot(container)
        if not snapshot: logger.error('Still no snapshot after suspend'); return False
    if not suspend_container(): return False
    time.sleep(SUSPEND_WAIT)
    ok = restore_snapshot(snapshot['snapshotId'])
    time.sleep(POST_RESTORE_WAIT)
    return ok

def trigger_github_workflow():
    logger.info('Triggering GitHub Actions workflow...')
    url = f'https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/paiza-create.yml/dispatches'
    headers = {'Authorization': f'Bearer {GITHUB_TOKEN}', 'Accept': 'application/vnd.github.v3+json'}
    r = requests.post(url, json={'ref': 'main'}, headers=headers, timeout=30)
    if r.status_code == 204: logger.info('Workflow triggered'); return True
    logger.error(f'Trigger failed: {r.status_code} {r.text[:200]}'); return False

def is_expired(container):
    if container.get('state') == 'suspended':
        created = container.get('createdAt', '')
        if created:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(created.replace('Z', '+00:00'))).total_seconds()
            if age > 86000: return True
    return False

def main():
    if not CONTAINER_ID or not CONTAINER_TOKEN: print('Set PAIZA_CONTAINER_ID and PAIZA_CONTAINER_TOKEN'); sys.exit(1)
    logger.info('=== PaizaCloud Auto-Renew Started ===')
    last_short_ka = last_long_ka = last_check = suspended_since = 0
    while True:
        try:
            now = time.time()
            if now - last_short_ka >= 60: keepalive_short(); last_short_ka = now
            if now - last_long_ka >= 300: keepalive_long(); last_long_ka = now
            if now - last_check < 60: time.sleep(10); continue
            last_check = now
            c = get_container(); st = c.get('state','?'); cid = c.get('containerIdShort','') or '-'
            uptime = get_uptime(c)
            logger.info(f'{st} uptime:{uptime:.0f}s containerId:{cid}')

            # Container expired (past 24h lifecycle)
            if st == 'expired' or (st == 'suspended' and is_expired(c)):
                logger.warning('Container expired, triggering workflow...')
                if trigger_github_workflow(): break
                else: logger.error('Trigger failed, retry in 5min'); time.sleep(300); continue

            # Pre-emptive renew at ~3h45m to reset 4h timer
            if uptime >= RESTORE_THRESHOLD and st not in ('creatingSnapshot', 'restoringSnapshot'):
                do_renew()

            # Fallback: if suspended, try restore (shouldn't happen if proactive works)
            if st == 'suspended':
                if suspended_since == 0: suspended_since = now; logger.info('Suspended, attempting restore...')
                if now - suspended_since >= 60:
                    snap = get_last_snapshot(c)
                    if snap and restore_snapshot(snap['snapshotId']): suspended_since = 0; time.sleep(POST_RESTORE_WAIT)
                    else: suspended_since = now
            else: suspended_since = 0

            time.sleep(10)
        except KeyboardInterrupt: break
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in (403, 404, 410):
                logger.error(f'Container gone ({e.response.status_code}), triggering workflow...')
                if trigger_github_workflow(): break
            time.sleep(30)
        except Exception as e: logger.error(f'Error {e}'); time.sleep(30)

if __name__ == '__main__': main()
