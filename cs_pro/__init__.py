import re
from datetime import datetime

import httpx
from nonebot import get_driver, get_plugin_config, logger, on_command, require
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.exception import FinishedException, MatcherException
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

require("nonebot_plugin_htmlrender")

from .binding_store import BindingStore
from .config import Config
from .crawler import FiveEEventCrawler, FiveECrawler, PWCrawler
from .llm import LLMEvaluator
from .match_service import MatchService, parse_bind_args, parse_match_args
from .renderer import (
    render_events_card,
    render_match_detail_card,
    render_matches_card,
    render_player_detail,
    render_pw_stats_card,
    render_stats_card,
)

plugin_config = get_plugin_config(Config)
driver_config = get_driver().config

__plugin_meta__ = PluginMetadata(
    name="CS Pro & Stats",
    description="CS职业选手查询、5E/完美/官匹战绩、绑定与每把详细战绩分析",
    usage=(
        "cs查询 [选手]\n"
        "cs赛事\n"
        "5e [ID/昵称]\n"
        "pw [ID/昵称]\n"
        "pwlogin [手机号] [验证码]\n"
        "bind [platform] [name]\n"
        "match [platform] [@群友] [round]"
    ),
    config=Config,
)

# Commands
cs_search = on_command("cs查询", aliases={"cs选手", "csplayer"}, priority=plugin_config.cs_pro_priority, block=True)
game_search = on_command("cs赛事", aliases={"赛事", "csgo赛事", "cs2赛事"}, priority=plugin_config.cs_pro_priority, block=True)
five_e_stats = on_command("5e", aliases={"5e战绩", "5e查询", "cs战绩"}, priority=plugin_config.cs_pro_priority, block=True)
pw_stats = on_command("pw", aliases={"pw战绩", "pw查询", "完美战绩"}, priority=plugin_config.cs_pro_priority, block=True)
pw_login = on_command("pwlogin", aliases={"完美登录"}, priority=plugin_config.cs_pro_priority, block=True)
bind_cmd = on_command("bind", aliases={"绑定", "添加", "绑定用户", "添加用户"}, priority=plugin_config.cs_pro_priority, block=True)
match_cmd = on_command("match", aliases={"战绩", "查询战绩"}, priority=plugin_config.cs_pro_priority, block=True)

# Shared services
store = BindingStore(plugin_config.cs_pro_bind_db_path)
match_service = MatchService(timeout=plugin_config.cs_pro_http_timeout)
_llm_api_key = (plugin_config.cs_pro_llm_api_key or "").strip()
_llm_api_type = plugin_config.cs_pro_llm_api_type
_llm_api_url = plugin_config.cs_pro_llm_api_url
_llm_model = plugin_config.cs_pro_llm_model
if not _llm_api_key:
    _llm_api_key = str(getattr(driver_config, "personification_api_key", "") or "").strip()
    _llm_api_type = str(getattr(driver_config, "personification_api_type", "openai") or "openai")
    _llm_api_url = str(getattr(driver_config, "personification_api_url", "https://api.openai.com/v1") or "https://api.openai.com/v1")
    _llm_model = str(getattr(driver_config, "personification_model", "gpt-4o-mini") or "gpt-4o-mini")
llm = LLMEvaluator(
    enabled=plugin_config.cs_pro_llm_enabled,
    api_type=_llm_api_type,
    api_url=_llm_api_url,
    api_key=_llm_api_key,
    model=_llm_model,
    timeout=plugin_config.cs_pro_llm_timeout,
    system_prompt=plugin_config.cs_pro_llm_system_prompt,
)

# Shared crawler instances
event_crawler = FiveEEventCrawler()
five_e_crawler = FiveECrawler()
pw_crawler = PWCrawler()


def _extract_target_qq(bot: Bot, event: MessageEvent) -> str:
    target = str(event.user_id)
    for seg in event.message:
        if seg.type == "at":
            qq = str(seg.data.get("qq") or "")
            if qq and qq != str(bot.self_id):
                target = qq
                break
    return target


def _platform_theme(platform: str) -> tuple[str, str, str]:
    if platform == "5e":
        return "5E平台", "#f74d4d", "#f78c00"
    if platform == "mm":
        return "官匹", "#2db3ff", "#3b82f6"
    return "完美平台", "#2db3ff", "#06b6d4"


