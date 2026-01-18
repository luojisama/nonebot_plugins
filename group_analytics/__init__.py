import aiosqlite
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Any

from nonebot import on_message, on_command, logger, require, get_driver
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment, Message
from nonebot.plugin import PluginMetadata
from nonebot.exception import FinishedException

require("nonebot_plugin_htmlrender")
from nonebot_plugin_htmlrender import md_to_pic

__plugin_meta__ = PluginMetadata(
    name="群活跃报告",
    description="统计群聊活跃度并生成可视化报告",
    usage="/活跃报告 [今日/本周]",
)

# 数据库路径
DB_PATH = Path("data/analytics.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# --- 数据库操作 ---

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS message_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER,
                user_id INTEGER,
                nickname TEXT,
                timestamp INTEGER
            )
        """)
        await db.commit()

@get_driver().on_startup
async def _init():
    await init_db()

async def log_message(group_id: int, user_id: int, nickname: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO message_log (group_id, user_id, nickname, timestamp) VALUES (?, ?, ?, ?)",
            (group_id, user_id, nickname, int(time.time()))
        )
        await db.commit()

async def get_stats(group_id: int, days: int = 1) -> List[tuple]:
    start_time = int(time.time()) - (days * 24 * 3600)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            SELECT user_id, nickname, COUNT(*) as msg_count 
            FROM message_log 
            WHERE group_id = ? AND timestamp > ?
            GROUP BY user_id 
            ORDER BY msg_count DESC 
            LIMIT 10
        """, (group_id, start_time))
        return await cursor.fetchall()

# --- 处理器 ---

msg_monitor = on_message(priority=10, block=False)

@msg_monitor.handle()
async def handle_msg(event: GroupMessageEvent):
    await log_message(event.group_id, event.user_id, event.sender.nickname or str(event.user_id))

stats_cmd = on_command("活跃报告", aliases={"水群榜", "活跃榜"}, priority=5, block=True)

async def get_stats_from_napcat(bot: Bot, group_id: int, days: int = 1) -> List[tuple]:
    """尝试从 NapCat/Go-CQHTTP 的历史记录接口获取数据"""
    try:
        # 获取合并后的消息列表（NapCat/Go-CQHTTP 扩展 API）
        # 注意：并不是所有 OneBot 实现都支持 get_group_msg_history
        history = await bot.call_api("get_group_msg_history", group_id=group_id)
        
        if not history or "messages" not in history:
            return []
            
        messages = history["messages"]
        start_time = int(time.time()) - (days * 24 * 3600)
        
        user_counts = {}
        user_names = {}
        
        for msg in messages:
            ts = msg.get("time", 0)
            if ts < start_time:
                continue
                
            uid = msg.get("user_id")
            nickname = msg.get("sender", {}).get("nickname", str(uid))
            
            user_counts[uid] = user_counts.get(uid, 0) + 1
            user_names[uid] = nickname
            
        # 排序并转为元组列表
        sorted_users = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)
        return [(uid, user_names[uid], count) for uid, count in sorted_users[:10]]
        
    except Exception as e:
        logger.warning(f"从 NapCat 接口获取历史记录失败: {e}")
        return []

