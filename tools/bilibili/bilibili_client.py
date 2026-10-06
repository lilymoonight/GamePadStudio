"""Bilibili API client for fetching message replies and sending comments."""
from datetime import datetime
import json
from pathlib import Path
import time
from typing import Dict, List, Optional, Union

import requests

from .bilibili_auth import USER_AGENT, check_auth, load_credentials

CACHE_PATH = Path(__file__).resolve().parent / '.cache_replies.json'


class BilibiliClient:
    """Client for Bilibili message feed and reply actions."""

    def __init__(self, creds: Optional[Dict[str, str]] = None):
        self.creds = creds or load_credentials()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': USER_AGENT,
            'Referer': 'https://message.bilibili.com/',
            'Origin': 'https://message.bilibili.com',
        })
        if self.creds:
            self.session.cookies.set('SESSDATA', self.creds.get('SESSDATA', ''))
            self.session.cookies.set('bili_jct', self.creds.get('bili_jct', ''))
            self.session.cookies.set('DedeUserID', str(self.creds.get('DedeUserID', '')))

    @property
    def csrf(self) -> str:
        return (self.creds or {}).get('bili_jct', '')

    def is_logged_in(self) -> bool:
        if not self.creds or not self.creds.get('SESSDATA'):
            return False
        info = self.get_my_info()
        return bool(info.get('ok') and info.get('is_login'))

    def get_my_info(self) -> Dict:
        if not self.creds or not self.creds.get('SESSDATA'):
            return {'ok': False, 'is_login': False, 'message': '未登录'}
        try:
            resp = self.session.get('https://api.bilibili.com/x/web-interface/nav', timeout=8)
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
                }
            return {'ok': False, 'is_login': False, 'message': data.get('message', '登录失效')}
        except Exception as e:
            return {'ok': False, 'is_login': False, 'message': f'请求失败: {e}'}

    def fetch_reply_messages(self, limit: int = 20) -> Dict:
        """Fetch reply messages from message center (https://message.bilibili.com/#/reply).

        Returns normalized dictionary with parsed items and raw response.
        """
        if not self.is_logged_in():
            return {
                'ok': False,
                'message': '未登录或登录态失效，请先运行 login 命令扫码登录。',
                'items': [],
            }

        url = 'https://api.bilibili.com/x/msgfeed/reply'
        params = {
            'build': 0,
            'mobi_app': 'web',
        }

        try:
            resp = self.session.get(url, params=params, timeout=12)
            data = resp.json()
            if data.get('code') != 0:
                return {
                    'ok': False,
                    'code': data.get('code'),
                    'message': data.get('message', '获取消息失败'),
                    'items': [],
                }

            raw_items = data.get('data', {}).get('items', []) or []
            parsed_list: List[Dict] = []

            for idx, raw in enumerate(raw_items[:limit], start=1):
                user = raw.get('user', {}) or {}
                item = raw.get('item', {}) or {}

                # Format timestamp
                reply_ts = item.get('reply_time') or raw.get('reply_time') or int(time.time())
                try:
                    time_str = datetime.fromtimestamp(reply_ts).strftime('%Y-%m-%d %H:%M')
                except Exception:
                    time_str = str(reply_ts)

                # Determine target IDs for reply
                # In Bilibili reply message:
                # - subject_id is the video/article oid
                # - business_id is the type (1 for video, 12 for article, etc.)
                # - source_id is the comment rpid of the newly posted reply
                # - root_id is the root comment ID
                # - target_id is the parent comment ID
                oid = item.get('subject_id') or item.get('target_id') or 0
                business_id = item.get('business_id', 1)
                source_id = item.get('source_id', 0)
                root_id = item.get('root_id', 0)
                target_id = item.get('target_id', 0)

                # If root_id is 0 or missing, but replying to a comment, the target comment might be root
                effective_root = root_id if root_id else source_id
                effective_parent = source_id

                base_uri = item.get('uri', '')
                direct_url = f"{base_uri}#reply{source_id}" if base_uri and source_id else base_uri

                parsed_entry = {
                    'index': idx,
                    'id': raw.get('id', idx),
                    'user_name': user.get('nickname') or user.get('name') or '匿名网友',
                    'user_mid': user.get('mid', 0),
                    'user_avatar': user.get('avatar', ''),
                    'time': time_str,
                    'title': item.get('title') or '视频/动态',
                    'content': item.get('source_content', '').strip(),
                    'target_content': item.get('target_reply_content', '').strip(),
                    'uri': base_uri,
                    'comment_url': direct_url,
                    # Routing IDs for reply
                    'oid': oid,
                    'type': business_id,
                    'root': effective_root,
                    'parent': effective_parent,
                    'raw': raw,
                }
                parsed_list.append(parsed_entry)

            # Cache the parsed replies locally so they can be referenced by index
            try:
                with open(CACHE_PATH, 'w', encoding='utf-8') as f:
                    json.dump(parsed_list, f, indent=2, ensure_ascii=False)
            except Exception:
                pass

            return {
                'ok': True,
                'total': len(parsed_list),
                'items': parsed_list,
            }

        except Exception as e:
            return {'ok': False, 'message': f'请求异常: {e}', 'items': []}

    def post_reply(
        self,
        oid: int,
        type_id: int,
        message: str,
        root: int = 0,
        parent: int = 0,
    ) -> Dict:
        """Post a reply to a comment or video.

        - oid: Subject ID (e.g. video avid)
        - type_id: 1 for video, 12 for read/article, 11/17 for dynamic
        - message: Reply text
        - root: Root comment ID (0 if direct video comment)
        - parent: Target comment ID
        """
        if not self.is_logged_in():
            return {'ok': False, 'message': '未登录或登录态失效'}

        if not self.csrf:
            return {'ok': False, 'message': '缺少 bili_jct (CSRF Token)'}

        if not message.strip():
            return {'ok': False, 'message': '回复内容不能为空'}

        url = 'https://api.bilibili.com/x/v2/reply/add'
        data = {
            'oid': oid,
            'type': type_id,
            'message': message.strip(),
            'root': root,
            'parent': parent,
            'csrf': self.csrf,
        }

        headers = {
            'User-Agent': USER_AGENT,
            'Referer': f'https://www.bilibili.com/video/av{oid}' if type_id == 1 else 'https://www.bilibili.com/',
            'Origin': 'https://www.bilibili.com',
        }

        try:
            resp = self.session.post(url, data=data, headers=headers, timeout=12)
            res = resp.json()
            code = res.get('code')
            if code == 0:
                reply_data = res.get('data', {})
                return {
                    'ok': True,
                    'code': 0,
                    'message': '回复发送成功！',
                    'rpid': reply_data.get('rpid'),
                    'data': reply_data,
                }
            else:
                msg = res.get('message', '')
                return {
                    'ok': False,
                    'code': code,
                    'message': f'发送失败 ({code}): {msg}',
                }
        except Exception as e:
            return {'ok': False, 'message': f'网络请求异常: {e}'}

    @staticmethod
    def get_cached_reply(index: int) -> Optional[Dict]:
        """Look up cached reply by 1-based index."""
        if not CACHE_PATH.exists():
            return None
        try:
            with open(CACHE_PATH, 'r', encoding='utf-8') as f:
                items = json.load(f)
                for item in items:
                    if item.get('index') == index:
                        return item
        except Exception:
            pass
        return None
