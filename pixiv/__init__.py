import re
import random
import asyncio
from datetime import datetime
from typing import List, Optional
from nonebot import on_command, on_regex, get_bot, logger, require, get_driver, get_plugin_config
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent, MessageSegment, Message
from nonebot.permission import SUPERUSER
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.params import CommandArg, RegexGroup
from nonebot.matcher import Matcher
from nonebot.exception import FinishedException

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .data_source import pixiv_client
from .manager import manager
from .model import Illust
from .config import Config

import hashlib
import base64
import secrets
from urllib.parse import urlencode

plugin_config = get_plugin_config(Config)

__plugin_meta__ = PluginMetadata(
    name="Pixiv 助手",
    description="提供 Pixiv 插画搜索、画师推荐、作品关注、定时推送及群聊黑名单管理功能",
    usage="""
    [基础指令]
    pixiv帮助 : 查看详细指令说明
    看看图 <PID> : 查看指定 ID 的插画
    来张图 : 获取一张随机推荐插画
    来<N>张<Tag>图 : 按标签搜索插画 (如: 来3张初音图)
    来<N>张<画师>老师的图 : 按画师名搜索插画
    来张色图 : (仅限R18群) 随机获取高质量 R18 插画
    pixiv日榜/周榜/月榜 : 获取对应时间段的排行榜 Top 10 插画

    [订阅与关注] (需管理员权限)
    pixiv关注 添加 <画师名> : 关注画师，有新作品时推送
    pixiv关注 删除 <画师名> : 取消关注
    pixiv关注 列表 : 查看本群关注列表
    pixiv订阅 添加 <tag/user> <关键词> <HH:mm> : 添加每日定时推送
    pixiv订阅 删除 <索引> : 删除指定订阅任务
    pixiv订阅 列表 : 查看本群订阅列表
    pixiv日榜订阅 开启/关闭 : 开启/关闭每日 Pixiv 日榜 Top 10 推送 (06:00)

    [管理指令] (需管理员/超管权限)
    pixiv r18 开启/关闭 : 开启或关闭本群 R18 内容显示
    pixiv黑名单 添加/删除/状态 : (仅超管) 管理本群插件禁用状态 (默认所有群可用)
    pixiv登录 : (仅超管) 更新 Pixiv 账号 Token
    """,
    config=Config,
)

# --- Helpers ---

async def is_enabled_group(event: GroupMessageEvent) -> bool:
    # Default True, unless in blacklist
    return manager.is_group_enabled(str(event.group_id)) or await SUPERUSER(get_bot(), event)

