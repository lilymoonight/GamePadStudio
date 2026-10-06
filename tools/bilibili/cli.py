#!/usr/bin/env python3
"""CLI interface for Bilibili message reading and replying."""
import argparse
import json
import sys
from typing import Optional

from .bilibili_auth import (
    check_auth,
    delete_credentials,
    load_credentials,
    poll_qrcode,
    render_qrcode,
    request_qrcode,
)
from .bilibili_client import BilibiliClient


def cmd_whoami(args):
    """Check and display current login status."""
    status = check_auth()
    if status.get('ok') and status.get('is_login'):
        print('\n✅ 当前已成功登录 B 站账号：')
        print(f"   👤 昵称: {status.get('uname')}")
        print(f"   🆔 UID: {status.get('mid')}")
        print(f"   ⭐ 等级: Lv{status.get('level', '?')}")
        print(f"   💰 硬币: {status.get('money', 0)}")
    else:
        print(f"\n❌ 未登录或登录已失效: {status.get('message')}")
        print('💡 请运行命令完成扫码登录: python -m tools.bilibili.cli login')
        sys.exit(1)


def cmd_login(args):
    """Generate QR code and wait for scan."""
    creds = load_credentials()
    if creds and not getattr(args, 'force', False):
        status = check_auth(creds)
        if status.get('ok'):
            print(f"✅ 您当前已登录为: {status.get('uname')} (UID: {status.get('mid')})")
            print('💡 若需切换账号，请添加 --force 参数强制重新登录。')
            return

    print('📡 正在向 B 站请求登录二维码...')
    qr_res = request_qrcode()
    if not qr_res.get('ok'):
        print(f"❌ 获取二维码失败: {qr_res.get('message')}")
        sys.exit(1)

    url = qr_res['url']
    qrcode_key = qr_res['qrcode_key']

    # Render terminal ASCII and open image
    render_qrcode(url, open_image=True)

    print('⏳ 等待手机 B 站 App 扫码确认中（有效时间 180 秒）...')

    def on_status_change(code, msg):
        if code in (86101, 86038):
            print('   [等待] 请在手机 B 站 App 中扫码...')
        elif code == 86090:
            print('   [已扫码] 手机端已扫描，请在手机上点击“确认登录”！')
        elif code in (86039,):
            print('   [过期] 二维码已超时失效。')
        elif code != 0:
            print(f'   [状态] {msg} ({code})')

    poll_res = poll_qrcode(qrcode_key, timeout=180, on_status=on_status_change)
    if poll_res.get('ok'):
        print('\n🎉 登录成功！凭证已安全保存至 tools/bilibili/.credentials.json')
        cmd_whoami(args)
    else:
        print(f"\n❌ 登录失败: {poll_res.get('message')}")
        sys.exit(1)


def cmd_check(args):
    """Fetch recent unread replies from message center."""
    client = BilibiliClient()
    limit = getattr(args, 'limit', 15) or 15
    res = client.fetch_reply_messages(limit=limit)

    if not res.get('ok'):
        print(f"❌ 获取回复失败: {res.get('message')}")
        if '未登录' in res.get('message', ''):
            print('💡 请先运行: python -m tools.bilibili.cli login')
        sys.exit(1)

    items = res.get('items', [])
    if getattr(args, 'json', False):
        print(json.dumps(items, indent=2, ensure_ascii=False))
        return

    print('\n' + '=' * 68)
    print(f'  📬 Bilibili 消息中心最新评论与回复 (共 {len(items)} 条)')
    print('=' * 68)

    if not items:
        print('暂无新的回复消息。')
        print('-' * 68)
        return

    for item in items:
        idx = item['index']
        user = item['user_name']
        t_str = item['time']
        title = item['title']
        content = item['content']
        target = item.get('target_content')

        print(f"\n【序号 {idx}】 来自 @{user} ({t_str})")
        print(f"   📺 所属内容: 《{title}》")
        if target:
            print(f"   💬 引用原句: “{target}”")
        print(f"   📝 网友评论: {content}")
        print(f"   🔗 路由参数: oid={item['oid']} type={item['type']} root={item['root']} parent={item['parent']}")
        print('-' * 68)

    print('\n💡 提示：如需回复某条评论，可直接运行：')
    print('   python -m tools.bilibili.cli reply --id <序号> --msg "您的回复内容"')


