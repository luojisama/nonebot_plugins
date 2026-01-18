import httpx
import hashlib
import json
from bs4 import BeautifulSoup
from pathlib import Path
from nonebot import on_command, require, logger, get_bots
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11 import Message, MessageSegment, Bot, GroupMessageEvent
from nonebot.adapters.onebot.v11.permission import GROUP_ADMIN, GROUP_OWNER
from nonebot.permission import SUPERUSER
from nonebot.exception import FinishedException
from datetime import datetime

# 注册插件元数据
__plugin_meta__ = PluginMetadata(
    name="LoveLive日程爬虫",
    description="定时爬取 ll-ch.com 的 LoveLive 活动日程并生成精美卡片",
    usage="使用命令：ll日程, ll访华, ll开启推送, ll关闭推送",
)

# 导入必要插件
require("nonebot_plugin_apscheduler")
require("nonebot_plugin_htmlrender")
from nonebot_plugin_apscheduler import scheduler
from nonebot_plugin_htmlrender import html_to_pic

# 路径定义
TEMPLATES_PATH = Path(__file__).parent / "templates"
DATA_PATH = Path(__file__).parent / "data" / "config.json"

# 目标网址
TARGET_URL = "https://ll-ch.com/"
CV_TO_CHINA_URL = "https://ll-ch.com/main/cvtochina/"

# 存储解析后的日程数据
cached_schedules = []
cached_cv_schedules = []
last_data_hash = ""  # 用于检测数据更新
last_cv_hash = ""    # 用于检测访华更新

# 配置管理
def load_config() -> dict:
    if not DATA_PATH.exists():
        DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        save_config({"whitelist": []})
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"whitelist": []}

def save_config(config: dict):
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)

def is_group_whitelisted(group_id: int) -> bool:
    config = load_config()
    return group_id in config.get("whitelist", [])