async def send_illust_to_group(bot: Bot, group_id: int, illust: Illust):
    """
    Helper to send illust to a group (used by scheduler and matchers).
    """
    # R18 Check
    if illust.is_r18: # R18 or R18G
        if not manager.is_r18_enabled(str(group_id)):
            # If called by scheduler, just skip silent
            # If called by matcher, we might want to notify, but this shared function returns None
            return False

    msg_text = f"Title: {illust.title}\nAuthor: {illust.user.name}\nPID: {illust.id}"
    
    try:
        if illust.page_count > 1:
            # Forward Message for multi-page
            urls = illust.all_large_image_urls
            
            # Split into batches (e.g. 10 images per forward msg to avoid Timeout)
            BATCH_SIZE = 10
            total_pages = len(urls)
            
            for i in range(0, total_pages, BATCH_SIZE):
                nodes = []
                batch_urls = urls[i : i + BATCH_SIZE]
                
                # Header node
                if i == 0:
                    header_text = msg_text
                    if total_pages > BATCH_SIZE:
                        header_text += f"\n(共 {total_pages} P，正在分批发送...)"
                    
                    nodes.append(MessageSegment.node_custom(
                        user_id=int(bot.self_id),
                        nickname="Pixiv助手",
                        content=Message(header_text)
                    ))
                
                for url in batch_urls:
                    img_data = await pixiv_client.download_image(url)
                    if img_data:
                        nodes.append(MessageSegment.node_custom(
                            user_id=int(bot.self_id),
                            nickname="Pixiv助手",
                            content=Message(MessageSegment.image(img_data))
                        ))
                
                if nodes:
                    # Retry logic for sending batch to handle Timeout/ActionFailed
                    sent = False
                    for attempt in range(3):
                        try:
                            await bot.call_api("send_group_forward_msg", group_id=group_id, messages=nodes)
                            sent = True
                            break
                        except Exception as e:
                            logger.warning(f"Batch {(i//BATCH_SIZE)+1} send failed (Attempt {attempt+1}/3): {repr(e)}")
                            if attempt < 2:
                                await asyncio.sleep(3)
                    
                    if not sent:
                        logger.error(f"Batch {(i//BATCH_SIZE)+1} failed to send after 3 attempts.")
                        
                        # Fallback for first batch failure: Send images directly
                        if i == 0:
                            logger.warning("Forward message failed, falling back to direct send (Limit 3).")
                            try:
                                # Add Link for easy access if images are blocked
                                fallback_msg = f"⚠️ 合并转发失败，转为直接发送 (显示前3张):\n{msg_text}\nLink: https://www.pixiv.net/artworks/{illust.id}"
                                await bot.send_group_msg(group_id=group_id, message=fallback_msg)
                                
                                count = 0
                                for url in urls[:3]:
                                    img_data = await pixiv_client.download_image(url)
                                    if img_data:
                                        try:
                                            await bot.send_group_msg(group_id=group_id, message=MessageSegment.image(img_data))
                                            count += 1
                                            await asyncio.sleep(1)
                                        except Exception as e:
                                            logger.error(f"Direct send failed: {e}")
                            except Exception as e:
                                logger.error(f"Fallback message failed: {e}")
                            
                            # Stop processing further batches since main method failed
                            return False
                    
                    if i + BATCH_SIZE < total_pages:
                        await asyncio.sleep(3)
        else:
            # Single page
            img_data = await pixiv_client.download_image(illust.image_urls.large)
            if img_data:
                await bot.send_group_msg(group_id=group_id, message=MessageSegment.image(img_data) + msg_text)
            else:
                await bot.send_group_msg(group_id=group_id, message=f"❌ 图片下载失败\n{msg_text}")
        return True
    except Exception as e:
        logger.error(f"Send illust to group failed: {e}")
        # Fallback to simple text if possible, or just log
        return False

async def send_illusts_to_group(bot: Bot, group_id: int, illusts: List[Illust], title: str):
    """
    Send multiple illusts as a forward message (e.g. for ranking).
    """
    nodes = []
    
    # Header
    nodes.append(MessageSegment.node_custom(
        user_id=int(bot.self_id),
        nickname="Pixiv助手",
        content=Message(title)
    ))
    
    for idx, illust in enumerate(illusts):
        img_data = await pixiv_client.download_image(illust.image_urls.large)
        if img_data:
            msg = Message()
            msg.append(MessageSegment.image(img_data))
            msg.append(f"\n#{idx+1} {illust.title}\nAuthor: {illust.user.name}\nPID: {illust.id}")
            
            nodes.append(MessageSegment.node_custom(
                user_id=int(bot.self_id),
                nickname="Pixiv助手",
                content=msg
            ))
            
    if len(nodes) > 1:
        try:
             await bot.call_api("send_group_forward_msg", group_id=group_id, messages=nodes)
        except Exception as e:
             logger.error(f"Send ranking failed: {e}")
             await bot.send_group_msg(group_id=group_id, message="❌ 发送排行榜失败，可能是图片过多或网络问题。")

