#!/usr/bin/env python3
import os, sys, time, logging, requests
from datetime import datetime, timezone
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('paiza_renew')
BASE_URL = 'https://paiza.cloud'
CONTAINER_ID = os.getenv('PAIZA_CONTAINER_ID', '')
CONTAINER_TOKEN = os.getenv('PAIZA_CONTAINER_TOKEN', '')
JWT_TOKEN = os.getenv('PAIZA_JWT', '')
POST_RESTORE_WAIT = int(os.getenv('PAIZA_POST_RESTORE_WAIT', '30'))
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

def restore_snapshot(sid):
    logger.info(f'Restoring {sid}')
    r = api_call('POST', f'/api/containers/{CONTAINER_ID}/snapshots/{sid}/restore', json={}, timeout=120)
    if r.status_code == 200: logger.info('Restore OK'); return True
    logger.error(f'Restore failed: {r.status_code}'); return False

def trigger_github_workflow():
    logger.info('Triggering GitHub Actions workflow...')
    url = f'https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/paiza-create.yml/dispatches'
    headers = {'Authorization': f'Bearer {GITHUB_TOKEN}', 'Accept': 'application/vnd.github.v3+json'}
    r = requests.post(url, json={'ref': 'main'}, headers=headers, timeout=30)
    if r.status_code == 204:
        logger.info('Workflow triggered successfully')
        return True
    logger.error(f'Trigger failed: {r.status_code} {r.text[:200]}')
    return False

def is_expired(container):
    if container.get('state') == 'suspended':
        created = container.get('createdAt', '')
        if created:
            created_dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
            age = (datetime.now(timezone.utc) - created_dt).total_seconds()
            if age > 86000:
                return True
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
            logger.info(f'{st} containerId:{cid}')
            if st == 'expired' or (st == 'suspended' and is_expired(c)):
                logger.warning('Container expired, triggering workflow...')
                if trigger_github_workflow():
                    logger.info('Workflow triggered, exiting')
                    break
                else:
                    logger.error('Failed to trigger workflow, retrying in 5min')
                    time.sleep(300)
                    continue
            if st == 'suspended':
                if suspended_since == 0: suspended_since = now; logger.info('Suspended, waiting cleanup...')
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
