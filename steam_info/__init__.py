import httpx
import json
import asyncio
from typing import List, Dict, Any, Optional
from nonebot import on_command, logger, get_plugin_config
from nonebot.params import CommandArg
from nonebot.adapters.onebot.v11 import Message, MessageSegment, Bot, MessageEvent
from nonebot.plugin import PluginMetadata
from nonebot.exception import FinishedException
from openai import AsyncOpenAI
from .config import Config
from pathlib import Path
from nonebot_plugin_htmlrender import template_to_pic

__plugin_metadata__ = PluginMetadata(
    name="Steam信息",
    description="获取Steam用户状态、最近游戏、游戏库信息并渲染显示",
    usage="steam 状态 <ID/ID64/别名>\nsteam 最近 <ID/ID64/别名>\nsteam 游戏 <ID/ID64/别名>\nsteam 绑定 <ID/ID64> [别名]",
    config=Config,
)

config = get_plugin_config(Config)

# 绑定数据管理
BIND_PATH = config.steam_bind_path
BIND_PATH.parent.mkdir(parents=True, exist_ok=True)

def load_binds() -> Dict[str, Dict[str, str]]:
    if BIND_PATH.exists():
        try:
            return json.loads(BIND_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"加载 Steam 绑定数据失败: {e}")
    return {"users": {}, "aliases": {}}

def save_binds(binds: Dict[str, Dict[str, str]]):
    try:
        BIND_PATH.write_text(json.dumps(binds, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"保存 Steam 绑定数据失败: {e}")

bind_data = load_binds()

def get_steam_id(user_id: str, input_str: str) -> str:
    """获取实际的 Steam ID。输入可以是 ID、别名，或者为空（使用绑定 ID）"""
    input_str = input_str.strip()
    
    # 1. 如果输入为空，尝试获取该用户的绑定 ID
    if not input_str:
        return bind_data["users"].get(user_id, "")
    
    # 2. 检查是否是已定义的别名
    if input_str in bind_data["aliases"]:
        return bind_data["aliases"][input_str]
    
    # 3. 否则认为是直接输入的 ID
    return input_str

async def get_ai_review(user_name: str, games: List[Dict]) -> str:
    """使用 AI 锐评游戏库"""
    # 获取 personification 插件的配置，复用 API Key 和 Model
    try:
        from ..personification.config import Config as PersonificationConfig
        ai_config = get_plugin_config(PersonificationConfig)
        api_key = ai_config.personification_api_key
        api_url = ai_config.personification_api_url
        model = ai_config.personification_model
    except Exception:
        return "（未配置 AI 接口，无法生成锐评）"

    if not api_key:
        return "（未配置 AI 接口，无法生成锐评）"

    # 提取前 50 个游戏信息
    game_list = []
    for game in games[:50]:
        game_list.append(f"{game['name']} (时长: {game['playtime']['total_desc']})")
    
    games_str = "\n".join(game_list)
    
    system_prompt = (
        "你是一个资深的 Steam 玩家，也是一个毒舌但幽默的评价者。\n"
        "请根据提供的用户游戏库清单（游戏名及游玩时长），为该用户写一段深度的“锐评”。\n"
        "要求：\n"
        "1. 语气要有特色，可以吐槽其品味、肝度或各种奇葩的游戏选择，也可以分析其偏好的游戏类型。\n"
        "2. 提到多个具体的游戏名，并根据时长分析玩家的行为（例如：是云玩家、全成就狂魔还是某个系列的死忠粉）。\n"
        "3. 字数要求在 300-500 字左右，要有一定的逻辑性和深度，不仅仅是简单的吐槽。\n"
        "4. 直接输出锐评内容，不要有任何前缀或后缀。"
    )
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as http_client:
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=api_url.rstrip("/") + "/v1",
                http_client=http_client
            )
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"玩家 {user_name} 的游戏库清单：\n{games_str}"}
                ],
                max_tokens=1000,
                temperature=0.8
            )
            return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"AI 锐评生成失败: {e}")
        return "（锐评生成失败，可能是 AI 接口异常）"

logger.info(f"Steam插件已加载，当前 Key 长度: {len(config.steam_api_key)}")

STEAM_BASE_URL = "https://api.viki.moe/steam"
TEMPLATES_PATH = Path(__file__).parent / "templates"

async def get_steam_data(endpoint: str, key: str) -> Optional[Any]:
    url = f"{STEAM_BASE_URL}/{endpoint}"
    params = {"key": key} if key else {}
    logger.debug(f"正在请求 Steam API: {url} (Key长度: {len(key) if key else 0})")
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 403:
                logger.error(f"Steam API 访问被拒绝 (403): 请检查 API Key 是否正确，或该用户是否设置了隐私限制。")
                return None
            else:
                logger.error(f"Steam API 请求失败: {resp.status_code} - {resp.text}")
                return None
        except Exception as e:
            logger.error(f"Steam API 请求异常: {e}")
            return None

steam = on_command("steam", aliases={"steam状态", "steam最近", "steam游戏"}, priority=5, block=True)

