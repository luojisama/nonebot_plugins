import asyncio
import os
import sys
import httpx
import nonebot
import json
import subprocess
import time
import re
from datetime import datetime
from typing import Optional
from pydantic import BaseModel
from nonebot import on_command, on_notice, on_request, get_driver, logger, get_plugin, get_loaded_plugins, require, get_plugin_config
from nonebot.message import run_preprocessor
from nonebot.matcher import Matcher
from nonebot.exception import FinishedException, IgnoredException
from nonebot.adapters.onebot.v11 import (
    Bot,
    Message,
    MessageSegment,
    MessageEvent,
    PrivateMessageEvent,
    GroupMessageEvent,
    GroupIncreaseNoticeEvent,
    GroupDecreaseNoticeEvent,
    FriendRequestEvent,
    GroupRequestEvent,
    ActionFailed
)
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.plugin import PluginMetadata

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

class Config(BaseModel):
    qzone_cookie: Optional[str] = None

try:
    from nonebot_plugin_htmlrender import md_to_pic
except ImportError:
    md_to_pic = None

# --- 全局变量 ---
plugin_config = get_plugin_config(Config)
_FRIEND_COUNT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "friend_count.json")
_today_friend_count = 0
_pending_group_requests = {} # {flag: event}

def _load_friend_count():
    global _today_friend_count
    if os.path.exists(_FRIEND_COUNT_FILE):
        try:
            with open(_FRIEND_COUNT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                _today_friend_count = data.get("count", 0)
        except Exception:
            _today_friend_count = 0

def _save_friend_count():
    try:
        os.makedirs(os.path.dirname(_FRIEND_COUNT_FILE), exist_ok=True)
        with open(_FRIEND_COUNT_FILE, "w", encoding="utf-8") as f:
            json.dump({"count": _today_friend_count, "date": datetime.now().strftime("%Y-%m-%d")}, f)
    except Exception as e:
        logger.error(f"保存好友统计失败: {e}")

# 初始化加载
_load_friend_count()


__plugin_meta__ = PluginMetadata(
    name="Bot管理",
    description="Bot综合管理：上下线提醒、重启关闭、插件管理、账号设置、群发消息、空间说说及群务监控",
    usage="""
【基础管理 (超管)】
重启: 重启Bot进程
关闭: 彻底关闭Bot
告诉管理员 [内容]: 向所有超管发送私聊消息
修改昵称 [新昵称]: 修改Bot昵称
修改头像 [图片/URL]: 修改Bot头像

【账号与消息 (超管)】
发布群消息 [群号] [内容]: 指定群发送消息
发布说说 [内容]: 发送QQ空间说说
更新空间Cookie: 获取/更新说说权限
申请列表: 查看未处理的加群/邀请申请
同意申请 [编号]: 同意加群/邀请
拒绝申请 [编号]: 拒绝加群/邀请

【插件管理 (超管)】
插件列表: 查看已加载插件及其详情
插件帮助 [名称/序号]: 查看插件详细说明
商店查询 [关键词]: 在NoneBot插件商店搜索
安装插件 [包名]: 使用pip安装新插件
更新插件 [包名]: 使用pip更新插件包

【群务管理 (管理/超管)】
禁言 [@目标/QQ] [时长]: 禁言成员 (支持 10m/2h 等格式)
解禁 [@目标/QQ]: 解除成员禁言
踢出 [@目标/QQ]: 将成员移出群聊
确认/取消: 对敏感管理操作进行二次确认
本群禁用 [插件名]: 在当前群禁用指定插件
本群启用 [插件名]: 在当前群启用指定插件
本群禁用列表: 查看当前群禁用的插件

【自动化功能】
上下线提醒: Bot连接/断开时私聊提醒超管
群变动监控: 绪山真寻风格的入群欢迎与退群告别
好友/入群申请: 自动同意好友，入群申请需管理员审核
    """.strip(),
)

driver = get_driver()
superusers = driver.config.superusers

# --- 上下线提示 ---
@driver.on_bot_connect
async def _(bot: Bot):
    for user_id in superusers:
        try:
            time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await bot.send_private_msg(user_id=int(user_id), message=f"🚀 Bot 已上线！\n当前时间：{time_str}")
        except Exception as e:
            logger.error(f"发送上线通知失败: {e}")

@driver.on_bot_disconnect
async def _(bot: Bot):
    # 注意：断开连接时可能无法直接通过该 bot 发送消息
    logger.info("Bot 已断开连接")

# --- 重启/关闭 ---
reboot = on_command("重启", permission=SUPERUSER, priority=1, block=True)
@reboot.handle()
async def _(bot: Bot, event: MessageEvent):
    await reboot.send("正在重启 Bot...")
    await asyncio.sleep(1) # 等待消息发送完毕
    try:
        # 获取项目根目录 (假设插件在 plugin/bot_manager 下)
        root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        bot_py = os.path.join(root_dir, "bot.py")
        
        if os.path.exists(bot_py):
            cmd = [sys.executable, bot_py]
        else:
            # 回退到 sys.argv，但 sys.argv[0] 可能是全路径也可能是相对路径
            cmd = [sys.executable] + sys.argv
            
        logger.info(f"正在尝试重启，执行命令: {' '.join(cmd)}")
        
        if sys.platform == "win32":
            # 在当前终端重启：不使用 CREATE_NEW_CONSOLE，直接拉起新进程并退出当前进程
            subprocess.Popen(cmd, cwd=root_dir)
            os._exit(0)
        else:
            # Linux/Unix 使用 execv 原地替换进程
            try:
                # 尝试关闭所有非标准文件描述符，防止泄露
                # 但在 NoneBot 这种复杂的异步框架中，通常不需要手动处理
                os.chdir(root_dir)
                os.execv(sys.executable, cmd)
            except Exception as e:
                logger.error(f"execv 失败: {e}")
                # 如果 execv 失败，尝试用 Popen 兜底
                subprocess.Popen(cmd, cwd=root_dir)
                os._exit(0)
    except Exception as e:
        logger.error(f"重启失败: {e}")
        await reboot.finish(f"重启失败: {e}")

shutdown = on_command("关闭", permission=SUPERUSER, priority=1, block=True)
@shutdown.handle()
async def _(bot: Bot, event: MessageEvent):
    await shutdown.send("正在关闭 Bot...")
    await asyncio.sleep(1) # 等待消息发送完毕
    os._exit(0)

# --- 查看插件 ---
list_plugins = on_command("插件列表", aliases={"查看插件"}, priority=1, block=True)
@list_plugins.handle()
async def _(bot: Bot, event: MessageEvent):
    await list_plugins.send("正在生成插件列表...")
    
    plugins = get_loaded_plugins()
    
    # 获取项目根目录和插件目录
    root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    local_plugin_dir = os.path.join(root_dir, "plugin")
    
    local_plugins = []
    store_plugins = []
    
    # 按照模块名排序，确保序号稳定
    plugins = sorted(list(plugins), key=lambda x: x.module_name)
    
    for i, p in enumerate(plugins, 1):
        # 获取插件模块的文件路径
        module_file = getattr(p.module, "__file__", "")
        
        # 判断是否为本地插件
        is_local = False
        if module_file:
            abs_module_path = os.path.abspath(module_file)
            if abs_module_path.startswith(os.path.abspath(local_plugin_dir)):
                is_local = True
        
        # 插件信息
        p_name = p.metadata.name if p.metadata else p.name
        p_desc = p.metadata.description if p.metadata else "无描述"
        p_module = p.module_name
        
        info = f"| {i} | {p_name} | {p_module} | {p_desc} |"
        
        if is_local:
            local_plugins.append(info)
        else:
            store_plugins.append(info)

    # 构造 Markdown 内容
    md = "# 🧩 NoneBot 插件列表\n\n"
    
    md += "## 🏠 本地插件 (Local)\n"
    md += "| ID | 插件名称 | 模块路径 | 描述 |\n"
    md += "| :--- | :--- | :--- | :--- |\n"
    md += "\n".join(local_plugins) + "\n\n"
    
    md += "## 🛒 商店插件 (Store)\n"
    md += "| ID | 插件名称 | 模块路径 | 描述 |\n"
    md += "| :--- | :--- | :--- | :--- |\n"
    md += "\n".join(store_plugins) + "\n\n"
    
    md += f"---\n> 统计: 共 {len(plugins)} 个插件 (本地: {len(local_plugins)} | 商店: {len(store_plugins)})\n"
    md += "> 提示: 发送 `帮助 [ID]` 可快速查看详情"

    if md_to_pic:
        try:
            pic = await md_to_pic(md, width=800)
            await list_plugins.finish(MessageSegment.image(pic))
        except FinishedException:
            raise
        except Exception as e:
            logger.error(f"渲染插件列表失败: {e}")
            await list_plugins.finish(f"渲染失败，回退文本显示：\n\n{md}")
    else:
        await list_plugins.finish(md)

tell_admin = on_command("告诉管理员", priority=5, block=True)
@tell_admin.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    content = args.extract_plain_text().strip()
    if not content:
        await tell_admin.finish("请发送 /告诉管理员 内容")

    if not superusers:
        await tell_admin.finish("未配置管理员(superusers)，无法发送")

    if isinstance(event, GroupMessageEvent):
        prefix = f"来自群 {event.group_id} 的用户 {event.user_id}"
    else:
        prefix = f"来自用户 {event.user_id}"

    msg = f"{prefix}\n内容: {content}"
    sent = 0
    for uid in superusers:
        try:
            await bot.send_private_msg(user_id=int(uid), message=msg)
            sent += 1
        except Exception as e:
            logger.error(f"发送管理员私聊失败: {e}")

    if sent <= 0:
        await tell_admin.finish("发送失败")
    await tell_admin.finish("已发送给管理员")

_pending_actions = {}
_pending_ttl_seconds = 30

def _parse_target_user_id(event: GroupMessageEvent, args: Message) -> Optional[int]:
    for seg in event.get_message():
        if seg.type == "at":
            qq = seg.data.get("qq")
            if qq and str(qq).isdigit():
                return int(qq)
    text = args.extract_plain_text().strip()
    if not text:
        return None
    first = text.split()[0]
    if first.isdigit():
        return int(first)
    return None

def _parse_duration_seconds(text: str) -> int | None:
    if not text:
        return None
    s = text.strip().lower()
    if s.endswith("分钟"):
        s = s[:-2] + "m"
    elif s.endswith("分"):
        s = s[:-1] + "m"
    elif s.endswith("小时"):
        s = s[:-2] + "h"
    elif s.endswith("时"):
        s = s[:-1] + "h"
    elif s.endswith("天"):
        s = s[:-1] + "d"
    elif s.endswith("秒"):
        s = s[:-1] + "s"
    if s.isdigit():
        return int(s)
    unit = s[-1]
    num = s[:-1]
    if not num.isdigit():
        return None
    n = int(num)
    if unit == "s":
        return n
    if unit == "m":
        return n * 60
    if unit == "h":
        return n * 3600
    if unit == "d":
        return n * 86400
    return None

def _extract_duration_seconds(args: Message, target_id: int | None, default_seconds: int = 600) -> int:
    tokens = args.extract_plain_text().strip().split()
    for token in tokens:
        if target_id is not None and token.isdigit() and int(token) == int(target_id):
            continue
        parsed = _parse_duration_seconds(token)
        if parsed is not None:
            return parsed
    return default_seconds

async def _bot_can_manage_group(bot: Bot, group_id: int) -> bool:
    try:
        info = await bot.get_group_member_info(group_id=group_id, user_id=int(bot.self_id))
        role = info.get("role")
        return role in {"admin", "owner"}
    except Exception:
        return False

async def _ensure_group_manage_enabled(bot: Bot, event: MessageEvent) -> tuple[bool, str]:
    if not isinstance(event, GroupMessageEvent):
        return False, "仅支持群聊使用"
    ok = await _bot_can_manage_group(bot, event.group_id)
    if not ok:
        return False, "Bot 不是群管理员/群主，无法执行该操作"
    return True, ""

group_ban = on_command("禁言", permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER, priority=5, block=True)
@group_ban.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    enabled, err = await _ensure_group_manage_enabled(bot, event)
    if not enabled:
        await group_ban.finish(err)
    assert isinstance(event, GroupMessageEvent)

    target_id = _parse_target_user_id(event, args)
    if not target_id:
        await group_ban.finish("请 @目标 或输入 QQ 号")
    if target_id == int(bot.self_id):
        await group_ban.finish("不能对 Bot 自己操作")

    duration = _extract_duration_seconds(args, target_id, default_seconds=600)
    if duration < 1:
        duration = 1
    if duration > 30 * 86400:
        duration = 30 * 86400

    _pending_actions[(event.group_id, event.user_id)] = {
        "action": "ban",
        "target_id": target_id,
        "duration": duration,
        "created_at": time.time(),
        "operator_id": event.user_id,
        "group_id": event.group_id,
    }
    await group_ban.finish(f"已进入二次确认：禁言 {target_id} {duration} 秒\n30 秒内发送 /确认 执行，或 /取消 取消")

group_unban = on_command("解禁", aliases={"解除禁言"}, permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER, priority=5, block=True)
@group_unban.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    enabled, err = await _ensure_group_manage_enabled(bot, event)
    if not enabled:
        await group_unban.finish(err)
    assert isinstance(event, GroupMessageEvent)

    target_id = _parse_target_user_id(event, args)
    if not target_id:
        await group_unban.finish("请 @目标 或输入 QQ 号")
    if target_id == int(bot.self_id):
        await group_unban.finish("不能对 Bot 自己操作")

    _pending_actions[(event.group_id, event.user_id)] = {
        "action": "unban",
        "target_id": target_id,
        "duration": 0,
        "created_at": time.time(),
        "operator_id": event.user_id,
        "group_id": event.group_id,
    }
    await group_unban.finish(f"已进入二次确认：解禁 {target_id}\n30 秒内发送 /确认 执行，或 /取消 取消")

group_kick = on_command("踢出", aliases={"踢人"}, permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER, priority=5, block=True)
@group_kick.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    enabled, err = await _ensure_group_manage_enabled(bot, event)
    if not enabled:
        await group_kick.finish(err)
    assert isinstance(event, GroupMessageEvent)

    target_id = _parse_target_user_id(event, args)
    if not target_id:
        await group_kick.finish("请 @目标 或输入 QQ 号")
    if target_id == int(bot.self_id):
        await group_kick.finish("不能对 Bot 自己操作")

    _pending_actions[(event.group_id, event.user_id)] = {
        "action": "kick",
        "target_id": target_id,
        "created_at": time.time(),
        "operator_id": event.user_id,
        "group_id": event.group_id,
    }
    await group_kick.finish(f"已进入二次确认：踢出 {target_id}\n30 秒内发送 /确认 执行，或 /取消 取消")

confirm_action = on_command("确认", permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER, priority=5, block=True)
@confirm_action.handle()
async def _(bot: Bot, event: MessageEvent):
    enabled, err = await _ensure_group_manage_enabled(bot, event)
    if not enabled:
        await confirm_action.finish(err)
    assert isinstance(event, GroupMessageEvent)

    key = (event.group_id, event.user_id)
    pending = _pending_actions.get(key)
    if not pending:
        await confirm_action.finish("没有待确认的操作")
    if time.time() - float(pending.get("created_at", 0)) > _pending_ttl_seconds:
        _pending_actions.pop(key, None)
        await confirm_action.finish("待确认操作已过期")

    action = pending.get("action")
    target_id = int(pending.get("target_id", 0))
    if target_id <= 0:
        _pending_actions.pop(key, None)
        await confirm_action.finish("待确认操作无效")

    try:
        if action == "ban":
            duration = int(pending.get("duration", 600))
            await bot.set_group_ban(group_id=event.group_id, user_id=target_id, duration=duration)
            _pending_actions.pop(key, None)
            await confirm_action.finish(f"已禁言 {target_id} {duration} 秒")
        elif action == "unban":
            await bot.set_group_ban(group_id=event.group_id, user_id=target_id, duration=0)
            _pending_actions.pop(key, None)
            await confirm_action.finish(f"已解禁 {target_id}")
        elif action == "kick":
            await bot.set_group_kick(group_id=event.group_id, user_id=target_id, reject_add_request=False)
            _pending_actions.pop(key, None)
            await confirm_action.finish(f"已踢出 {target_id}")
        else:
            _pending_actions.pop(key, None)
            await confirm_action.finish("待确认操作无效")
    except FinishedException:
        raise
    except Exception as e:
        _pending_actions.pop(key, None)
        await confirm_action.finish(f"执行失败: {e}")

cancel_action = on_command("取消", aliases={"撤销"}, permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER, priority=5, block=True)
@cancel_action.handle()
async def _(event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        await cancel_action.finish("仅支持群聊使用")
    key = (event.group_id, event.user_id)
    if key in _pending_actions:
        _pending_actions.pop(key, None)
        await cancel_action.finish("已取消")
    await cancel_action.finish("没有待确认的操作")

# --- 插件帮助 ---
plugin_help = on_command("插件帮助", aliases={"帮助", "help"}, priority=1, block=True)
@plugin_help.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    plugin_name = args.extract_plain_text().strip()
    plugins = get_loaded_plugins()
    
    if not plugin_name:
        # 如果没带参数，显示 Bot 管理器的帮助
        help_text = "💡 **使用说明**\n\n"
        help_text += "发送 `插件列表` 查看已安装插件\n"
        help_text += "发送 `插件帮助 [插件名]` 查看具体功能\n\n"
        help_text += "**管理命令 (仅超管):**\n"
        help_text += "- `重启`: 重启 Bot\n"
        help_text += "- `关闭`: 彻底关闭 Bot\n"
        help_text += "- `商店查询 [关键词]`: 搜插件\n"
        help_text += "- `安装插件 [包名]`: 安装新插件\n"
        help_text += "- `更新插件 [包名]`: 更新已有插件\n"
        
        if md_to_pic:
            try:
                pic = await md_to_pic(help_text, width=500)
                await plugin_help.finish(MessageSegment.image(pic))
            except FinishedException: raise
            except: pass
        await plugin_help.finish(help_text)

    # 查找插件
    target = None
    plugins = sorted(list(plugins), key=lambda x: x.module_name)

    # 尝试通过序号查找
    if plugin_name.isdigit():
        idx = int(plugin_name) - 1
        if 0 <= idx < len(plugins):
            target = plugins[idx]
    
    # 如果没通过序号找到，尝试通过名称模糊匹配
    if not target:
        for p in plugins:
            p_meta_name = p.metadata.name if p.metadata else ""
            if plugin_name.lower() in [p.name.lower(), p_meta_name.lower(), p.module_name.lower()]:
                target = p
                break
    
    if not target:
        await plugin_help.finish(f"❌ 未找到插件 '{plugin_name}'，请检查名称是否正确。")

    # 构造插件详情 Markdown
    meta = target.metadata
    md = f"# 📖 插件帮助: {meta.name if meta else target.name}\n\n"
    
    if meta:
        md += f"**描述**: {meta.description}\n\n"
        if meta.usage:
            md += f"## 🛠️ 使用方法\n```text\n{meta.usage}\n```\n"
        else:
            md += "> 该插件未提供详细使用说明。\n"
    else:
        md += "> 该插件未配置元数据 (Metadata)。\n"
        
    md += f"\n---\n**模块路径**: `{target.module_name}`"

    if md_to_pic:
        try:
            pic = await md_to_pic(md, width=600)
            await plugin_help.finish(MessageSegment.image(pic))
        except FinishedException:
            raise
        except Exception as e:
            logger.error(f"渲染帮助失败: {e}")
            await plugin_help.finish(md)
    else:
        await plugin_help.finish(md)
    
# --- 插件商店功能 ---
store_search = on_command("商店查询", permission=SUPERUSER, priority=1, block=True)
@store_search.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    keyword = args.extract_plain_text().strip()
    await store_search.send("正在查询插件商店...")
    
    plugins = []
    urls = [
        "https://registry.nonebot.dev/plugins.json",
        "https://v2.nonebot.dev/plugins.json" # 备用地址
    ]
    
    error_msg = ""
    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        for url in urls:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                # 显式使用 utf-8 解码并处理可能的 BOM
                content = resp.text.lstrip('\ufeff')
                plugins = json.loads(content)
                if plugins:
                    break
            except Exception as e:
                error_msg = str(e)
                logger.warning(f"从 {url} 获取插件列表失败: {e}")
                continue

    if not plugins:
        await store_search.finish(f"获取插件列表失败，请检查网络。错误: {error_msg}")

    if keyword:
        filtered = [p for p in plugins if keyword.lower() in p["name"].lower() or keyword.lower() in p["desc"].lower()]
    else:
        filtered = plugins[:20] # 默认显示前20个

    if not filtered:
        await store_search.finish(f"未找到包含 '{keyword}' 的插件")

    # 构造聊天记录形式
    messages = []
    for p in filtered:
        content = (
            f"名称: {p['name']}\n"
            f"包名: {p['project_link']}\n"
            f"模块: {p['module_name']}\n"
            f"描述: {p['desc']}\n"
            f"作者: {p['author']}"
        )
        messages.append({
            "type": "node",
            "data": {
                "name": "NoneBot 商店",
                "uin": bot.self_id,
                "content": content
            }
        })

    if isinstance(event, GroupMessageEvent):
        await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=messages)
    else:
        await bot.call_api("send_private_forward_msg", user_id=event.user_id, messages=messages)

install_plugin = on_command("安装插件", permission=SUPERUSER, priority=1, block=True)
@install_plugin.handle()
async def _(args: Message = CommandArg()):
    plugin_name = args.extract_plain_text().strip()
    if not plugin_name:
        await install_plugin.finish("请输入要安装的插件包名")
    
    await install_plugin.send(f"开始安装插件 {plugin_name}...")
    try:
        # 使用 pip 安装
        process = subprocess.Popen(
            [sys.executable, "-m", "pip", "install", plugin_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = process.communicate()
        if process.returncode == 0:
            await install_plugin.finish(f"插件 {plugin_name} 安装成功！请重启 Bot。")
        else:
            await install_plugin.finish(f"安装失败：\n{stderr}")
    except Exception as e:
        await install_plugin.finish(f"执行安装出错: {e}")

update_plugin = on_command("更新插件", permission=SUPERUSER, priority=1, block=True)
@update_plugin.handle()
async def _(args: Message = CommandArg()):
    plugin_name = args.extract_plain_text().strip()
    if not plugin_name:
        await update_plugin.finish("请输入要更新的插件包名")
    
    await update_plugin.send(f"开始更新插件 {plugin_name}...")
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "pip", "install", "--upgrade", plugin_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = process.communicate()
        if process.returncode == 0:
            await update_plugin.finish(f"插件 {plugin_name} 更新成功！请重启 Bot。")
        else:
            await update_plugin.finish(f"更新失败：\n{stderr}")
    except Exception as e:
        await update_plugin.finish(f"执行更新出错: {e}")

# --- 群聊人员变动监控 ---
group_increase = on_notice(priority=5)
@group_increase.handle()
async def _(bot: Bot, event: GroupIncreaseNoticeEvent):
    user_id = event.user_id
    group_id = event.group_id
    
    # 别当欧尼酱主题：真寻风格的欢迎
    welcome_msgs = [
        f"诶？又有新成员加入了吗？(・◇・)\n欢迎 [CQ:at,qq={user_id}] 来到本群！我是绪山真寻，请多指教！",
        f"喔！是新面孔呢！[CQ:at,qq={user_id}]，欢迎加入！！",
        f"（惊）有人进来了！[CQ:at,qq={user_id}] 欢迎！在这里要好好相处哦，不然美波里会生气的！"
    ]
    import random
    msg = random.choice(welcome_msgs)
    await bot.send_group_msg(group_id=group_id, message=msg)

group_decrease = on_notice(priority=5)
@group_decrease.handle()
async def _(bot: Bot, event: GroupDecreaseNoticeEvent):
    user_id = event.user_id
    group_id = event.group_id
    
    # 别当欧尼酱主题：真寻风格的告别
    farewell_msgs = [
        f"呜... [CQ:at,qq={user_id}] 离开了群聊呢。是因为我太不可靠了吗？(Ｔ▽Ｔ)",
        f"啊，那个人走了呢... [CQ:at,qq={user_id}]，祝你以后也能开开心心的，记得回来看看哦！",
        f"总觉得心里空落落的... [CQ:at,qq={user_id}] 已经不在这个群里了。这就是离别的感觉吗？"
    ]
    import random
    msg = random.choice(farewell_msgs)
    await bot.send_group_msg(group_id=group_id, message=msg)

# --- 好友申请自动同意 ---
friend_req = on_request(priority=1, block=True)

@friend_req.handle()
async def _(bot: Bot, event: FriendRequestEvent):
    global _today_friend_count
    try:
        await event.approve(bot)
        _today_friend_count += 1
        _save_friend_count()
        logger.info(f"Bot管理：已自动同意用户 {event.user_id} 的好友申请，今日第 {_today_friend_count} 个")
    except Exception as e:
        logger.error(f"Bot管理：自动同意好友申请失败: {e}")

# --- 入群申请处理 ---
group_req = on_request(priority=1, block=True)

@group_req.handle()
async def _(bot: Bot, event: GroupRequestEvent):
    # 仅处理加群申请 (add) 或 邀请 (invite)
    if event.sub_type not in ["add", "invite"]:
        return

    # 生成一个简短的 ID
    req_id = str(len(_pending_group_requests) + 1)
    _pending_group_requests[req_id] = event
    
    # 获取用户信息
    user_id = event.user_id
    group_id = event.group_id
    comment = event.comment or "无"
    
    msg = (
        f"【入群申请】\n"
        f"编号: {req_id}\n"
        f"申请人: {user_id}\n"
        f"说明: {comment}\n"
        f"请发送 `/同意申请 {req_id}` 批准，或忽略。"
    )
    
    try:
        await bot.send_group_msg(group_id=group_id, message=msg)
        logger.info(f"已推送入群申请 {req_id} 到群 {group_id}")
    except Exception as e:
        logger.error(f"推送入群申请失败: {e}")

# --- 同意申请指令 ---
approve_req = on_command("同意申请", permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER, priority=1, block=True)

@approve_req.handle()
async def _(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    req_id = args.extract_plain_text().strip()
    if not req_id:
        await approve_req.finish("请输入申请编号，例如：/同意申请 1")
    
    if req_id not in _pending_group_requests:
        await approve_req.finish("找不到该编号的申请，可能已过期或不存在。")
        
    req_event = _pending_group_requests[req_id]
    
    # 校验是否是本群的申请
    if req_event.group_id != event.group_id:
        await approve_req.finish("该申请不属于本群。")
        
    try:
        await req_event.approve(bot)
        del _pending_group_requests[req_id]
        await approve_req.finish(f"已同意申请 {req_id} (用户 {req_event.user_id})")
    except FinishedException:
        raise
    except Exception as e:
        await approve_req.finish(f"操作失败: {e}")

# --- 定时任务 ---
@scheduler.scheduled_job("cron", hour=23, minute=59, id="bot_manager_daily_report")
async def daily_report():
    # 1. 好友统计
    global _today_friend_count
    # 即使数量为0也推送，确保管理员知道Bot还在运行
    bots = get_driver().bots.values()
    msg = f"【每日统计】\n今日新增好友: {_today_friend_count} 人"
    for bot in bots:
        for su in superusers:
            try:
                await bot.send_private_msg(user_id=int(su), message=msg)
            except: pass
    
    _today_friend_count = 0 # 重置
    _save_friend_count() # 保存重置后的状态

    # 2. 清空入群申请
    global _pending_group_requests
    if _pending_group_requests:
        count = len(_pending_group_requests)
        _pending_group_requests.clear()
        logger.info(f"Bot管理：已自动清空 {count} 条未处理的入群申请")

# --- 群内插件管理 ---

_DISABLED_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "disabled_plugins.json")
_disabled_plugins_cache = {} # {str(group_id): [plugin_names]}

def _load_disabled_config():
    global _disabled_plugins_cache
    if not os.path.exists(os.path.dirname(_DISABLED_CONFIG_PATH)):
        os.makedirs(os.path.dirname(_DISABLED_CONFIG_PATH), exist_ok=True)
    
    if os.path.exists(_DISABLED_CONFIG_PATH):
        try:
            with open(_DISABLED_CONFIG_PATH, "r", encoding="utf-8") as f:
                _disabled_plugins_cache = json.load(f)
        except Exception as e:
            logger.error(f"加载插件禁用配置失败: {e}")
            _disabled_plugins_cache = {}
    else:
        _disabled_plugins_cache = {}

def _save_disabled_config():
    try:
        with open(_DISABLED_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(_disabled_plugins_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存插件禁用配置失败: {e}")

# 初始化加载
_load_disabled_config()

@run_preprocessor
async def _(matcher: Matcher, event: GroupMessageEvent):
    """拦截被禁用的插件"""
    plugin = matcher.plugin
    if not plugin:
        return
    
    group_id = str(event.group_id)
    if group_id in _disabled_plugins_cache:
        # 检查是否禁用了该插件
        if plugin.name in _disabled_plugins_cache[group_id] or plugin.module_name in _disabled_plugins_cache[group_id]:
            # Bot管理插件本身不允许被禁用，否则无法解禁
            if plugin.name == "Bot管理" or "bot_manager" in plugin.module_name:
                return
            
            raise IgnoredException(f"Plugin {plugin.name} is disabled in group {group_id}")

def _find_plugin(name_or_index: str):
    """根据名称或序号查找插件"""
    plugins = get_loaded_plugins()
    plugins = sorted(list(plugins), key=lambda x: x.module_name)
    
    if name_or_index.isdigit():
        idx = int(name_or_index) - 1
        if 0 <= idx < len(plugins):
            return plugins[idx]
            
    for p in plugins:
        p_meta_name = p.metadata.name if p.metadata else ""
        if name_or_index.lower() in [p.name.lower(), p_meta_name.lower(), p.module_name.lower()]:
            return p
    return None

disable_plugin_cmd = on_command("本群禁用", aliases={"禁用插件"}, permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER, priority=1, block=True)
@disable_plugin_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    params = args.extract_plain_text().strip().split()
    if not params:
        await disable_plugin_cmd.finish("用法: 禁用插件 [群号] <插件名/序号>")
    
    target_group_id = None
    plugin_str = None
    
    # 判断是否指定了群号 (仅限超管)
    if len(params) >= 2 and params[0].isdigit() and len(params[0]) >= 5:
        if str(event.user_id) not in superusers:
            # 如果不是超管，但尝试指定群号，则视为参数错误或无权操作
            # 这里简单处理：如果不是超管，就把第一个参数当做插件名（虽然很少见纯数字插件名且长度>5）
            # 或者直接报错
             await disable_plugin_cmd.finish("权限不足，无法指定其他群号")
        else:
            target_group_id = params[0]
            plugin_str = params[1]
    else:
        # 未指定群号，默认当前群
        if isinstance(event, GroupMessageEvent):
            target_group_id = str(event.group_id)
            plugin_str = params[0]
        else:
            await disable_plugin_cmd.finish("请指定群号: 禁用插件 <群号> <插件名/序号>")

    # 查找插件
    target = _find_plugin(plugin_str)
    if not target:
        await disable_plugin_cmd.finish(f"找不到插件: {plugin_str}")
        
    real_name = target.metadata.name if target.metadata else target.name
    
    # 禁止禁用自己
    if real_name == "Bot管理" or "bot_manager" in target.module_name:
        await disable_plugin_cmd.finish("无法禁用 Bot管理 插件")

    if target_group_id not in _disabled_plugins_cache:
        _disabled_plugins_cache[target_group_id] = []
        
    if real_name not in _disabled_plugins_cache[target_group_id]:
        _disabled_plugins_cache[target_group_id].append(real_name)
        _save_disabled_config()
        await disable_plugin_cmd.finish(f"已在群 {target_group_id} 禁用插件: {real_name}")
    else:
        await disable_plugin_cmd.finish(f"插件 {real_name} 已经在群 {target_group_id} 被禁用了")

enable_plugin_cmd = on_command("本群启用", aliases={"启用插件"}, permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER, priority=1, block=True)
@enable_plugin_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    params = args.extract_plain_text().strip().split()
    if not params:
        await enable_plugin_cmd.finish("用法: 启用插件 [群号] <插件名/序号>")

    target_group_id = None
    plugin_str = None

    if len(params) >= 2 and params[0].isdigit() and len(params[0]) >= 5:
        if str(event.user_id) not in superusers:
             await enable_plugin_cmd.finish("权限不足，无法指定其他群号")
        else:
            target_group_id = params[0]
            plugin_str = params[1]
    else:
        if isinstance(event, GroupMessageEvent):
            target_group_id = str(event.group_id)
            plugin_str = params[0]
        else:
            await enable_plugin_cmd.finish("请指定群号: 启用插件 <群号> <插件名/序号>")

    if target_group_id not in _disabled_plugins_cache:
        await enable_plugin_cmd.finish(f"群 {target_group_id} 没有禁用任何插件")
        
    # 查找插件 (为了获取标准名称)
    target = _find_plugin(plugin_str)
    found_name = None
    
    if target:
        real_name = target.metadata.name if target.metadata else target.name
        if real_name in _disabled_plugins_cache[target_group_id]:
            found_name = real_name
    
    # 如果没找到标准插件对象（可能插件已卸载但配置还在），或者标准名称不在禁用列表中
    # 尝试在禁用列表中模糊搜索
    if not found_name:
        disabled_list = _disabled_plugins_cache[target_group_id]
        if plugin_str in disabled_list:
             found_name = plugin_str
        else:
            for name in disabled_list:
                if plugin_str.lower() in name.lower():
                    found_name = name
                    break
    
    if found_name:
        _disabled_plugins_cache[target_group_id].remove(found_name)
        if not _disabled_plugins_cache[target_group_id]:
            del _disabled_plugins_cache[target_group_id]
        _save_disabled_config()
        await enable_plugin_cmd.finish(f"已在群 {target_group_id} 启用插件: {found_name}")
    else:
        await enable_plugin_cmd.finish(f"在群 {target_group_id} 未找到已禁用的插件: {plugin_str}")

list_disabled_cmd = on_command("本群禁用列表", aliases={"群禁用列表"}, permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER, priority=1, block=True)
@list_disabled_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    target_group_id = None
    arg_str = args.extract_plain_text().strip()
    
    if arg_str and arg_str.isdigit():
        if str(event.user_id) not in superusers:
             await list_disabled_cmd.finish("权限不足，无法查看其他群号")
        target_group_id = arg_str
    else:
        if isinstance(event, GroupMessageEvent):
            target_group_id = str(event.group_id)
        else:
            await list_disabled_cmd.finish("请指定群号")

    if target_group_id not in _disabled_plugins_cache or not _disabled_plugins_cache[target_group_id]:
        await list_disabled_cmd.finish(f"群 {target_group_id} 当前没有禁用任何插件")
    
    msg = f"🚫 群 {target_group_id} 已禁用插件:\n" + "\n".join([f"- {name}" for name in _disabled_plugins_cache[target_group_id]])
    await list_disabled_cmd.finish(msg)

# --- 扩展功能: 账号设置 ---
set_nickname = on_command("修改昵称", permission=SUPERUSER, priority=5, block=True)
@set_nickname.handle()
async def _(bot: Bot, arg: Message = CommandArg()):
    nickname = arg.extract_plain_text().strip()
    if not nickname:
        await set_nickname.finish("请输入要修改的昵称")
    try:
        await bot.set_nickname(nickname=nickname)
        await set_nickname.finish(f"昵称已成功修改为：{nickname}")
    except ActionFailed as e:
        await set_nickname.finish(f"修改失败：{str(e)}")
    except FinishedException:
        raise

set_face = on_command("修改头像", permission=SUPERUSER, priority=5, block=True)
@set_face.handle()
async def _(bot: Bot, arg: Message = CommandArg()):
    img_url = ""
    for seg in arg:
        if seg.type == "image":
            img_url = seg.data["url"]
            break
    if not img_url:
        text = arg.extract_plain_text().strip()
        if text.startswith("http"):
            img_url = text
    if not img_url:
        await set_face.finish("请发送图片或提供图片 URL")
    try:
        await bot.set_face(file=img_url)
        await set_face.finish("头像修改指令已提交（可能需要一定时间生效）")
    except ActionFailed as e:
        await set_face.finish(f"修改失败：{str(e)}")
    except FinishedException:
        raise

# --- 扩展功能: 申请列表与拒绝 ---
list_req = on_command("申请列表", permission=SUPERUSER, priority=5, block=True)
@list_req.handle()
async def _(bot: Bot):
    if not _pending_group_requests:
        await list_req.finish("当前没有待处理申请")
    msg = "待处理申请列表：\n"
    for req_id, event in _pending_group_requests.items():
        type_str = "加群" if event.sub_type == "add" else "邀请"
        msg += f"编号: {req_id} | [{type_str}] 群:{event.group_id} QQ:{event.user_id} | 说明: {event.comment}\n"
    await list_req.finish(msg.strip())

reject_req = on_command("拒绝申请", permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER, priority=1, block=True)
@reject_req.handle()
async def _(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
    req_id = args.extract_plain_text().strip()
    if not req_id:
        await reject_req.finish("请输入申请编号，例如：/拒绝申请 1")
    if req_id not in _pending_group_requests:
        await reject_req.finish("找不到该编号的申请，可能已过期或不存在。")
    req_event = _pending_group_requests[req_id]
    if req_event.group_id != event.group_id:
        await reject_req.finish("该申请不属于本群。")
    try:
        await req_event.approve(bot, approve=False)
        del _pending_group_requests[req_id]
        await reject_req.finish(f"已拒绝申请 {req_id} (用户 {req_event.user_id})")
    except FinishedException:
        raise
    except Exception as e:
        await reject_req.finish(f"操作失败: {e}")

# --- 扩展功能: 群发消息 ---
send_group_msg_cmd = on_command("发布群消息", aliases={"发送群消息"}, permission=SUPERUSER, priority=5, block=True)
@send_group_msg_cmd.handle()
async def _(bot: Bot, arg: Message = CommandArg()):
    text = arg.extract_plain_text().strip()
    if not text:
        await send_group_msg_cmd.finish("格式：/发布群消息 [群号] [内容]")
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await send_group_msg_cmd.finish("请输入要发送的内容")
    group_id_str, content = parts
    if not group_id_str.isdigit():
        await send_group_msg_cmd.finish("群号必须为数字")
    try:
        await bot.send_group_msg(group_id=int(group_id_str), message=content)
        await send_group_msg_cmd.finish(f"消息已发送至群 {group_id_str}")
    except ActionFailed as e:
        await send_group_msg_cmd.finish(f"发送失败：{str(e)}")
    except FinishedException:
        raise

# --- 扩展功能: 空间说说 ---
def save_cookie_to_env(cookie: str):
    env_path = ".env.prod"
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        new_lines = []
        found = False
        for line in lines:
            if line.strip().startswith("qzone_cookie="):
                new_lines.append(f'qzone_cookie="{cookie}"\n')
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f'\nqzone_cookie="{cookie}"\n')
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
    except Exception as e:
        logger.error(f"保存 Cookie 到 .env 失败: {e}")

def get_g_tk(p_skey: str) -> int:
    hash_val = 5381
    for char in p_skey:
        hash_val += (hash_val << 5) + ord(char)
    return hash_val & 0x7fffffff

async def update_qzone_cookie(bot) -> tuple[bool, str]:
    """自动获取并刷新 Qzone Cookie，供定时任务调用"""
    try:
        cookies_resp = await bot.get_cookies(domain="qzone.qq.com")
        cookie = cookies_resp.get("cookies")
        if not cookie:
            return False, "自动获取 Cookie 失败，返回结果为空"
        if "p_skey" not in cookie:
            return False, f"获取到的 Cookie 不完整（缺少 p_skey）"
        if "uin=" not in cookie:
            cookie = f"uin=o{bot.self_id}; {cookie}"
        plugin_config.qzone_cookie = cookie
        save_cookie_to_env(cookie)
        return True, cookie
    except Exception as e:
        return False, str(e)

async def publish_qzone_shuo(content: str, bot_id: str) -> tuple[bool, str]:
    cookie = plugin_config.qzone_cookie
    if not cookie:
        return False, "未配置 Qzone Cookie"
    try:
        content = re.sub(r'\[图片\]|\[表情\]|\[动画表情\]', '', content).strip()
        if not content:
            return False, "说说内容不能为空（已过滤图片和表情）"
        pskey_match = re.search(r"p_skey=([^; ]+)", cookie)
        if not pskey_match:
            return False, "Cookie 缺少 p_skey 字段"
        p_skey = pskey_match.group(1)
        uin_match = re.search(r"uin=[o0]*(\d+)", cookie)
        qq = uin_match.group(1) if uin_match else bot_id
        formatted_cookie = f"uin=o{qq}; p_skey={p_skey};"
        if "skey=" in cookie:
            skey_match = re.search(r"skey=([^; ]+)", cookie)
            if skey_match:
                formatted_cookie += f" skey={skey_match.group(1)};"
        g_tk = get_g_tk(p_skey)
        url = f"https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6?g_tk={g_tk}"
        data = {
            "syn_tweet_version": 1, "paramstr": 1, "pic_template": "", "richtype": "", "richval": "",
            "special_url": "", "subrichtype": "", "con": content, "feed_tpl_id": "w_v6",
            "ugc_right": 1, "who": 1, "modifyflag": 0, "hostuin": qq, "format": "json",
            "qzreferrer": f"https://user.qzone.qq.com/{qq}"
        }
        headers = {
            "Cookie": formatted_cookie,
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Referer": f"https://user.qzone.qq.com/{qq}",
            "Origin": "https://user.qzone.qq.com"
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, data=data, headers=headers)
            if resp.status_code != 200:
                return False, f"请求失败，状态码：{resp.status_code}"
            resp_text = resp.text
            # 优先检查成功码（部分响应可能被包裹在 HTML/JSONP 中）
            if '"code":0' in resp_text or '"code": 0' in resp_text:
                return True, "发布成功"
            # 再检查是否返回了登录页面
            resp_lower = resp_text.strip().lower()
            if resp_lower.startswith("<html") or resp_lower.startswith("<!doctype"):
                return False, "Qzone 返回了登录页面或验证码，请尝试重新执行 /更新空间Cookie"
            msg_match = re.search(r'"message":"([^"]+)"', resp_text)
            err_msg = msg_match.group(1) if msg_match else resp_text[:100]
            return False, f"发布失败，返回：{err_msg}"
    except Exception as e:
        return False, f"发生异常：{str(e)}"

publish_shuo_cmd = on_command("发布说说", aliases={"发说说"}, permission=SUPERUSER, priority=5, block=True)
@publish_shuo_cmd.handle()
async def _(bot: Bot, arg: Message = CommandArg()):
    content = arg.extract_plain_text().strip()
    if not content:
        await publish_shuo_cmd.finish("请输入说说内容")
    if not plugin_config.qzone_cookie:
        await publish_shuo_cmd.finish("未配置 Qzone Cookie，请先执行 /更新空间Cookie")
    success, msg = await publish_qzone_shuo(content, bot.self_id)
    if success:
        await publish_shuo_cmd.finish("说说发布成功！")
    else:
        await publish_shuo_cmd.finish(f"说说发布失败：{msg}")

update_cookie_cmd = on_command("更新空间Cookie", aliases={"获取空间Cookie"}, permission=SUPERUSER, priority=5, block=True)
@update_cookie_cmd.handle()
async def _(bot: Bot):
    try:
        cookies_resp = await bot.get_cookies(domain="qzone.qq.com")
        cookie = cookies_resp.get("cookies")
        if not cookie:
            await update_cookie_cmd.finish("自动获取 Cookie 失败，返回结果为空。")
        if "p_skey" not in cookie:
            await update_cookie_cmd.finish(f"获取到的 Cookie 不完整（缺少 p_skey），请确保机器人已正常登录且环境支持。\n当前获取结果：{cookie[:50]}...")
        if "uin=" not in cookie:
            cookie = f"uin=o{bot.self_id}; {cookie}"
        plugin_config.qzone_cookie = cookie
        save_cookie_to_env(cookie)
        await update_cookie_cmd.finish(f"✅ 空间 Cookie 已自动更新并持久化保存！\n当前账号：{bot.self_id}\n你可以尝试发送 /发布说说 了。")
    except ActionFailed as e:
        await update_cookie_cmd.finish(f"调用 API 失败：{str(e)}")
    except FinishedException:
        raise
    except Exception as e:
        await update_cookie_cmd.finish(f"发生异常：{str(e)}")