@stats_cmd.handle()
async def handle_stats(bot: Bot, event: GroupMessageEvent):
    args = event.get_plaintext().strip().split()
    days = 1
    period_text = "今日"
    
    if "本周" in args or "周" in args:
        days = 7
        period_text = "本周"
    
    # 1. 优先尝试从接口获取（NapCat 漫游消息）
    stats = await get_stats_from_napcat(bot, event.group_id, days)
    
    # 2. 如果接口不支持或没数据，回退到本地数据库统计
    if not stats:
        logger.info("NapCat 接口未返回数据，回退至本地数据库统计")
        stats = await get_stats(event.group_id, days)
    
    if not stats:
        await stats_cmd.finish(f"暂无{period_text}活跃数据（接口与本地均无记录）")
        
    # 构建渲染用的 Markdown
    md = f"# 📊 {period_text}群活跃报告\n\n"
    md += f"**群号：** {event.group_id}\n\n"
    md += "| 排名 | 昵称 (QQ) | 发言数 |\n"
    md += "| :--- | :--- | :--- |\n"
    
    for i, (user_id, nickname, count) in enumerate(stats, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        md += f"| {medal} | {nickname} ({user_id}) | **{count}** |\n"
    
    md += "\n---\n"
    
    # 构建完全本地的 HTML，不依赖任何外部 CDN 资源
    max_count = stats[0][2] if stats else 1
    
    full_html = f"""
    <html>
    <head>
        <meta charset="UTF-8">
        <style>
            body {{ 
                font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; 
                background-color: #f1f5f9; 
                padding: 30px; 
                margin: 0;
                display: flex;
                justify-content: center;
            }}
            .card {{ 
                background: white; 
                border-radius: 20px; 
                box-shadow: 0 10px 25px -5px rgba(0,0,0,0.1); 
                padding: 30px; 
                width: 550px;
                border: 1px solid #e2e8f0; 
            }}
            .header {{ text-align: center; margin-bottom: 30px; }}
            .title {{ font-size: 26px; font-weight: bold; color: #1e293b; margin-bottom: 8px; }}
            .subtitle {{ color: #64748b; font-size: 15px; }}
            
            .stats-container {{ display: flex; flex-direction: column; gap: 20px; }}
            .user-row {{ display: flex; flex-direction: column; gap: 8px; }}
            .user-meta {{ display: flex; justify-content: space-between; align-items: flex-end; }}
            .user-info {{ display: flex; align-items: center; gap: 10px; }}
            .rank-tag {{ 
                width: 28px; height: 28px; display: flex; align-items: center; justify-content: center;
                border-radius: 8px; font-size: 14px; font-weight: bold;
            }}
            .rank-1 {{ background: #fef3c7; color: #92400e; }}
            .rank-2 {{ background: #f1f5f9; color: #475569; }}
            .rank-3 {{ background: #ffedd5; color: #9a3412; }}
            .rank-other {{ color: #94a3b8; }}
            
            .name-box {{ display: flex; flex-direction: column; }}
            .nickname {{ font-size: 16px; font-weight: 600; color: #1e293b; }}
            .user-id {{ font-size: 12px; color: #94a3b8; }}
            
            .msg-count {{ font-size: 18px; font-weight: 800; color: #2563eb; }}
            .msg-unit {{ font-size: 12px; color: #64748b; font-weight: normal; margin-left: 2px; }}
            
            .bar-bg {{ width: 100%; height: 12px; background: #f1f5f9; border-radius: 6px; overflow: hidden; }}
            .bar-fill {{ 
                height: 100%; background: linear-gradient(90deg, #3b82f6, #2563eb); 
                border-radius: 6px; transition: width 0.3s ease;
            }}
            
            .footer {{ 
                margin-top: 40px; padding-top: 20px; border-top: 1px solid #f1f5f9;
                text-align: center; color: #94a3b8; font-size: 12px; 
            }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="header">
                <div class="title">📊 {period_text}群活跃报告</div>
                <div class="subtitle">群号: {event.group_id}</div>
            </div>
            
            <div class="stats-container">
                {"".join([f'''
                <div class="user-row">
                    <div class="user-meta">
                        <div class="user-info">
                            <div class="rank-tag {"rank-1" if i==0 else "rank-2" if i==1 else "rank-3" if i==2 else "rank-other"}">
                                { "🥇" if i==0 else "🥈" if i==1 else "🥉" if i==2 else i+1 }
                            </div>
                            <div class="name-box">
                                <span class="nickname">{s[1]}</span>
                                <span class="user-id">{s[0]}</span>
                            </div>
                        </div>
                        <div class="msg-count">{s[2]}<span class="msg-unit">条</span></div>
                    </div>
                    <div class="bar-bg">
                        <div class="bar-fill" style="width: {max(5, (s[2]/max_count)*100)}%;"></div>
                    </div>
                </div>
                ''' for i, s in enumerate(stats)])}
            </div>

            <div class="footer">
                报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            </div>
        </div>
    </body>
    </html>
    """
    
    try:
        from nonebot_plugin_htmlrender import get_new_page
        
        async with get_new_page(viewport={"width": 600, "height": 1000}) as page:
            # 完全本地内容，使用 domcontentloaded 即可，完全不联网
            await page.set_content(full_html, wait_until="domcontentloaded")
            # 无需长时间等待，稍微给一点渲染时间即可
            import asyncio
            await asyncio.sleep(0.2)
            pic = await page.screenshot(full_page=True)
            
        await stats_cmd.finish(MessageSegment.image(pic))
        
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"活跃报告生成出错: {e}")
        await stats_cmd.finish(f"生成报告失败了，请检查后台日志。错误: {str(e)}")