def cmd_reply(args):
    """Post a reply to a specified comment."""
    client = BilibiliClient()

    oid = getattr(args, 'oid', None)
    type_id = getattr(args, 'type', 1)
    root = getattr(args, 'root', 0)
    parent = getattr(args, 'parent', 0)
    msg = getattr(args, 'msg', '') or ''

    # If --id was specified, auto-resolve from cache
    idx = getattr(args, 'id', None)
    target_info = ''
    if idx is not None:
        cached = BilibiliClient.get_cached_reply(idx)
        if not cached:
            print(f"❌ 未找到序号为 [{idx}] 的缓存评论，请先运行 `check` 命令刷新列表。")
            sys.exit(1)
        oid = cached['oid']
        type_id = cached['type']
        root = cached['root']
        parent = cached['parent']
        target_info = f"对 @{cached['user_name']} 的评论（《{cached['title']}》）"

    if not oid or not msg.strip():
        print('❌ 参数不完整：必须指定有效的内容 ID (--oid) 和回复文本 (--msg)')
        sys.exit(1)

    print(f"📤 正在发送回复 {target_info}...")
    print(f"   内容: “{msg.strip()}”")

    res = client.post_reply(
        oid=int(oid),
        type_id=int(type_id),
        message=msg.strip(),
        root=int(root or 0),
        parent=int(parent or 0),
    )

    if res.get('ok'):
        print(f"✅ {res.get('message', '发送成功')} (新评论 ID: {res.get('rpid')})")
    else:
        print(f"❌ {res.get('message', '发送失败')}")
        sys.exit(1)


def cmd_logout(args):
    """Log out and remove credentials."""
    delete_credentials()
    print('✅ 已清除本地 B 站登录凭证。')


def main():
    parser = argparse.ArgumentParser(
        description='Bilibili Message Center Assistant CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest='subcommand', required=True)

    # login
    p_login = subparsers.add_parser('login', help='二维码扫码登录 B 站')
    p_login.add_argument('--force', action='store_true', help='强制重新登录')
    p_login.set_defaults(func=cmd_login)

    # whoami
    p_whoami = subparsers.add_parser('whoami', help='检查当前登录用户信息')
    p_whoami.set_defaults(func=cmd_whoami)

    # check
    p_check = subparsers.add_parser('check', help='读取消息中心最新评论回复')
    p_check.add_argument('--limit', type=int, default=15, help='拉取消息数量上限 (默认 15)')
    p_check.add_argument('--json', action='store_true', help='以 JSON 格式输出')
    p_check.set_defaults(func=cmd_check)

    # reply
    p_reply = subparsers.add_parser('reply', help='发表评论回复')
    p_reply.add_argument('--id', type=int, help='通过 check 列表中的序号自动匹配目标评论')
    p_reply.add_argument('--oid', type=int, help='目标视频/动态 ID')
    p_reply.add_argument('--type', type=int, default=1, help='业务类型 (1:视频, 12:专栏, 11/17:动态)')
    p_reply.add_argument('--root', type=int, default=0, help='根评论 ID (顶层评论填 0)')
    p_reply.add_argument('--parent', type=int, default=0, help='被回复的父级评论 ID')
    p_reply.add_argument('--msg', type=str, required=True, help='回复内容文本')
    p_reply.set_defaults(func=cmd_reply)

    # logout
    p_logout = subparsers.add_parser('logout', help='注销并清除本地凭证')
    p_logout.set_defaults(func=cmd_logout)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
