"""Bilibili QR code authentication and credential management."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable, Dict, Optional
from urllib.parse import parse_qs, urlparse

import qrcode
import requests

CREDENTIALS_PATH = Path(__file__).resolve().parent / '.credentials.json'
USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
)


def load_credentials() -> Optional[Dict[str, str]]:
    """Load credentials from local json file if exists."""
    if not CREDENTIALS_PATH.exists():
        return None
    try:
        with open(CREDENTIALS_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if data.get('SESSDATA') and data.get('bili_jct'):
                return data
    except Exception:
        pass
    return None


def save_credentials(creds: Dict[str, str]):
    """Persist credentials securely."""
    with open(CREDENTIALS_PATH, 'w', encoding='utf-8') as f:
        json.dump(creds, f, indent=2, ensure_ascii=False)
    try:
        os.chmod(CREDENTIALS_PATH, 0o600)
    except Exception:
        pass


def delete_credentials():
    """Remove credentials file."""
    if CREDENTIALS_PATH.exists():
        CREDENTIALS_PATH.unlink()


def check_auth(creds: Optional[Dict[str, str]] = None) -> Dict:
    """Verify credentials with Bilibili navigation API."""
    if creds is None:
        creds = load_credentials()
    if not creds:
        return {'ok': False, 'message': '未找到本地凭证，请先扫码登录'}

    headers = {
        'User-Agent': USER_AGENT,
        'Referer': 'https://www.bilibili.com/',
    }
    cookies = {
        'SESSDATA': creds.get('SESSDATA', ''),
        'bili_jct': creds.get('bili_jct', ''),
        'DedeUserID': creds.get('DedeUserID', ''),
    }

    try:
        resp = requests.get(
            'https://api.bilibili.com/x/web-interface/nav',
            headers=headers,
            cookies=cookies,
            timeout=10
        )
        data = resp.json()
        if data.get('code') == 0 and data.get('data', {}).get('isLogin'):
            user_data = data['data']
            return {
                'ok': True,
                'is_login': True,
                'mid': user_data.get('mid'),
                'uname': user_data.get('uname'),
                'money': user_data.get('money'),
                'level': user_data.get('level_info', {}).get('current_level'),
                'avatar': user_data.get('face'),
                'vip_type': user_data.get('vipType'),
            }
        else:
            return {
                'ok': False,
                'is_login': False,
                'message': data.get('message', '登录凭证已过期或无效'),
            }
    except Exception as e:
        return {'ok': False, 'is_login': False, 'message': f'网络请求失败: {e}'}


def request_qrcode() -> Dict:
    """Request a new QR code key and scan URL from Bilibili."""
    headers = {
        'User-Agent': USER_AGENT,
        'Referer': 'https://passport.bilibili.com/login',
    }
    resp = requests.get(
        'https://passport.bilibili.com/x/passport-login/web/qrcode/generate',
        headers=headers,
        timeout=10
    )
    res = resp.json()
    if res.get('code') == 0:
        return {
            'ok': True,
            'url': res['data']['url'],
            'qrcode_key': res['data']['qrcode_key'],
        }
    return {'ok': False, 'message': res.get('message', '生成二维码失败')}


def render_qrcode(url: str, open_image: bool = True) -> str:
    """Render QR code in terminal and save a temporary PNG image.

    Returns the path to the saved PNG image.
    """
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)

    print('\n' + '=' * 48)
    print('  请使用哔哩哔哩 (Bilibili) 手机 App 扫描下方二维码')
    print('=' * 48 + '\n')

    # Print terminal ASCII QR
    qr.print_ascii(invert=True)
    print('\n' + '-' * 48)

    # Save to image
    img_path = '/tmp/bilibili_qr_login.png'
    try:
        img = qr.make_image(fill_color='black', back_color='white')
        img.save(img_path)
        print(f'已生成二维码图片: {img_path}')
        if open_image and sys.platform == 'darwin':
            try:
                subprocess.Popen(['open', img_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print('已自动为您弹出二维码预览窗口，请扫码。')
            except Exception:
                pass
    except Exception as e:
        print(f'保存图片失败: {e}')

    return img_path


def poll_qrcode(
    qrcode_key: str,
    timeout: int = 180,
    interval: float = 1.5,
    on_status: Optional[Callable[[int, str], None]] = None
) -> Dict:
    """Poll Bilibili QR login status until success, timeout or expiration."""
    start_time = time.time()
    headers = {
        'User-Agent': USER_AGENT,
        'Referer': 'https://passport.bilibili.com/login',
    }

    session = requests.Session()
    last_code = None

    while time.time() - start_time < timeout:
        try:
            resp = session.get(
                'https://passport.bilibili.com/x/passport-login/web/qrcode/poll',
                params={'qrcode_key': qrcode_key},
                headers=headers,
                timeout=10
            )
            res = resp.json()
            data = res.get('data', {})
            code = data.get('code')
            msg = data.get('message', '')

            if code != last_code:
                last_code = code
                if on_status:
                    on_status(code, msg)

            if code == 0:
                # Login success! Extract cookies from response and redirect URL
                creds = {}

                # 1. From response cookies
                for c_name in ('SESSDATA', 'bili_jct', 'DedeUserID', 'DedeUserID__ckMd5'):
                    if c_name in resp.cookies:
                        creds[c_name] = resp.cookies[c_name]

                # 2. From redirect URL parameters
                url = data.get('url', '')
                if url:
                    parsed = urlparse(url)
                    qs = parse_qs(parsed.query)
                    for k in ('SESSDATA', 'bili_jct', 'DedeUserID', 'DedeUserID__ckMd5'):
                        if k in qs and qs[k]:
                            creds[k] = qs[k][0]

                if 'refresh_token' in data:
                    creds['refresh_token'] = data['refresh_token']
                creds['updated_at'] = int(time.time())

                save_credentials(creds)
                return {
                    'ok': True,
                    'code': 0,
                    'message': '登录成功',
                    'credentials': creds,
                }
            elif code == 86038:
                # 二维码未扫码
                pass
            elif code == 86090:
                # 已扫码未确认
                pass
            elif code == 86039:
                # 二维码已失效
                return {'ok': False, 'code': 86039, 'message': '二维码已过期，请重新生成'}
            else:
                return {'ok': False, 'code': code, 'message': msg or f'未知状态 ({code})'}

        except Exception as e:
            if on_status:
                on_status(-1, f'轮询出错: {e}')

        time.sleep(interval)

    return {'ok': False, 'code': -2, 'message': '登录超时，请重试'}
