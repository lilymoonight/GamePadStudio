"""Tests for Bilibili message assistant tool (auth, client, and CLI)."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.bilibili import bilibili_auth, bilibili_client
from tools.bilibili.bilibili_client import BilibiliClient


@pytest.fixture
def tmp_creds(tmp_path, monkeypatch):
    creds_file = tmp_path / '.credentials.json'
    cache_file = tmp_path / '.cache_replies.json'
    monkeypatch.setattr(bilibili_auth, 'CREDENTIALS_PATH', creds_file)
    monkeypatch.setattr(bilibili_client, 'CACHE_PATH', cache_file)
    return creds_file, cache_file


def test_save_load_delete_credentials(tmp_creds):
    creds_file, _ = tmp_creds
    assert bilibili_auth.load_credentials() is None

    test_data = {'SESSDATA': 'sess123', 'bili_jct': 'csrf456', 'DedeUserID': '10086'}
    bilibili_auth.save_credentials(test_data)
    assert creds_file.exists()

    loaded = bilibili_auth.load_credentials()
    assert loaded == test_data

    bilibili_auth.delete_credentials()
    assert not creds_file.exists()
    assert bilibili_auth.load_credentials() is None


def test_check_auth_success(tmp_creds):
    creds = {'SESSDATA': 'sess', 'bili_jct': 'csrf', 'DedeUserID': '1'}
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        'code': 0,
        'data': {
            'isLogin': True,
            'mid': 123456,
            'uname': '测试UP主',
            'money': 50,
            'level_info': {'current_level': 6},
        },
    }

    with patch('requests.get', return_value=mock_resp):
        res = bilibili_auth.check_auth(creds)
        assert res['ok'] is True
        assert res['uname'] == '测试UP主'
        assert res['level'] == 6


def test_poll_qrcode_success(tmp_creds):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        'code': 0,
        'data': {
            'code': 0,
            'url': 'https://passport.biligame.com/crossDomain?SESSDATA=mysess&bili_jct=mycsrf&DedeUserID=888',
            'message': '扫码成功',
        },
    }
    mock_resp.cookies = {}

    with patch('requests.Session.get', return_value=mock_resp):
        status_updates = []
        res = bilibili_auth.poll_qrcode('dummy_key', timeout=2, on_status=lambda c, m: status_updates.append(c))
        assert res['ok'] is True
        assert res['credentials']['SESSDATA'] == 'mysess'
        assert res['credentials']['bili_jct'] == 'mycsrf'

        # Verified stored
        loaded = bilibili_auth.load_credentials()
        assert loaded['SESSDATA'] == 'mysess'


def test_fetch_reply_messages_parsing_and_cache(tmp_creds):
    _, cache_file = tmp_creds
    creds = {'SESSDATA': 'sess', 'bili_jct': 'csrf', 'DedeUserID': '888'}
    client = BilibiliClient(creds)

    mock_nav = MagicMock()
    mock_nav.json.return_value = {'code': 0, 'data': {'isLogin': True, 'uname': 'UP主'}}

    mock_replies = MagicMock()
    mock_replies.json.return_value = {
        'code': 0,
        'data': {
            'items': [
                {
                    'id': 101,
                    'user': {'mid': 999, 'nickname': '热心网友'},
                    'item': {
                        'title': 'DualSense 在 Mac 上的最佳配置指南',
                        'source_content': '请问摇杆的死区怎么调？',
                        'target_reply_content': '这是视频介绍',
                        'reply_time': 1728200000,
                        'subject_id': 1234567,
                        'business_id': 1,
                        'source_id': 789,
                        'root_id': 0,
                    },
                }
            ]
        },
    }

    def fake_get(url, **kwargs):
        if 'nav' in url:
            return mock_nav
        return mock_replies

    with patch.object(client.session, 'get', side_effect=fake_get):
        res = client.fetch_reply_messages()
        assert res['ok'] is True
        assert len(res['items']) == 1
        item = res['items'][0]
        assert item['index'] == 1
        assert item['user_name'] == '热心网友'
        assert item['content'] == '请问摇杆的死区怎么调？'
        assert item['oid'] == 1234567

        # Check cache
        cached = BilibiliClient.get_cached_reply(1)
        assert cached is not None
        assert cached['user_name'] == '热心网友'


def test_post_reply_success(tmp_creds):
    creds = {'SESSDATA': 'sess', 'bili_jct': 'csrf_token', 'DedeUserID': '888'}
    client = BilibiliClient(creds)

    mock_nav = MagicMock()
    mock_nav.json.return_value = {'code': 0, 'data': {'isLogin': True}}

    mock_post = MagicMock()
    mock_post.json.return_value = {
        'code': 0,
        'data': {'rpid': 998877},
        'message': 'success',
    }

    def fake_get(url, **kwargs):
        return mock_nav

    with patch.object(client.session, 'get', side_effect=fake_get), \
         patch.object(client.session, 'post', return_value=mock_post) as mock_post_call:
        res = client.post_reply(oid=12345, type_id=1, message='在手感设置里可以拖动滑块调整死区！')
        assert res['ok'] is True
        assert res['rpid'] == 998877

        # Verify CSRF was passed
        args, kwargs = mock_post_call.call_args
        assert kwargs['data']['csrf'] == 'csrf_token'
        assert kwargs['data']['message'] == '在手感设置里可以拖动滑块调整死区！'


def test_cli_whoami_and_logout(tmp_creds, capsys):
    from tools.bilibili import cli
    from argparse import Namespace

    # 1. When not logged in
    with pytest.raises(SystemExit):
        cli.cmd_whoami(Namespace())
    out = capsys.readouterr().out
    assert '未登录或登录已失效' in out

    # 2. When logged in
    bilibili_auth.save_credentials({'SESSDATA': 's', 'bili_jct': 'c'})
    mock_auth = {'ok': True, 'is_login': True, 'uname': '我的账号', 'mid': 12345, 'level': 5, 'money': 100}
    with patch('tools.bilibili.cli.check_auth', return_value=mock_auth):
        cli.cmd_whoami(Namespace())
    out = capsys.readouterr().out
    assert '我的账号' in out
    assert 'UID: 12345' in out

    # 3. Logout
    cli.cmd_logout(Namespace())
    assert not tmp_creds[0].exists()


def test_cli_reply_with_cached_id(tmp_creds, capsys):
    from tools.bilibili import cli
    from argparse import Namespace

    # Save a fake cached reply
    _, cache_file = tmp_creds
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump([
            {
                'index': 1,
                'user_name': '测试小伙伴',
                'title': '测试视频',
                'oid': 777777,
                'type': 1,
                'root': 111,
                'parent': 222,
                'content': '好棒！',
            }
        ], f)

    with patch.object(BilibiliClient, 'post_reply', return_value={'ok': True, 'rpid': 99999, 'message': '回复成功'}) as mock_post:
        args = Namespace(id=1, oid=None, type=1, root=0, parent=0, msg='谢谢支持！')
        cli.cmd_reply(args)

    out = capsys.readouterr().out
    assert '回复成功' in out
    mock_post.assert_called_with(oid=777777, type_id=1, message='谢谢支持！', root=111, parent=222)