async def send_illust_msg(matcher: Matcher, event: MessageEvent, illust: Illust, allow_error_finish: bool = True) -> bool:
    """
    Wrapper for matchers to send illust. Returns True if sent, False if filtered/failed.
    """
    group_id = None
    if isinstance(event, GroupMessageEvent):
        group_id = event.group_id
    
    if group_id:
        # Check R18 again for feedback
        if illust.is_r18 and not manager.is_r18_enabled(str(group_id)):
            if allow_error_finish:
                await matcher.finish("🔞 本群未开启 R18 模式，无法查看该插画")
            return False

        bot = get_bot()
        await send_illust_to_group(bot, group_id, illust)
        return True
    else:
        # Private message
        msg_text = f"Title: {illust.title}\nAuthor: {illust.user.name}\nPID: {illust.id}"
        # Simplified private send
        if illust.page_count > 1:
             await matcher.send(msg_text)
             for url in illust.all_large_image_urls[:10]:
                 img_data = await pixiv_client.download_image(url)
                 if img_data:
                     await matcher.send(MessageSegment.image(img_data))
        else:
            img_data = await pixiv_client.download_image(illust.image_urls.large)
            if img_data:
                await matcher.send(MessageSegment.image(img_data) + msg_text)
        return True

async def send_illust(matcher: Matcher, event: MessageEvent, illust_id: int):
    try:
        illust = await pixiv_client.get_illust(illust_id)
        if not illust:
            await matcher.finish("⚠️ 未找到该插画")
            return
        await send_illust_msg(matcher, event, illust)
    except Exception as e:
        logger.error(f"Get illust error: {e}")
        await matcher.finish(f"❌ 获取插画出错: {e}")

# --- Commands ---

# 1. Login (Superuser)
login_cmd = on_command("pixiv登录", aliases={"pixiv login"}, permission=SUPERUSER, priority=1, block=True)

@login_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    arg = args.extract_plain_text().strip()
    if not arg:
        # Step 1: Generate Login URL
        code_verifier = secrets.token_urlsafe(32)
        code_challenge = hashlib.sha256(code_verifier.encode()).digest()
        code_challenge = base64.urlsafe_b64encode(code_challenge).rstrip(b'=').decode()
        
        # Store code_verifier temporarily (using manager or just global dict?)
        # For simplicity, we can't easily store state across calls without DB/Redis for simple plugin.
        # But we can ask user to run a python script locally.
        # Actually, let's just output the instructions for local script as it's more stable.
        
        msg = (
            "由于 Pixiv 登录流程限制，请按照以下步骤获取 Token：\n"
            "1. 确保服务器已安装 `gppt` (pip install gppt)\n"
            "2. 在服务器终端运行 `gppt login`\n"
            "3. 按照提示登录并在浏览器中复制回调 URL\n"
            "4. 脚本会自动更新 Refresh Token\n"
            "\n或者，使用指令 `pixiv登录 <REFRESH_TOKEN>` 直接设置。\n"
            "如果不能直接访问 Pixiv 的话注意设置好代理环境变量 HTTPS_PROXY"
        )
        await login_cmd.finish(msg)
    
    # If arg provided, assume it's a token
    manager.update_refresh_token(arg)
    
    # Try auth
    old_token = pixiv_client.refresh_token
    pixiv_client.refresh_token = arg
    await pixiv_client.auth()
    
    if pixiv_client.access_token:
         await login_cmd.finish("✅ Pixiv 登录成功！Refresh Token 已更新。")
    else:
         pixiv_client.refresh_token = old_token # Revert
         await login_cmd.finish("❌ 登录失败，请检查 Token 是否有效。")

# 2. Blacklist Management (Default Allow)
blacklist_cmd = on_command("pixiv黑名单", aliases={"pixiv blacklist", "黑名单管理"}, permission=SUPERUSER, priority=1, block=True)

@blacklist_cmd.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    cmd = args.extract_plain_text().strip()
    group_id = str(event.group_id)
    if cmd in ["add", "添加"]:
        manager.add_blacklist(group_id)
        await blacklist_cmd.finish("❌ 本群已加入 Pixiv 黑名单 (已禁用)")
    elif cmd in ["remove", "删除", "移除"]:
        manager.remove_blacklist(group_id)
        await blacklist_cmd.finish("✅ 本群已移除 Pixiv 黑名单 (已启用)")
    elif cmd in ["status", "状态"]:
        status = "已启用" if manager.is_group_enabled(group_id) else "已禁用"
        await blacklist_cmd.finish(f"当前状态：{status}")
    else:
        await blacklist_cmd.finish("使用方法：pixiv黑名单 添加/删除/状态")

