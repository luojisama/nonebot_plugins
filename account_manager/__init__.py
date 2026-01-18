import httpx
import re
import os
from nonebot import on_command, on_request, logger, get_plugin_config
from nonebot.adapters.onebot.v11 import (
    Bot, 
    Message, 
    MessageSegment, 
    FriendRequestEvent, 
    GroupRequestEvent,
    ActionFailed
)
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.exception import FinishedException

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="账号管理",
    description="管理 Bot 账号，包括好友申请、邀群管理、修改头像和昵称、群发消息、发说说",
    usage=(
        "/修改昵称 [新昵称]\n"
        "/修改头像 [图片/URL]\n"
        "/申请列表 - 查看待处理申请\n"
        "/同意 [序号]\n"
        "/拒绝 [序号]\n"
        "/发布群消息 [群号] [内容] - 管理员向指定群发送消息\n"
        "/发布说说 [内容] - 管理员发布 QQ 空间说说\n"
        "/更新空间Cookie - 自动获取并更新空间发布权限"
    ),
)

plugin_config = get_plugin_config(Config)

# 存储待处理申请
pending_requests = {}

# --- 处理器 ---

friend_request = on_request(priority=1)

@friend_request.handle()
async def handle_request(bot: Bot, event: FriendRequestEvent):
    req_id = f"friend_{event.user_id}_{event.time}"
    pending_requests[req_id] = event
    
    msg = f"收到好友申请：\nQQ：{event.user_id}\n验证信息：{event.comment}\n使用 /同意 或 /拒绝 处理"
    for admin in bot.config.superusers:
        await bot.send_private_msg(user_id=int(admin), message=msg)

group_request = on_request(priority=1)

@group_request.handle()
async def handle_group_request(bot: Bot, event: GroupRequestEvent):
    if event.sub_type == "add":
        type_str = "申请加群"
    else:
        type_str = "邀请入群"
        
    req_id = f"group_{event.group_id}_{event.user_id}_{event.time}"
    pending_requests[req_id] = event
    
    msg = f"收到{type_str}：\n群号：{event.group_id}\nQQ：{event.user_id}\n验证信息：{event.comment}\n使用 /同意 或 /拒绝 处理"
    for admin in bot.config.superusers:
        await bot.send_private_msg(user_id=int(admin), message=msg)

# --- 指令 ---

set_nickname = on_command("修改昵称", permission=SUPERUSER, priority=5, block=True)

@set_nickname.handle()
async def handle_set_nickname(bot: Bot, arg: Message = CommandArg()):
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
async def handle_set_face(bot: Bot, arg: Message = CommandArg()):
    img_url = ""
    
    # 优先从参数中获取图片
    for seg in arg:
        if seg.type == "image":
            img_url = seg.data["url"]
            break
    
    # 如果没有图片，尝试从文本获取 URL
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

list_requests = on_command("申请列表", permission=SUPERUSER, priority=5, block=True)

@list_requests.handle()
async def handle_list_requests():
    if not pending_requests:
        await list_requests.finish("当前没有待处理申请")
        
    msg = "待处理申请列表：\n"
    for i, (req_id, event) in enumerate(pending_requests.items(), 1):
        if isinstance(event, FriendRequestEvent):
            msg += f"{i}. [好友] QQ:{event.user_id}\n"
        else:
            type_str = "加群" if event.sub_type == "add" else "邀群"
            msg += f"{i}. [{type_str}] 群:{event.group_id} QQ:{event.user_id}\n"
            
    await list_requests.finish(msg.strip())

approve = on_command("同意", permission=SUPERUSER, priority=5, block=True)

@approve.handle()
async def handle_approve(bot: Bot, arg: Message = CommandArg()):
    index_str = arg.extract_plain_text().strip()
    if not index_str or not index_str.isdigit():
        await approve.finish("请输入正确的申请序号")
        
    index = int(index_str) - 1
    if index < 0 or index >= len(pending_requests):
        await approve.finish("序号超出范围")
        
    req_id = list(pending_requests.keys())[index]
    event = pending_requests.pop(req_id)
    
    try:
        if isinstance(event, FriendRequestEvent):
            await bot.set_friend_add_request(flag=event.flag, approve=True)
            await approve.finish(f"已同意 QQ:{event.user_id} 的好友申请")
        else:
            await bot.set_group_add_request(flag=event.flag, sub_type=event.sub_type, approve=True)
            await approve.finish(f"已同意 QQ:{event.user_id} 的群申请/邀请")
    except ActionFailed as e:
        await approve.finish(f"操作失败：{str(e)}")
    except FinishedException:
        raise

reject = on_command("拒绝", permission=SUPERUSER, priority=5, block=True)

