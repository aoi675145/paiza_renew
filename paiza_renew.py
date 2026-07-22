#!/usr/bin/env python3
"""
PaizaCloud Free Container Auto-Renew Script
- 每2分钟保活（防 idle 超时）
- 接近4h时自动快照恢复（重置运行计时器）
"""

import os
import sys
import time
import logging
import requests
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('paiza_renew')

BASE_URL = 'https://paiza.cloud'

CONTAINER_ID = os.getenv('PAIZA_CONTAINER_ID', '')
CONTAINER_TOKEN = os.getenv('PAIZA_CONTAINER_TOKEN', '')
RESTORE_THRESHOLD = int(os.getenv('PAIZA_RESTORE_THRESHOLD', '13500'))
SUSPEND_WAIT = int(os.getenv('PAIZA_SUSPEND_WAIT', '15'))
POST_RESTORE_WAIT = int(os.getenv('PAIZA_POST_RESTORE_WAIT', '60'))



def api_call(method, path, **kwargs):
    url = f'{BASE_URL}{path}'
    params = kwargs.pop('params', {})
    params['container_token'] = CONTAINER_TOKEN
    resp = requests.request(method, url, params=params, timeout=kwargs.pop('timeout', 30), **kwargs)
    return resp


def keepalive_short():
    """3min timeout keepalive"""
    try:
        resp = api_call('GET', f'/api/containers/{CONTAINER_ID}/keepalive')
        return resp.status_code == 200
    except Exception as e:
        logger.warning(f'keepalive error: {e}')
        return False

def keepalive_long():
    """20min timeout keepalive"""
    try:
        resp = api_call('GET', f'/api/containers/{CONTAINER_ID}/activeKeepalive')
        return resp.status_code == 200
    except Exception as e:
        logger.warning(f'activeKeepalive error: {e}')
        return False


def get_container():
    resp = api_call('GET', f'/api/containers/{CONTAINER_ID}')
    resp.raise_for_status()
    return resp.json()


def get_last_snapshot(container):
    snapshots = container.get('snapshots', [])
    if not snapshots:
        return None
    snapshots.sort(key=lambda s: s.get('createdAt', ''), reverse=True)
    return snapshots[0]


def get_uptime(container):
    created_str = container.get('containerCreatedAt', '')
    if not created_str:
        return 0
    created = datetime.fromisoformat(created_str.replace('Z', '+00:00'))
    now = datetime.now(timezone.utc)
    return (now - created).total_seconds()


def suspend_container():
    logger.info('Suspending container...')
    resp = api_call('POST', f'/api/containers/{CONTAINER_ID}/suspend', json={})
    if resp.status_code in (200, 204):
        logger.info('Container suspended')
        return True
    if resp.status_code == 500 and 'not running' in resp.text:
        logger.info('Container already suspended')
        return True
    logger.error(f'Suspend failed: {resp.status_code} {resp.text}')
    return False


def restore_snapshot(snapshot_id):
    logger.info(f'Restoring snapshot: {snapshot_id}')
    resp = api_call(
        'POST',
        f'/api/containers/{CONTAINER_ID}/snapshots/{snapshot_id}/restore',
        json={}, timeout=120
    )
    if resp.status_code == 200:
        data = resp.json()
        logger.info(f'Restore OK - new containerId: {data.get("containerIdShort", "?")}')
        return True
    logger.error(f'Restore failed: {resp.status_code} {resp.text}')
    return False


def do_renew():
    logger.warning('Starting renew cycle...')
    container = get_container()
    snapshot = get_last_snapshot(container)

    if not snapshot:
        logger.info('No snapshots, suspending to create one...')
        suspend_container()
        time.sleep(SUSPEND_WAIT)
        container = get_container()
        snapshot = get_last_snapshot(container)
        if not snapshot:
            logger.error('Still no snapshot after suspend')
            return False

    if not suspend_container():
        logger.error('Suspend failed, aborting renew')
        return False

    time.sleep(SUSPEND_WAIT)

    ok = restore_snapshot(snapshot['snapshotId'])
    time.sleep(POST_RESTORE_WAIT)
    return ok


def main():
    if not CONTAINER_ID or not CONTAINER_TOKEN:
        print('Set PAIZA_CONTAINER_ID and PAIZA_CONTAINER_TOKEN')
        sys.exit(1)

    logger.info('=== PaizaCloud Auto-Renew Started ===')
    logger.info(f'Container: {CONTAINER_ID}')

    last_renew_check = 0
    last_short_ka = 0
    last_long_ka = 0

    while True:
        try:
            now = time.time()

            # Keepalive (3min timeout): every 60s
            if now - last_short_ka >= 60:
                keepalive_short()
                last_short_ka = now

            # Active keepalive (20min timeout): every 5min
            if now - last_long_ka >= 300:
                keepalive_long()
                last_long_ka = now

            # Check uptime every 180s
            if now - last_renew_check >= 180:
                container = get_container()
                uptime = get_uptime(container)
                state = container.get('state', 'unknown')
                snap_count = len(container.get('snapshots', []))
                logger.info(f'State: {state}, Uptime: {uptime:.0f}s, Snapshots: {snap_count}')

                if uptime >= RESTORE_THRESHOLD and state not in ('creatingSnapshot', 'restoringSnapshot'):
                    do_renew()

                last_renew_check = now

            time.sleep(10)

        except KeyboardInterrupt:
            logger.info('Shutting down')
            break
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.error('Container not found, stopping')
                break
            logger.error(f'HTTP error: {e}')
            time.sleep(30)
        except Exception as e:
            logger.error(f'Error: {e}')
            time.sleep(30)


if __name__ == '__main__':
    main()