# 3. Get Illust by ID
illust_id_cmd = on_regex(r"^/?看看图\s*(\d+)$", priority=5, block=True, rule=is_enabled_group)

@illust_id_cmd.handle()
async def _(bot: Bot, event: MessageEvent, matcher: Matcher, groups = RegexGroup()):
    illust_id = int(groups[0])
    await send_illust(matcher, event, illust_id)

# 4. Random Illust
random_cmd = on_regex(r"^/?来([0-9一二三四五六七八九十]*)(?:张|份)(?:(.+?)老师的)?(.+?)?图$", priority=5, block=True, rule=is_enabled_group)

def parse_count(count_str: str) -> int:
    if not count_str: return 1
    cn_map = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
    if count_str in cn_map: return cn_map[count_str]
    try: return int(count_str)
    except: return 1

def filter_illusts(illusts: List[Illust], group_id: Optional[str] = None, r18_enabled: bool = False) -> List[Illust]:
    """
    Filter illusts based on config (min bookmarks, min view, exclude AI), R18 setting, and history.
    """
    filtered = []
    
    # Defaults if config is 0 (optimization)
    min_bookmarks = plugin_config.pixiv_min_bookmarks if plugin_config.pixiv_min_bookmarks > 0 else 100
    min_view = plugin_config.pixiv_min_view if plugin_config.pixiv_min_view > 0 else 1000
    
    for i in illusts:
        # Basic Filter
        if i.total_bookmarks < min_bookmarks: continue
        if i.total_view < min_view: continue
        if plugin_config.pixiv_exclude_ai and i.illust_ai_type == 2: continue
        
        # R18 Filter
        if i.is_r18 and not r18_enabled: continue
        
        # History Filter (Deduplication) - DISABLED per request 2025-01-26
        # if group_id and manager.is_in_history(group_id, i.id): continue
        
        filtered.append(i)
    return filtered

def weighted_choice(illusts: List[Illust], k: int, group_id: Optional[str] = None, r18_enabled: bool = False) -> List[Illust]:
    """
    Select k illusts using weighted random based on popularity (bookmarks/view).
    """
    pool = filter_illusts(illusts, group_id, r18_enabled)
    if not pool: return []
    
    # Calculate weights
    weights = []
    for i in pool:
        # Simple weight formula
        w = (i.total_bookmarks * 2) + i.total_view
        weights.append(w)
        
    selected = []
    # If k > len(pool), just return all
    if k >= len(pool):
        return pool
    
    # Copy for manipulation
    pool_weights = list(weights)
    
    # 3. Select
    for _ in range(k):
        if not pool: break
        # random.choices is available in Python 3.6+
        chosen = random.choices(pool, weights=pool_weights, k=1)[0]
        selected.append(chosen)
        
        # Remove chosen to avoid duplicates
        try:
            index = pool.index(chosen)
            pool.pop(index)
            pool_weights.pop(index)
        except ValueError:
            pass # Should not happen
            
    return selected