def _fmt_pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _build_match_view_data(match_data, llm_title: str, llm_detail: str) -> dict:
    platform_label, color_a, color_b = _platform_theme(match_data.platform)

    def _p(p):
        return {
            "name": p.name,
            "rating": f"{p.rating:.2f}",
            "adr": f"{p.adr:.1f}",
            "kill": p.kill,
            "death": p.death,
            "hs": _fmt_pct(p.headshot_rate),
            "elo": f"{p.elo_change:+.1f}",
            "rws": f"{p.rws:.2f}",
            "uuid": p.uuid,
        }

    start_at = datetime.fromtimestamp(match_data.start_time).strftime("%Y-%m-%d %H:%M:%S")
    
    # 构造队友列表（包含自己），并按 Rating 排序
    all_teammates = [match_data.player] + match_data.teammates
    # 按 rating 降序排序
    all_teammates.sort(key=lambda x: x.rating, reverse=True)
    
    return {
        "platform_label": platform_label,
        "theme_a": color_a,
        "theme_b": color_b,
        "map_name": match_data.map_name,
        "match_type": match_data.match_type or "未知模式",
        "start_at": start_at,
        "duration_min": match_data.duration_min,
        "result_text": match_data.result_text,
        "match_id": match_data.match_id,
        "player": _p(match_data.player),
        "teammates": [_p(x) for x in all_teammates],  # 现在这里包含自己和队友，并已排序
        "opponents": [_p(x) for x in match_data.opponents],
        "llm_title": llm_title,
        "llm_detail": llm_detail,
    }


@bind_cmd.handle()
async def handle_bind(event: MessageEvent, args: Message = CommandArg()):
    raw = args.extract_plain_text().strip()
    if not raw:
        await bind_cmd.finish("用法: /bind [5e|pw] [玩家名]")

    try:
        default_platform = store.get_default_platform(str(event.user_id))
        platform, name = parse_bind_args(raw, default_platform=default_platform)

        # 5E绑定复用 /5e 查询规则：ID/域名直接绑定，昵称走 search_player 首条匹配。
        if platform == "5e":
            is_id = re.match(r"^\d+s\w+$|^\d+$|^[0-9a-f-]{36}$", name)
            if is_id:
                bound = await match_service.bind_5e_domain(store, str(event.user_id), name, canonical_name=name)
            else:
                candidates = await five_e_crawler.search_player(name)
                if not candidates:
                    await bind_cmd.finish(f"绑定失败: 未找到5E玩家 {name}")
                first = candidates[0]
                domain = str(first.get("domain") or "").strip()
                if not domain:
                    await bind_cmd.finish("绑定失败: 5E搜索结果缺少domain")
                canonical = str(first.get("name") or name)
                bound = await match_service.bind_5e_domain(store, str(event.user_id), domain, canonical_name=canonical)
        else:
            bound = await match_service.bind_player(store, str(event.user_id), platform, name)
    except Exception as e:
        await bind_cmd.finish(f"绑定失败: {e}")

    if bound.platform == "pw" and (not bound.domain or not bound.uuid):
        await bind_cmd.finish(
            f"绑定成功\n平台: {bound.platform}\n玩家: {bound.player_name}\n"
            "已按用户名绑定，将在首次查询官匹/完美战绩时自动补全平台ID与SteamID。"
        )
    await bind_cmd.finish(
        f"绑定成功\n平台: {bound.platform}\n玩家: {bound.player_name}\n平台ID: {bound.domain}\nSteamID: {bound.uuid}"
    )


