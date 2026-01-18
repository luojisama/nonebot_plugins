from nonebot import on_command, logger, require
from nonebot.exception import FinishedException, MatcherException
require("nonebot_plugin_htmlrender")

from nonebot.adapters.onebot.v11 import Message, MessageSegment, Bot, Event
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
import httpx
import re
from datetime import datetime
from .crawler import FiveEEventCrawler, FiveECrawler, PWCrawler
from .renderer import (
    render_events_card, 
    render_matches_card, 
    render_stats_card, 
    render_player_detail,
    render_pw_stats_card
)

__plugin_meta__ = PluginMetadata(
    name="CS Pro & 5E Stats",
    description="查询 CS 职业选手信息、5E 平台战绩及热门赛事",
    usage="cs查询 [选手名] | cs赛事 | 5e [ID/昵称] | pw [ID/昵称] | pwlogin [手机号] [验证码]",
)

# Commands
cs_search = on_command("cs查询", aliases={"cs选手", "csplayer"}, priority=5, block=True)
game_search = on_command("cs赛事", aliases={"赛事", "csgo赛事", "cs2赛事"}, priority=5, block=True)
five_e_stats = on_command("5e", aliases={"5e战绩", "5e查询", "cs战绩"}, priority=5, block=True)
pw_stats = on_command("pw", aliases={"pw战绩", "pw查询", "完美战绩"}, priority=5, block=True)
pw_login = on_command("pwlogin", aliases={"完美登录"}, priority=5, block=True)

# Shared Crawler Instances
event_crawler = FiveEEventCrawler()
five_e_crawler = FiveECrawler()
pw_crawler = PWCrawler()

@cs_search.handle()
async def handle_cs_search(args: Message = CommandArg()):
    query = args.extract_plain_text().strip()
    if not query:
        await cs_search.finish("请输入选手名称，例如: cs查询 sh1ro")

    search_api = "https://api.viki.moe/pw-cs/search"
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(search_api, params={"type": "player", "s": query})
            data = resp.json()
        except Exception as e:
            await cs_search.finish(f"查询出错: {e}")

    if not isinstance(data, list):
        await cs_search.finish(f"查询出错: {data.get('message') if isinstance(data, dict) else '未知错误'}")

    if not data:
        await cs_search.finish("未找到相关选手，请检查名称是否正确")

    player_brief = data[0]
    hltv_id = player_brief.get("hltv_id")
    if not hltv_id:
        await cs_search.finish("未找到选手的HLTV ID")

    detail_api = f"https://api.viki.moe/pw-cs/player/{hltv_id}"
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(detail_api)
            player = resp.json()
        except Exception as e:
            await cs_search.finish(f"获取选手详情出错: {e}")

    # Render detailed player card
    try:
        image_bytes = await render_player_detail(player)
    except Exception as e:
        logger.error(f"Error rendering player detail: {e}")
        # Fallback to simple text if rendering fails
        name = player.get("name", "未知")
        team_name = player.get("team", {}).get("name", "无战队")
        await cs_search.finish(f"选手: {name}\n战队: {team_name}\n(图片渲染失败，请检查日志)")

    await cs_search.finish(MessageSegment.image(image_bytes))

@game_search.handle()
async def handle_game_search():
    await game_search.send("正在获取实时赛程与赛事信息...")
    
    try:
        # Try 5EPlay matches first (Real-time scores & logos)
        matches = await event_crawler.get_matches()
        if matches:
            image_bytes = await render_matches_card(matches)
            await game_search.finish(MessageSegment.image(image_bytes))
            
        # Fallback to events list if matches fail
        events = await event_crawler.get_events()
        if events:
            image_bytes = await render_events_card(events)
            await game_search.finish(MessageSegment.image(image_bytes))
            
        # Final fallback to Viki API
        await game_search.finish("暂无实时赛程数据。")
        
    except (FinishedException, MatcherException):
        raise
    except Exception as e:
        logger.error(f"Error in game_search: {e}")
        await game_search.finish(f"查询赛事失败: {e}")

