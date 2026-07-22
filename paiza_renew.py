#!/usr/bin/env python3
"""
PaizaCloud Free Container Auto-Renew Script
- Keepalive to prevent idle timeout
- Delete + trigger rebuild when approaching 4h
"""

import os, sys, time, logging, requests
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('paiza_renew')

BASE_URL = 'https://paiza.cloud'
GITHUB_API = 'https://api.github.com'

CONTAINER_ID = os.getenv('PAIZA_CONTAINER_ID', '')
CONTAINER_TOKEN = os.getenv('PAIZA_CONTAINER_TOKEN', '')
JWT_TOKEN = os.getenv('PAIZA_JWT', '')
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN', '')
RENEW_THRESHOLD = int(os.getenv('PAIZA_RENEW_THRESHOLD', '13500'))


def api_call(method, path, **kwargs):
    url = f'{BASE_URL}{path}'
    params = kwargs.pop('params', {})
    params['container_token'] = CONTAINER_TOKEN
    return requests.request(method, url, params=params, timeout=kwargs.pop('timeout', 60), **kwargs)


def get_container():
    resp = api_call('GET', f'/api/containers/{CONTAINER_ID}')
    resp.raise_for_status()
    return resp.json()


def get_uptime(container):
    created_str = container.get('containerCreatedAt', '')
    if not created_str:
        return 0
    created = datetime.fromisoformat(created_str.replace('Z', '+00:00'))
    now = datetime.now(timezone.utc)
    return (now - created).total_seconds()


def trigger_workflow():
    logger.info('Triggering recreate workflow...')
    try:
        headers = {
            'Authorization': f'Bearer {GITHUB_TOKEN}',
            'Accept': 'application/vnd.github.v3+json',
        }
        r = requests.post(
            f'{GITHUB_API}/repos/aoi675145/paiza_renew/actions/workflows/paiza-create.yml/dispatches',
            json={'ref': 'main'},
            headers=headers,
            timeout=30
        )
        if r.status_code == 204:
            logger.info('Workflow triggered')
            return True
        logger.error(f'Trigger failed: {r.status_code} {r.text}')
        return False
    except Exception as e:
        logger.error(f'Trigger error: {e}')
        return False


def do_renew():
    logger.warning('Starting renew cycle...')
    if not trigger_workflow():
        logger.error('Workflow trigger failed, aborting')
        return False
    logger.warning('Renew cycle complete, exiting')
    return True


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

            if now - last_short_ka >= 60:
                try:
                    api_call('GET', f'/api/containers/{CONTAINER_ID}/keepalive')
                except Exception:
                    pass
                last_short_ka = now

            if now - last_long_ka >= 300:
                try:
                    api_call('GET', f'/api/containers/{CONTAINER_ID}/activeKeepalive')
                except Exception:
                    pass
                last_long_ka = now

            if now - last_renew_check >= 180:
                container = get_container()
                uptime = get_uptime(container)
                state = container.get('state', 'unknown')
                logger.info(f'State: {state}, Uptime: {uptime:.0f}s')

                if uptime >= RENEW_THRESHOLD:
                    if do_renew():
                        sys.exit(0)

                last_renew_check = now

            time.sleep(10)

        except KeyboardInterrupt:
            logger.info('Shutting down')
            break
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.error('Container not found, stopping')
                sys.exit(1)
            logger.error(f'HTTP error: {e}')
            time.sleep(30)
        except Exception as e:
            logger.error(f'Error: {e}')
            time.sleep(30)


if __name__ == '__main__':
    main()