@match_cmd.handle()
async def handle_match(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    raw = args.extract_plain_text().strip()
    platform, round_index = parse_match_args(raw)
    target_qq = _extract_target_qq(bot, event)

    await match_cmd.send("正在查询详细战绩并生成评价...")
    try:
        match_data = await match_service.fetch_match(store, target_qq, platform, round_index)
    except Exception as e:
        await match_cmd.finish(f"查询失败: {e}")

    llm_title = "评价暂不可用"
    llm_detail = "未配置或调用失败，本次仅展示战绩数据。"
    try:
        result = await llm.evaluate(match_data.llm_context())
        if result:
            llm_title = result.title
            llm_detail = result.detail
    except Exception as e:
        logger.warning(f"[cs_pro] llm evaluate failed: {e}")

    view_data = _build_match_view_data(match_data, llm_title, llm_detail)
    image_bytes = await render_match_detail_card(view_data)
    await match_cmd.finish(MessageSegment.image(image_bytes))


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
        await cs_search.finish("未找到相关选手，请检查名称")

    player_brief = data[0]
    hltv_id = player_brief.get("hltv_id")
    if not hltv_id:
        await cs_search.finish("未找到选手HLTV ID")

    detail_api = f"https://api.viki.moe/pw-cs/player/{hltv_id}"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(detail_api)
            player = resp.json()
        except Exception as e:
            await cs_search.finish(f"获取选手详情出错: {e}")

    try:
        image_bytes = await render_player_detail(player)
    except Exception as e:
        logger.error(f"Error rendering player detail: {e}")
        name = player.get("name", "未知")
        team_name = player.get("team", {}).get("name", "无战队")
        await cs_search.finish(f"选手: {name}\n战队: {team_name}\n(图片渲染失败)")

    await cs_search.finish(MessageSegment.image(image_bytes))


@game_search.handle()
async def handle_game_search():
    await game_search.send("正在获取实时赛程与赛事信息...")
    try:
        matches = await event_crawler.get_matches()
        if matches:
            image_bytes = await render_matches_card(matches)
            await game_search.finish(MessageSegment.image(image_bytes))

        events = await event_crawler.get_events()
        if events:
            image_bytes = await render_events_card(events)
            await game_search.finish(MessageSegment.image(image_bytes))

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
        await five_e_stats.finish("请输入5E玩家域名、ID或昵称，例如: /5e 15429443s91f72")

    await five_e_stats.send(f"正在查询 5E 玩家 {input_str}...")
    domain = input_str

    try:
        is_id = re.match(r"^\d+s\w+$|^\d+$|^[0-9a-f-]{36}$", input_str)
        search_info = {}
        if not is_id:
            search_results = await five_e_crawler.search_player(input_str)
            if not search_results:
                await five_e_stats.finish(f"未找到昵称为 {input_str} 的玩家。")
            search_info = search_results[0]
            domain = search_info["domain"]
            await five_e_stats.send(f"匹配到玩家: {search_info['name']} ({domain})，正在获取详细战绩...")

        data = await five_e_crawler.get_player_data(domain)
        if (not data.get("nickname") or data["nickname"] == "Unknown") and search_info.get("name"):
            data["nickname"] = search_info["name"]
        if (not data.get("avatar")) and search_info.get("avatar"):
            data["avatar"] = search_info["avatar"]

        if not data.get("stats") or not data["stats"].get("career"):
            await five_e_stats.finish(f"未找到玩家 {domain} 的有效战绩数据。")

        image_bytes = await render_stats_card(data)
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
        await pw_login.finish("请输入手机号和验证码，例如: /pwlogin 13800138000 123456")

    mobile, code = args
    await pw_login.send("正在尝试登录完美平台...")

    result = await pw_crawler.login(mobile, code)
    if "error" in result:
        await pw_login.finish(f"登录失败: {result['error']}")

    nickname = result.get("nickname", "未知")
    await pw_login.finish(f"登录成功，欢迎回来，{nickname}。Session 已更新。")


@pw_stats.handle()
async def handle_pw_stats(arg: Message = CommandArg()):
    input_str = arg.extract_plain_text().strip()
    if not input_str:
        await pw_stats.finish("请输入完美平台玩家昵称或 SteamId，例如: /pw sh1ro")

    await pw_stats.send(f"正在查询完美玩家 {input_str}...")

    try:
        is_steam_id = input_str.isdigit() and len(input_str) > 10
        target_steam_id = input_str
        search_info = {}

        if not is_steam_id:
            search_results = await pw_crawler.search_player(input_str)
            if not search_results:
                await pw_stats.finish(f"未找到昵称为 {input_str} 的玩家。")
            search_info = search_results[0]
            target_steam_id = str(search_info["steamId"])
            await pw_stats.send(f"匹配到玩家: {search_info.get('pvpNickName', '未知')}，正在获取详细战绩...")

        data = await pw_crawler.get_player_data(target_steam_id)
        if "error" in data:
            await pw_stats.finish(f"查询完美战绩失败: {data['error']}")
        if not data or not data.get("stats"):
            await pw_stats.finish(f"未找到玩家 {target_steam_id} 的有效战绩数据。")

        if not data.get("summary", {}).get("nickname"):
            data["summary"]["nickname"] = search_info.get("pvpNickName", "Unknown")
        if not data.get("summary", {}).get("avatarUrl"):
            data["summary"]["avatarUrl"] = search_info.get("pvpAvatar")

        image_bytes = await render_pw_stats_card(data)
        await pw_stats.finish(MessageSegment.image(image_bytes))
    except (FinishedException, MatcherException):
        raise
    except Exception as e:
        logger.error(f"Error in pw_stats: {e}")
        await pw_stats.finish(f"完美战绩查询失败: {str(e)}")