@random_cmd.handle()
async def _(bot: Bot, event: MessageEvent, matcher: Matcher, groups = RegexGroup()):
    count_str, user_name, tag = groups
    count = parse_count(count_str)
    count = min(count, 5)
    
    if user_name: user_name = user_name.strip()
    if tag: tag = tag.strip().replace(",", " ").replace("，", " ")
    
    group_id = str(event.group_id) if isinstance(event, GroupMessageEvent) else None
    r18_enabled = manager.is_r18_enabled(group_id) if group_id else False

    collected = []
    max_pages = 20 # Try up to 20 pages to find valid images (Increased for robustness)
    
    try:
        if user_name:
            await matcher.send(f"🔍 正在搜索画师 {user_name}...")
            users = await pixiv_client.search_user(user_name)
            if not users:
                await matcher.finish("⚠️ 未找到该画师")
                return
            target_user = users[0]
            # user_illusts currently returns latest 30. Pagination requires handling next_url which is complex.
            illusts = await pixiv_client.user_illusts(target_user.id)
            if not illusts:
                await matcher.finish("⚠️ 该画师没有插画作品")
                return
            
            selected = weighted_choice(illusts, min(count, len(illusts)), group_id=group_id, r18_enabled=r18_enabled)
            collected.extend(selected)

        elif tag:
            await matcher.send(f"🔍 正在搜索标签: {tag}")
            
            search_targets = ["partial_match_for_tags", "title_and_caption"]
            
            for target in search_targets:
                if len(collected) >= count: break
                
                if target == "title_and_caption" and not collected:
                     logger.info(f"Tag search failed, trying title_and_caption for {tag}")

                for page in range(max_pages):
                    if len(collected) >= count: break
                    
                    current_illusts = []
                    # Try popular first for first page
                    if page == 0:
                        current_illusts = await pixiv_client.search_illust(tag, sort="popular_desc", search_target=target, offset=0)
                    
                    if not current_illusts:
                        # Fallback or subsequent pages: date_desc
                        current_illusts = await pixiv_client.search_illust(tag, sort="date_desc", search_target=target, offset=page*30)
                
                    if not current_illusts:
                        break
                
                    # Filter out history duplicates for Tag search
                    if group_id:
                        current_illusts = [i for i in current_illusts if not manager.is_in_history(group_id, i.id)]

                    needed = count - len(collected)
                    batch_selected = weighted_choice(current_illusts, needed, group_id=group_id, r18_enabled=r18_enabled)
                        
                    existing_ids = {c.id for c in collected}
                    for i in batch_selected:
                        if i.id not in existing_ids:
                            collected.append(i)
                
                if collected:
                    break
                        
        else:
            await matcher.send("🔍 正在获取随机推荐...")
            for page in range(max_pages):
                if len(collected) >= count: break
                
                illusts = await pixiv_client.recommended_illusts(30)
                if not illusts:
                     illusts = await pixiv_client.search_illust("original", sort="popular_desc", offset=page*30)
                     if not illusts:
                         illusts = await pixiv_client.search_illust("original", offset=page*30)
                
                if not illusts: break

                needed = count - len(collected)
                batch_selected = weighted_choice(illusts, needed, group_id=group_id, r18_enabled=r18_enabled)
                
                existing_ids = {c.id for c in collected}
                for i in batch_selected:
                    if i.id not in existing_ids:
                        collected.append(i)
                        
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"Search error: {repr(e)}")
        await matcher.finish(f"❌ 搜索出错: {repr(e)}")
        return

    if not collected:
        # User requested to avoid "filtered/duplicate" prompts.
        await matcher.finish("⚠️ 未找到任何图片 (请尝试其他关键词)")
        return

    # Send collected images
    sent_count = 0
    for illust in collected[:count]:
        try:
            success = await send_illust_msg(matcher, event, illust, allow_error_finish=False)
            if success:
                sent_count += 1
                # Record history to prevent duplicates
                if group_id:
                    manager.record_history(group_id, illust.id)
            if len(collected) > 1: await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Send illust error: {repr(e)}")
            
    if sent_count == 0:
        # User requested to avoid "filtered/duplicate" prompts.
        await matcher.finish("⚠️ 未能发送图片 (可能下载失败)")

# 4.5. Setu Command (R18 Only)
# Regex matches: 来张色图, 来张涩图, 来张瑟图 (Homophones)
setu_cmd = on_regex(r"^/?来张[色涩瑟]图$", priority=4, block=True, rule=is_enabled_group)

