import asyncio
import os
import sys
import httpx
import nonebot
import json
import subprocess
import time
from datetime import datetime
from typing import Optional
from nonebot import on_command, get_driver, logger, get_plugin, get_loaded_plugins
from nonebot.exception import FinishedException
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment, MessageEvent, PrivateMessageEvent, GroupMessageEvent
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.plugin import PluginMetadata

try:
    from nonebot_plugin_htmlrender import md_to_pic
except ImportError:
    md_to_pic = None

__plugin_meta__ = PluginMetadata(
    name="Bot管理",
    description="管理Bot上下线提醒、重启关闭、插件管理及商店功能",
    usage="""
    重启: 重启Bot
    关闭: 关闭Bot
    插件列表: 查看已加载插件
    商店查询 [关键词]: 查询插件商店
    安装插件 [插件名]: 安装插件
    更新插件 [插件名]: 更新插件
    """,
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