@five_e_stats.handle()
async def handle_five_e_stats(arg: Message = CommandArg()):
    input_str = arg.extract_plain_text().strip()
    if not input_str:
        await five_e_stats.finish("请输入5E平台玩家域名、ID或昵称，例如：/5e 15429443s91f72")
    
    await five_e_stats.send(f"正在查询 5E 玩家 {input_str}...")
    
    domain = input_str
    
    try:
        # Check if it's a domain/ID
        is_id = re.match(r'^\d+s\w+$|^\d+$|^[0-9a-f-]{36}$', input_str)
        
        search_info = {}
        if not is_id:
            # Search for the player
            search_results = await five_e_crawler.search_player(input_str)
            if not search_results:
                await five_e_stats.finish(f"未找到昵称为 {input_str} 的玩家。")
            
            # Use the first search result
            search_info = search_results[0]
            domain = search_info['domain']
            await five_e_stats.send(f"匹配到玩家：{search_info['name']} ({domain})，正在获取详细战绩...")

        # Get data
        data = await five_e_crawler.get_player_data(domain)
        
        # Merge search info if page extraction failed
        if not data.get("nickname") or data["nickname"] == "Unknown":
            if search_info.get("name"):
                data["nickname"] = search_info["name"]
        if not data.get("avatar") and search_info.get("avatar"):
            data["avatar"] = search_info["avatar"]
        
        # Check if we got any stats
        if not data.get("stats") or not data["stats"].get("career"):
            await five_e_stats.finish(f"未找到玩家 {domain} 的有效战绩数据。")
            
        # Render image
        image_bytes = await render_stats_card(data)
        
        # Send image
        await five_e_stats.finish(MessageSegment.image(image_bytes))
        
    except (FinishedException, MatcherException):
        raise
    except Exception as e:
        logger.error(f"Error in five_e_stats: {e}")
        await five_e_stats.finish(f"5E 查询失败: {str(e)}")

@pw_login.handle()
async def handle_pw_login(arg: Message = CommandArg()):
    args = arg.extract_plain_text().strip().split()
    if len(args) != 2:
        await pw_login.finish("请输入手机号和验证码，例如：/pwlogin 13800138000 123456")
    
    mobile, code = args
    await pw_login.send(f"正在尝试登录完美世界平台...")
    
    result = await pw_crawler.login(mobile, code)
    if "error" in result:
        await pw_login.finish(f"登录失败: {result['error']}")
    
    nickname = result.get("nickname", "未知")
    await pw_login.finish(f"登录成功！欢迎回来，{nickname}。Session 已更新。")

@pw_stats.handle()
async def handle_pw_stats(arg: Message = CommandArg()):
    input_str = arg.extract_plain_text().strip()
    if not input_str:
        await pw_stats.finish("请输入完美世界平台玩家昵称或 SteamId，例如：/pw sh1ro")
    
    await pw_stats.send(f"正在查询完美世界玩家 {input_str}...")
    
    try:
        # Check if it's a SteamId (numerical)
        is_steam_id = input_str.isdigit() and len(input_str) > 10
        
        target_steam_id = input_str
        search_info = {}
        
        if not is_steam_id:
            # Search for the player
            search_results = await pw_crawler.search_player(input_str)
            if not search_results:
                await pw_stats.finish(f"未找到昵称为 {input_str} 的玩家。")
            
            # Use the first search result
            search_info = search_results[0]
            target_steam_id = str(search_info['steamId'])
            await pw_stats.send(f"匹配到玩家：{search_info.get('pvpNickName', '未知')}，正在获取详细战绩...")

        # Get data
        data = await pw_crawler.get_player_data(target_steam_id)
        
        if "error" in data:
            await pw_stats.finish(f"查询完美战绩失败: {data['error']}")
            
        if not data or not data.get("stats"):
            await pw_stats.finish(f"未找到玩家 {target_steam_id} 的有效战绩数据。")
            
        # Merge search info if detail API lacked some summary info
        if not data.get("summary", {}).get("nickname"):
            data["summary"]["nickname"] = search_info.get("pvpNickName", "Unknown")
        if not data.get("summary", {}).get("avatarUrl"):
            data["summary"]["avatarUrl"] = search_info.get("pvpAvatar")
            
        # Render image
        image_bytes = await render_pw_stats_card(data)
        
        # Send image
        await pw_stats.finish(MessageSegment.image(image_bytes))
        
    except (FinishedException, MatcherException):
        raise
    except Exception as e:
        logger.error(f"Error in pw_stats: {e}")
        await pw_stats.finish(f"完美战绩查询失败: {str(e)}")
