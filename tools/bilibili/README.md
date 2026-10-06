# Bilibili 消息助手工具 (Bilibili Message Assistant)

轻量级 B 站消息中心未读回复读取与智能回复工具。

## 功能特性
1. **官方扫码安全登录**：无需抓包提取 Cookie，直接在终端打印 ASCII 二维码并自动弹窗预览，使用手机 B 站 App 扫码即可完成登录。
2. **凭证安全隔离**：提取的 `SESSDATA` 与 `bili_jct` (CSRF Token) 仅加密/私密保存在本地 `tools/bilibili/.credentials.json`，已设置文件权限（0600）并添加至 `.gitignore`，杜绝凭证泄漏。
3. **消息中心一键拉取**：调用 B 站通知接口 `GET /x/msgfeed/reply`，结构化输出网友评论、被评论视频/动态标题、回复时间与定位路由参数。
4. **智能编号回复**：自动本地缓存拉取的评论列表，支持直接通过 `--id <序号>` 指定评论进行回复，无需繁琐复制复杂的 `oid`、`root`、`parent` 参数。

---

## 常用命令

### 1. 扫码登录
```bash
python -m tools.bilibili.cli login
```
*提示：如需切换账号，可添加 `--force` 参数强制重新扫码。*

### 2. 查看当前登录状态
```bash
python -m tools.bilibili.cli whoami
```

### 3. 查看消息中心最新回复
```bash
# 格式化终端列表展示
python -m tools.bilibili.cli check

# 获取前 30 条评论
python -m tools.bilibili.cli check --limit 30

# 输出 JSON 格式
python -m tools.bilibili.cli check --json
```

### 4. 发送评论回复
```bash
# 方式 A：通过 check 列表中的编号一键回复（推荐）
python -m tools.bilibili.cli reply --id 1 --msg "感谢您的支持！"

# 方式 B：手动指定参数回复
python -m tools.bilibili.cli reply --oid 12345678 --type 1 --root 0 --parent 0 --msg "您的回复内容"
```

### 5. 注销退出
```bash
python -m tools.bilibili.cli logout
```