@steam.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    arg_str = args.extract_plain_text().strip()
    user_qq = str(event.user_id)
    
    if not arg_str:
        await steam.finish("使用方法:\nsteam 状态 <ID/别名>\nsteam 最近 <ID/别名>\nsteam 游戏 <ID/别名>\nsteam 绑定 <ID> [别名]")
    
    parts = arg_str.split(maxsplit=2)
    subcommand = parts[0]
    
    if subcommand == "绑定":
        if len(parts) < 2:
            await steam.finish("使用方法: steam 绑定 <SteamID/ID64> [别名]")
        
        steam_id = parts[1]
        alias = parts[2] if len(parts) > 2 else ""
        
        bind_data["users"][user_qq] = steam_id
        if alias:
            bind_data["aliases"][alias] = steam_id
        
        save_binds(bind_data)
        msg = f"绑定成功！您的 Steam ID 已设为 {steam_id}"
        if alias:
            msg += f"，别名已设为 {alias}"
        await steam.finish(msg)

    # 处理查询命令
    target_id = ""
    if subcommand in ["状态", "最近", "游戏"]:
        input_val = parts[1] if len(parts) > 1 else ""
        target_id = get_steam_id(user_qq, input_val)
        if not target_id:
            await steam.finish(f"请输入 Steam ID/别名，或先使用 'steam 绑定 <ID>' 绑定您的账号")
    else:
        # 兼容旧格式或直接输入 ID/别名
        target_id = get_steam_id(user_qq, arg_str)
        subcommand = "状态" # 默认查询状态

    if subcommand == "状态":
        await steam.send("正在获取Steam状态信息...")
        data = await get_steam_data(target_id, config.steam_api_key)
        if not data:
            await steam.finish("获取Steam状态失败，请检查ID是否正确或稍后重试。")
        
        try:
            pic = await template_to_pic(
                template_path=str(TEMPLATES_PATH),
                template_name="index.html",
                templates={"user": data, "mode": "status"},
                pages={
                    "viewport": {"width": 700, "height": 350},
                    "base_url": TEMPLATES_PATH.as_uri()
                }
            )
            await steam.finish(MessageSegment.image(pic))
        except FinishedException:
            raise
        except Exception as e:
            logger.error(f"渲染Steam状态图片失败: {e}")
            status_text = f"🎮 Steam 状态: {data.get('persona_name')}\n"
            status_text += f"状态: {data.get('persona_state_desc')}\n"
            if data.get('game_info'):
                game_name = data['game_info'].get('game_name') or data['game_info'].get('name') or "未知游戏"
                status_text += f"正在玩: {game_name}\n"
            status_text += f"个人链接: {data.get('profile_url')}"
            await steam.finish(status_text)

    elif subcommand == "最近":
        await steam.send("正在获取最近游戏信息...")
        # 获取基础信息
        status_data = await get_steam_data(target_id, config.steam_api_key)
        if not status_data:
            await steam.finish("获取Steam状态失败，请检查ID是否正确。")
            
        data = await get_steam_data(f"{target_id}/recently-played", config.steam_api_key)
        if not data or not isinstance(data, list):
            await steam.finish("获取最近游戏信息失败，请检查该用户是否公开了游戏信息。")
        
        try:
            pic = await template_to_pic(
                template_path=str(TEMPLATES_PATH),
                template_name="index.html",
                templates={"games": data, "user": status_data, "mode": "recent"},
                pages={
                    "viewport": {"width": 950, "height": 1200},
                    "base_url": TEMPLATES_PATH.as_uri()
                }
            )
            await steam.finish(MessageSegment.image(pic))
        except FinishedException:
            raise
        except Exception as e:
            logger.error(f"渲染最近游戏图片失败: {e}")
            msg = f"🎮 {status_data.get('persona_name')} 最近玩过的游戏：\n"
            for game in data[:5]:
                msg += f"- {game['name']} ({game['playtime']['recent_desc']})\n"
            await steam.finish(msg)

    elif subcommand == "游戏":
        await steam.send("正在获取游戏库信息并生成 AI 锐评...")
        # 获取基础信息
        status_data = await get_steam_data(target_id, config.steam_api_key)
        if not status_data:
            await steam.finish("获取Steam状态失败，请检查ID是否正确。")

        data = await get_steam_data(f"{target_id}/games", config.steam_api_key)
        if not data or not isinstance(data, list):
            await steam.finish("获取游戏库信息失败，请检查该用户是否公开了库信息。")
        
        # 并发获取 AI 锐评
        ai_review_task = asyncio.create_task(get_ai_review(status_data.get("persona_name", target_id), data))
        ai_review = await ai_review_task
        
        # 动态计算高度：基础高度(200) + AI锐评预估高度(按字数估算，约每100字150px) + 游戏列表高度(每行3个，每行60px)
        ai_char_count = len(ai_review)
        estimated_ai_height = (ai_char_count // 50 + 1) * 30 + 100 # 估算行数 * 行高 + 边距
        estimated_library_height = (len(data) // 3 + 1) * 60 + 150
        render_height = 200 + estimated_ai_height + estimated_library_height
        # 限制在 1000 到 15000 之间
        render_height = max(1000, min(15000, render_height))

        try:
            pic = await template_to_pic(
                template_path=str(TEMPLATES_PATH),
                template_name="index.html",
                templates={"games": data, "user": status_data, "mode": "games", "ai_review": ai_review},
                pages={
                    "viewport": {"width": 950, "height": render_height},
                    "base_url": TEMPLATES_PATH.as_uri()
                }
            )
            await steam.finish(MessageSegment.image(pic))
        except FinishedException:
            raise
        except Exception as e:
            logger.error(f"渲染游戏库图片失败: {e}")
            msg = f"🎮 {status_data.get('persona_name')} 的游戏库 (前10个)：\n"
            for game in data[:10]:
                msg += f"- {game['name']} (总时长: {game['playtime']['total_desc']})\n"
            await steam.finish(msg)
    
    else:
        await steam.finish("未知子命令。使用方法:\nsteam 状态 <ID>\nsteam 最近 <ID>\nsteam 游戏 <ID>\nsteam 绑定 <ID> [别名]")