async def render_schedule_card(schedules: list, limit: int = 5) -> bytes:
    """渲染日程卡片"""
    template_path = TEMPLATES_PATH / "schedule_card.html"
    with open(template_path, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    items_html = ""
    for schedule in schedules[:limit]:  # 根据 limit 参数显示条数
        lines = [line.strip() for line in schedule.split("\n") if line.strip()]
        if not lines: continue
        
        title = lines[0]
        details = "\n".join(lines[1:])
        
        items_html += f'''
        <div class="event-card">
            <div class="tag">Live / Event</div>
            <div class="event-title">{title}</div>
            <div class="event-detail">{details}</div>
        </div>
        '''
    
    update_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    html_content = html_content.replace("{update_time}", update_time)
    html_content = html_content.replace("{items_html}", items_html)
    
    return await html_to_pic(html_content, viewport={"width": 500, "height": 10}) # height会自动增长

async def fetch_ll_schedule() -> tuple[list, bool]:
    """获取并解析 LoveLive 日程信息，返回 (日程列表, 是否有更新)"""
    global cached_schedules, last_data_hash
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
            response = await client.get(TARGET_URL, headers=headers)
            response.raise_for_status()
            html_content = response.text
            
        soup = BeautifulSoup(html_content, "html.parser")
        new_schedules = []
        
        items = soup.find_all("div", class_="cd-timeline-content")
        if not items:
            items = soup.find_all(["div", "section"], class_=["timeline-content", "event-item", "cd-timeline-block"])
        if not items:
            items = soup.find_all("table")
            
        for item in items:
            text = item.get_text(separator="\n", strip=True)
            keywords = ["ラブライブ", "LoveLive", "Liella", "虹ヶ咲", "蓮ノ空", "Aqours", "μ's", "いきづらい部"]
            if any(kw in text for kw in keywords):
                if "本站功能定位" in text: continue
                if len(text) > 30:
                    lines = [line.strip() for line in text.split("\n") if line.strip()]
                    clean_text = "\n".join(lines)
                    if clean_text not in new_schedules:
                        new_schedules.append(clean_text)
        
        # 检测更新
        current_hash = hashlib.md5("".join(new_schedules).encode()).hexdigest()
        is_updated = False
        
        if new_schedules and current_hash != last_data_hash:
            is_updated = True if last_data_hash else False # 第一次加载不触发更新推送
            last_data_hash = current_hash
            cached_schedules = new_schedules
            logger.info(f"LoveLive schedule updated. Found {len(new_schedules)} events.")
            
        return new_schedules, is_updated

    except Exception as e:
        logger.error(f"Error fetching LoveLive schedule: {e}")
        return None, False

async def fetch_cv_to_china() -> tuple[list, bool]:
    """获取并解析声优访华日程信息"""
    global cached_cv_schedules, last_cv_hash
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        async with httpx.AsyncClient(timeout=20.0, verify=False) as client:
            response = await client.get(CV_TO_CHINA_URL, headers=headers)
            response.raise_for_status()
            html_content = response.text
            
        soup = BeautifulSoup(html_content, "html.parser")
        new_cv_schedules = []
        
        # 查找表格
        table = soup.find("table")
        if table:
            rows = table.find_all("tr")[1:]  # 跳过表头
            for row in rows:
                cols = row.find_all("td")
                if len(cols) >= 4:
                    date = cols[0].get_text(strip=True)
                    name = cols[1].get_text(strip=True)
                    time = cols[2].get_text(strip=True)
                    location = cols[3].get_text(strip=True)
                    
                    event_text = f"【访华】{name}\n日期：{date}\n时间：{time}\n地点：{location}"
                    new_cv_schedules.append(event_text)
        
        # 检测更新
        current_hash = hashlib.md5("".join(new_cv_schedules).encode()).hexdigest()
        is_updated = False
        
        if new_cv_schedules and current_hash != last_cv_hash:
            is_updated = True if last_cv_hash else False
            last_cv_hash = current_hash
            cached_cv_schedules = new_cv_schedules
            logger.info(f"CV to China schedule updated. Found {len(new_cv_schedules)} events.")
            
        return new_cv_schedules, is_updated

    except Exception as e:
        logger.error(f"Error fetching CV to China schedule: {e}")
        return None, False

# 定时任务：每小时执行一次
@scheduler.scheduled_job("cron", hour="*", minute="0", id="fetch_ll_schedule_task")
async def scheduled_fetch():
    logger.info("Starting hourly LoveLive schedule update...")
    data, is_updated = await fetch_ll_schedule()
    cv_data, cv_is_updated = await fetch_cv_to_china()
    
    config = load_config()
    whitelist = config.get("whitelist", [])
    
    if not whitelist:
        logger.info("No whitelisted groups. Skipping push.")
        return

    # 合并推送逻辑
    for bot in get_bots().values():
        if not isinstance(bot, Bot): continue
        
        try:
            # 仅推送至白名单内的群组
            for group_id in whitelist:
                # 推送普通日程
                if is_updated and data:
                    pic = await render_schedule_card(data, limit=5)
                    msg = "✨ 检测到 LoveLive! 日程有更新！\n" + MessageSegment.image(pic)
                    await bot.send_group_msg(group_id=group_id, message=msg)
                
                # 推送访华日程
                if cv_is_updated and cv_data:
                    pic = await render_schedule_card(cv_data, limit=5)
                    msg = "🇨🇳 检测到声优访华日程有更新！\n" + MessageSegment.image(pic)
                    await bot.send_group_msg(group_id=group_id, message=msg)
                    
        except Exception as e:
            logger.error(f"Push error for bot {bot.self_id}: {e}")

# 手动查询命令
ll_schedule_cmd = on_command("ll日程", aliases={"lovelive日程", "ll日程表"}, priority=5, block=True)
ll_all_schedule_cmd = on_command("ll全部日程", aliases={"ll日程全部", "lovelive全部日程"}, priority=5, block=True)
ll_cv_china_cmd = on_command("ll访华", aliases={"声优访华", "ll访华日程"}, priority=5, block=True)

# 管理命令
ll_enable_cmd = on_command("ll开启推送", aliases={"ll启用推送", "ll加入白名单"}, permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER, priority=5, block=True)
ll_disable_cmd = on_command("ll关闭推送", aliases={"ll停用推送", "ll退出白名单"}, permission=GROUP_ADMIN | GROUP_OWNER | SUPERUSER, priority=5, block=True)

@ll_enable_cmd.handle()
async def handle_ll_enable(event: GroupMessageEvent):
    group_id = event.group_id
    config = load_config()
    if group_id not in config["whitelist"]:
        config["whitelist"].append(group_id)
        save_config(config)
        await ll_enable_cmd.finish(f"✅ 已开启本群 LoveLive! 日程推送功能。")
    else:
        await ll_enable_cmd.finish(f"ℹ️ 本群已在推送白名单中。")

@ll_disable_cmd.handle()
async def handle_ll_disable(event: GroupMessageEvent):
    group_id = event.group_id
    config = load_config()
    if group_id in config["whitelist"]:
        config["whitelist"].remove(group_id)
        save_config(config)
        await ll_disable_cmd.finish(f"📴 已关闭本群 LoveLive! 日程推送功能。")
    else:
        await ll_disable_cmd.finish(f"ℹ️ 本群未开启推送功能。")

@ll_schedule_cmd.handle()
async def handle_ll_schedule(event: GroupMessageEvent):
    if not is_group_whitelisted(event.group_id):
        return
    await process_schedule_request(ll_schedule_cmd, limit=5, source="main")

@ll_all_schedule_cmd.handle()
async def handle_ll_all_schedule(event: GroupMessageEvent):
    if not is_group_whitelisted(event.group_id):
        return
    await process_schedule_request(ll_all_schedule_cmd, limit=20, source="main")

@ll_cv_china_cmd.handle()
async def handle_ll_cv_china(event: GroupMessageEvent):
    if not is_group_whitelisted(event.group_id):
        return
    await process_schedule_request(ll_cv_china_cmd, limit=10, source="cv")

async def process_schedule_request(matcher, limit: int, source: str = "main"):
    global cached_schedules, cached_cv_schedules
    
    target_cache = cached_schedules if source == "main" else cached_cv_schedules
    fetch_func = fetch_ll_schedule if source == "main" else fetch_cv_to_china
    source_name = "LoveLive! 日程" if source == "main" else "声优访华"
    
    if not target_cache:
        await matcher.send(f"正在获取最新{source_name}信息，请稍候...")
        data, _ = await fetch_func()
        if data is None:
            await matcher.finish(f"获取{source_name}失败（网络错误或超时），请稍后再试。")
        if not data:
            await matcher.finish(f"当前没有查询到{source_name}相关信息哦。")
        target_cache = data
    
    # 渲染卡片
    try:
        pic = await render_schedule_card(target_cache, limit=limit)
        await matcher.finish(MessageSegment.image(pic))
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"Render error: {e}")
        # 降级到文本模式
        msg = f"📅 {source_name} (文本模式 - 前{limit}条)\n"
        for i, s in enumerate(target_cache[:limit], 1):
            msg += f"{i}. {s[:100]}...\n"
        await matcher.finish(msg + f"\n渲染失败，请稍后再试。")

