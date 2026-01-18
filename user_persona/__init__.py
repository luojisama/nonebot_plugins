import json
import os
import time
import asyncio
from typing import Dict, List, Optional
from pathlib import Path

from nonebot import on_message, on_command, get_plugin_config, logger, get_driver
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment, MessageEvent
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from openai import AsyncOpenAI
import httpx

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="用户画像",
    description="记录用户聊天记录并生成用户画像",
    usage="查看画像 [@用户/QQ号]\n刷新画像 [@用户/QQ号]",
    config=Config,
)

plugin_config = get_plugin_config(Config)
driver_config = get_driver().config

# 尝试加载拟人插件的配置作为默认值
try:
    from ..personification.config import Config as PersonConfig
    person_config = get_plugin_config(PersonConfig)
except Exception:
    person_config = None

# 数据存储路径
data_path = Path(plugin_config.user_persona_data_path)
data_path.parent.mkdir(parents=True, exist_ok=True)

# 内存中的数据缓存
# 格式: { "histories": { "user_id": [msg1, msg2, ...] }, "personas": { "user_id": { "data": "...", "time": 123 } } }
user_data: Dict = {"histories": {}, "personas": {}}

def load_data():
    global user_data
    if data_path.exists():
        try:
            with open(data_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                user_data["histories"] = loaded.get("histories", {})
                user_data["personas"] = loaded.get("personas", {})
        except Exception as e:
            logger.error(f"加载画像数据失败: {e}")

def save_data():
    try:
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump(user_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存画像数据失败: {e}")

load_data()

# AI 调用函数
async def call_ai_persona(messages: List[str]) -> Optional[str]:
    api_key = plugin_config.user_persona_api_key or (person_config.personification_api_key if person_config else None)
    api_url = plugin_config.user_persona_api_url or (person_config.personification_api_url if person_config else "https://api.openai.com/v1")
    model = plugin_config.user_persona_model or (person_config.personification_model if person_config else "gpt-4o-mini")

    if not api_key:
        logger.warning("用户画像插件：未配置 API Key，且无法从拟人插件获取备选 Key")
        return None

    if not api_url.endswith(("/v1", "/v1/")):
        api_url = api_url.rstrip("/") + "/v1"

    prompt = (
        "你是一个专业的人格分析师和用户画像专家。\n"
        "请根据以下用户最近的 30 条聊天记录，分析该用户的特征。\n"
        "要求输出格式严格如下：\n"
        "【职业推测】：...\n"
        "【年龄推测】：...\n"
        "【性别推测】：...\n"
        "【人物描述】：（此处要求 150-200 字左右，详细描述性格、语言风格、兴趣爱好等特征）\n\n"
        "用户聊天记录如下：\n" + "\n".join([f"- {m}" for m in messages])
    )

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as http_client:
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=api_url,
                http_client=http_client
            )
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7
            )
            return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"画像生成 AI 调用失败: {e}")
        return None

# 消息记录器
message_recorder = on_message(priority=99, block=False)

@message_recorder.handle()
async def handle_message(event: MessageEvent):
    user_id = str(event.user_id)
    content = event.get_plaintext().strip()
    
    if not content:
        return

    # 简单过滤命令
    command_starts = getattr(driver_config, "command_start", {"/", ""})
    if any(content.startswith(start) for start in command_starts if start):
        return

    if user_id not in user_data["histories"]:
        user_data["histories"][user_id] = []
    
    # 记录消息
    user_data["histories"][user_id].append(content)
    
    # 检查是否达到上限
    if len(user_data["histories"][user_id]) >= plugin_config.user_persona_history_max:
        # 触发异步生成
        history = user_data["histories"][user_id].copy()
        # 清空记录
        user_data["histories"][user_id] = []
        save_data()
        
        logger.info(f"用户 {user_id} 消息达到 {plugin_config.user_persona_history_max} 条，开始生成画像...")
        asyncio.create_task(trigger_generation(user_id, history))
    else:
        save_data()

async def trigger_generation(user_id: str, history: List[str]):
    persona_text = await call_ai_persona(history)
    if persona_text:
        user_data["personas"][user_id] = {
            "data": persona_text,
            "time": int(time.time())
        }
        save_data()
        logger.info(f"用户 {user_id} 画像生成成功")
    else:
        logger.error(f"用户 {user_id} 画像生成失败")

# 命令处理器
view_persona_cmd = on_command("查看画像", priority=5, block=True)
refresh_persona_cmd = on_command("刷新画像", priority=5, block=True)

@view_persona_cmd.handle()
async def handle_view_persona(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    target_id = ""
    # 1. 检查 at
    for seg in event.get_message():
        if seg.type == "at":
            target_id = str(seg.data["qq"])
            break
    # 2. 检查参数
    if not target_id:
        arg_text = args.extract_plain_text().strip()
        if arg_text.isdigit():
            target_id = arg_text
    # 3. 默认自己
    if not target_id:
        target_id = str(event.user_id)
        
    if target_id not in user_data["personas"]:
        # 检查是否有正在记录的历史
        count = len(user_data["histories"].get(target_id, []))
        await view_persona_cmd.finish(f"该用户暂无画像。当前已记录 {count}/{plugin_config.user_persona_history_max} 条消息。")
    
    persona = user_data["personas"][target_id]
    update_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(persona["time"]))
    
    msg = f"用户 {target_id} 的画像分析 (更新时间: {update_time})：\n\n{persona['data']}"
    await view_persona_cmd.finish(msg)

@refresh_persona_cmd.handle()
async def handle_refresh_persona(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    target_id = ""
    # 1. 检查 at
    for seg in event.get_message():
        if seg.type == "at":
            target_id = str(seg.data["qq"])
            break
    # 2. 检查参数
    if not target_id:
        arg_text = args.extract_plain_text().strip()
        if arg_text.isdigit():
            target_id = arg_text
    # 3. 默认自己
    if not target_id:
        target_id = str(event.user_id)

    history = user_data["histories"].get(target_id, [])
    if not history:
        await refresh_persona_cmd.finish("当前没有任何聊天记录，无法刷新画像。")
    
    await refresh_persona_cmd.send(f"正在根据当前 {len(history)} 条记录生成画像，请稍候...")
    
    # 强制生成并清空
    persona_text = await call_ai_persona(history)
    if persona_text:
        user_data["personas"][target_id] = {
            "data": persona_text,
            "time": int(time.time())
        }
        user_data["histories"][target_id] = []
        save_data()
        await refresh_persona_cmd.finish(f"画像刷新成功！\n\n{persona_text}")
    else:
        await refresh_persona_cmd.finish("画像刷新失败，请检查 API 配置或网络。")