@setu_cmd.handle()
async def _(bot: Bot, event: MessageEvent, matcher: Matcher):
    group_id = str(event.group_id) if isinstance(event, GroupMessageEvent) else None
    
    if not group_id:
        await matcher.finish("请在群聊中使用此命令")
        
    if not manager.is_r18_enabled(group_id):
        await matcher.finish("🔞 本群未开启 R18 模式，无法使用此功能")

    await matcher.send("🔍 正在寻找高质量 R18 图片...")
    
    try:
        # Loop to find valid candidates (Exclude Manga, Enforce R18, Exclude History)
        candidates = []
        for _ in range(5): # Try up to 5 different pages
            page = random.randint(0, 100) 
            # Mix popular and new
            sort_mode = "popular_desc" if random.random() < 0.8 else "date_desc"
            
            illusts = await pixiv_client.search_illust("R-18", sort=sort_mode, offset=page*30)
            
            if not illusts: continue

            for i in illusts:
                # 1. Exclude Manga (Prioritize Illust)
                if i.type != 'illust': 
                    continue
                
                # 2. Enforce R18 Tags (Must contain R-18 or R-18G)
                if not i.is_r18: 
                    continue
                    
                # 3. Filter duplicates from history
                if manager.is_in_history(group_id, i.id): 
                    continue
                
                candidates.append(i)
            
            if candidates: break

        if not candidates:
            await matcher.finish("⚠️ 未找到图片 (过滤了漫画/重复)")
            return
        
        # Use weighted_choice to pick high quality one
        # Explicitly allow R18
        selected = weighted_choice(candidates, 1, group_id=group_id, r18_enabled=True)
        
        if selected:
            success = await send_illust_msg(matcher, event, selected[0])
            if success:
                manager.record_history(group_id, selected[0].id)
        else:
             await matcher.finish("⚠️ 图片获取失败")
             
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"Setu command error: {repr(e)}")
        await matcher.finish(f"❌ 出错: {e}")

# 5. Ranking Command
rank_cmd = on_regex(r"^/?pixiv(日|周|月)榜$", priority=5, block=True, rule=is_enabled_group)

@rank_cmd.handle()
async def _(bot: Bot, event: GroupMessageEvent, groups = RegexGroup()):
    mode_cn = groups[0]
    mode_map = {
        "日": "day",
        "周": "week",
        "月": "month"
    }
    mode = mode_map.get(mode_cn, "day")
    
    await rank_cmd.send(f"正在获取 Pixiv {mode_cn}榜...")
    
    try:
        # Get Ranking
        illusts = await pixiv_client.illust_ranking(mode=mode)
        
        # Filter: Type=Illust, No R18
        filtered = []
        for i in illusts:
            if i.type == "illust" and not i.is_r18:
                 filtered.append(i)
        
        if not filtered:
            await rank_cmd.finish("⚠️ 未找到符合条件的排行榜数据。")
            
        # Take Top 10
        top_10 = filtered[:10]
        
        await send_illusts_to_group(bot, event.group_id, top_10, f"Pixiv {mode_cn}榜 Top 10")
        
    except Exception as e:
        logger.error(f"Ranking command failed: {e}")
        await rank_cmd.finish(f"❌ 获取排行榜失败: {e}")

# 6. Scheduled Push
subscription_cmd = on_command("pixiv订阅", aliases={"pixiv subscription"}, permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER, priority=1, block=True)

@subscription_cmd.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    cmd = args.extract_plain_text().strip().split()
    group_id = str(event.group_id)
    
    if not cmd:
        await subscription_cmd.finish("使用方法：\n添加: pixiv订阅 添加 <tag/user> <关键词> <HH:mm>\n删除: pixiv订阅 删除 <索引>\n列表: pixiv订阅 列表")
        
    action = cmd[0]
    
    if action in ["add", "添加"]:
        if len(cmd) != 4:
            await subscription_cmd.finish("参数错误。格式：pixiv订阅 添加 <tag/user> <关键词> <HH:mm>")
        sub_type, keyword, time_str = cmd[1], cmd[2], cmd[3]
        if sub_type not in ["tag", "user"]:
            await subscription_cmd.finish("类型错误，仅支持 tag 或 user")
        
        try:
            # Validate time format
            datetime.strptime(time_str, "%H:%M")
            manager.add_subscription(group_id, sub_type, keyword, time_str)
            await subscription_cmd.finish(f"✅ 已添加订阅：每天 {time_str} 推送 {sub_type}:{keyword}")
        except ValueError:
            await subscription_cmd.finish("时间格式错误，应为 HH:mm")
            
    elif action in ["del", "delete", "删除"]:
        if len(cmd) != 2:
            await subscription_cmd.finish("参数错误。格式：pixiv订阅 删除 <索引>")
        try:
            index = int(cmd[1]) - 1
            if manager.remove_subscription(group_id, index):
                 await subscription_cmd.finish("✅ 删除成功")
            else:
                 await subscription_cmd.finish("❌ 删除失败，索引无效")
        except ValueError:
             await subscription_cmd.finish("参数错误，索引应为数字")

    elif action in ["list", "列表"]:
        subs = manager.get_subscriptions(group_id)
        if not subs:
            await subscription_cmd.finish("当前无订阅")
        
        msg = "📋 当前订阅列表：\n"
        for i, sub in enumerate(subs):
            msg += f"{i+1}. [{sub['type']}] {sub['keyword']} ({sub['schedule']})\n"
        await subscription_cmd.finish(msg)