@reject.handle()
async def handle_reject(bot: Bot, arg: Message = CommandArg()):
    index_str = arg.extract_plain_text().strip()
    if not index_str or not index_str.isdigit():
        await reject.finish("请输入正确的申请序号")
        
    index = int(index_str) - 1
    if index < 0 or index >= len(pending_requests):
        await reject.finish("序号超出范围")
        
    req_id = list(pending_requests.keys())[index]
    event = pending_requests.pop(req_id)
    
    try:
        if isinstance(event, FriendRequestEvent):
            await bot.set_friend_add_request(flag=event.flag, approve=False)
            await reject.finish(f"已拒绝 QQ:{event.user_id} 的好友申请")
        else:
            await bot.set_group_add_request(flag=event.flag, sub_type=event.sub_type, approve=False)
            await reject.finish(f"已拒绝 QQ:{event.user_id} 的群申请/邀请")
    except ActionFailed as e:
        await reject.finish(f"操作失败：{str(e)}")
    except FinishedException:
        raise

# --- 管理员扩展功能 ---

send_group_msg_cmd = on_command("发布群消息", aliases={"发送群消息"}, permission=SUPERUSER, priority=5, block=True)

@send_group_msg_cmd.handle()
async def handle_send_group_msg(bot: Bot, arg: Message = CommandArg()):
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

# --- 空间功能支持 ---

def save_cookie_to_env(cookie: str):
    """将 Cookie 持久化保存到 .env.prod 文件"""
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
    """计算 QQ 空间的 g_tk (bkn)"""
    hash_val = 5381
    for char in p_skey:
        hash_val += (hash_val << 5) + ord(char)
    return hash_val & 0x7fffffff

async def publish_qzone_shuo(content: str, bot_id: str) -> tuple[bool, str]:
    """通过 HTTP 请求发布说说"""
    cookie = plugin_config.qzone_cookie
    if not cookie:
        return False, "未配置 Qzone Cookie"
    
    try:
        # 提取 p_skey (必要字段)
        pskey_match = re.search(r"p_skey=([^; ]+)", cookie)
        if not pskey_match:
            return False, "Cookie 缺少 p_skey 字段"
        p_skey = pskey_match.group(1)
        
        # 提取 uin，如果不存在则使用机器人 QQ
        uin_match = re.search(r"uin=[o0]*(\d+)", cookie)
        qq = uin_match.group(1) if uin_match else bot_id
        
        # 重新整理 Cookie 确保包含关键字段
        formatted_cookie = f"uin=o{qq}; p_skey={p_skey};"
        if "skey=" in cookie:
            skey_match = re.search(r"skey=([^; ]+)", cookie)
            if skey_match:
                formatted_cookie += f" skey={skey_match.group(1)};"
        
        g_tk = get_g_tk(p_skey)
        
        url = f"https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6?g_tk={g_tk}"
        
        data = {
            "con": content,
            "ugc_right": 1,
            "hostuin": qq,
            "format": "json",
            "qzreferrer": f"https://user.qzone.qq.com/{qq}"
        }
        
        headers = {
            "Cookie": formatted_cookie,
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 QQ/8.8.3"
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, data=data, headers=headers)
            if resp.status_code != 200:
                return False, f"请求失败，状态码：{resp.status_code}"
            
            resp_text = resp.text
            if '"code":0' in resp_text or '"code": 0' in resp_text:
                return True, "发布成功"
            else:
                return False, f"发布失败，返回：{resp_text[:100]}"
                
    except Exception as e:
        return False, f"发生异常：{str(e)}"

publish_shuo_cmd = on_command("发布说说", aliases={"发说说"}, permission=SUPERUSER, priority=5, block=True)

@publish_shuo_cmd.handle()
async def handle_publish_shuo(bot: Bot, arg: Message = CommandArg()):
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
async def handle_update_cookie(bot: Bot):
    try:
        # 调用 OneBot 标准 API 获取 Cookie
        cookies_resp = await bot.get_cookies(domain="qzone.qq.com")
        cookie = cookies_resp.get("cookies")
        
        if not cookie:
            await update_cookie_cmd.finish("自动获取 Cookie 失败，返回结果为空。")
            
        # 验证是否包含必要字段
        if "p_skey" not in cookie:
            await update_cookie_cmd.finish(f"获取到的 Cookie 不完整（缺少 p_skey），请确保机器人已正常登录且环境支持。\n当前获取结果：{cookie[:50]}...")
            
        # 如果没有 uin，尝试手动补充（有些实现只返回 p_skey 部分）
        if "uin=" not in cookie:
            cookie = f"uin=o{bot.self_id}; {cookie}"
            
        # 更新内存中的配置
        plugin_config.qzone_cookie = cookie
        
        # 持久化到 .env 文件
        save_cookie_to_env(cookie)
        
        await update_cookie_cmd.finish(f"✅ 空间 Cookie 已自动更新并持久化保存！\n当前账号：{bot.self_id}\n你可以尝试发送 /发布说说 了。")
        
    except ActionFailed as e:
        await update_cookie_cmd.finish(f"调用 API 失败：{str(e)}")
    except FinishedException:
        raise
    except Exception as e:
        await update_cookie_cmd.finish(f"发生异常：{str(e)}")