# 7. Ranking Subscription
rank_sub_cmd = on_command("pixiv日榜订阅", permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER, priority=1, block=True)

@rank_sub_cmd.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    arg = args.extract_plain_text().strip()
    group_id = str(event.group_id)
    
    if arg == "开启":
        # Remove existing to avoid duplicates
        manager.remove_subscription_by_type(group_id, "ranking")
        manager.add_subscription(group_id, "ranking", "day", "06:00")
        await rank_sub_cmd.finish("✅ 已开启 Pixiv 日榜订阅 (每天 06:00 推送)")
        
    elif arg == "关闭":
        if manager.remove_subscription_by_type(group_id, "ranking"):
            await rank_sub_cmd.finish("✅ 已关闭 Pixiv 日榜订阅")
        else:
            await rank_sub_cmd.finish("⚠️ 当前未开启日榜订阅")
            
    else:
        await rank_sub_cmd.finish("使用方法：pixiv日榜订阅 开启/关闭")

# Scheduler
@scheduler.scheduled_job("cron", minute="*")
async def schedule_check():
    # Check subscriptions
    now = datetime.now().strftime("%H:%M")
    
    # Optimization: Fetch ranking once if needed
    daily_ranking_cache = None
    
    for sub in manager.subscriptions:
        if sub.get('schedule') == now:
            group_id = sub.get('group_id')
            keyword = sub.get('keyword')
            sub_type = sub.get('type')
            
            try:
                bot = get_bot()
                
                if sub_type == 'tag':
                     await bot.send_group_msg(group_id=int(group_id), message=f"⏰ 订阅推送: {keyword}")
                     illusts = await pixiv_client.search_illust(keyword, sort="date_desc")
                     # Filter R18 and History
                     illusts = [i for i in illusts if not manager.is_in_history(group_id, i.id)]
                     if not manager.is_r18_enabled(group_id):
                         illusts = [i for i in illusts if not i.is_r18]
                         
                     if illusts:
                         success = await send_illust_to_group(bot, int(group_id), illusts[0])
                         if success:
                             manager.record_history(group_id, illusts[0].id)
                             
                elif sub_type == 'ranking':
                    # Ranking Push
                    if daily_ranking_cache is None:
                        # Fetch and Filter
                        raw_illusts = await pixiv_client.illust_ranking(mode='day')
                        daily_ranking_cache = raw_illusts
                    
                    # Per-group filtering
                    filtered = []
                    for i in daily_ranking_cache:
                        if i.type == "illust":
                            if i.is_r18 and not manager.is_r18_enabled(group_id):
                                continue
                            filtered.append(i)
                    
                    top_10 = filtered[:10]
                    if top_10:
                        await send_illusts_to_group(bot, int(group_id), top_10, f"Pixiv 日榜 Top 10 ({now})")
                
                elif sub_type == 'user':
                    # Placeholder for user subscription
                    pass
                    
            except Exception as e:
                logger.error(f"Push error for group {group_id}: {e}")
