import httpx
import json
import asyncio
from typing import List, Dict, Optional, Any
from nonebot import on_command, get_plugin_config, logger
from nonebot.params import CommandArg
from nonebot.adapters.onebot.v11 import Message, MessageSegment, MessageEvent
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="B站追番查询",
    description="查询B站用户的追番列表",
    usage="追番 [用户名/UID]",
    config=Config,
)

plugin_config = get_plugin_config(Config)

# 尝试导入 htmlrender
try:
    from nonebot_plugin_htmlrender import html_to_pic
except ImportError:
    html_to_pic = None

from nonebot.exception import FinishedException

search_bangumi = on_command("追番", aliases={"查追番", "b站追番"}, priority=5, block=True)

async def get_uid_by_name(name: str) -> Optional[int]:
    """通过用户名获取 UID (使用移动端接口绕过部分限制)"""
    try:
        url = "https://api.bilibili.com/x/web-interface/search/type"
        params = {
            "search_type": "bili_user",
            "keyword": name,
            "page": 1
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Mobile/15E148 Safari/604.1",
            "Referer": "https://m.bilibili.com/",
            "Origin": "https://m.bilibili.com"
        }
        async with httpx.AsyncClient(headers=headers, timeout=10) as client:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                if data["code"] == 0 and data["data"].get("result"):
                    # 取搜索结果第一个
                    return data["data"]["result"][0]["mid"]
    except Exception as e:
        logger.error(f"获取UID失败: {e}")
    return None

async def get_bangumi_list(uid: int) -> List[Dict[str, Any]]:
    """获取追番列表"""
    try:
        url = f"https://api.viki.moe/bili/u/{uid}/bangumi"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.error(f"获取追番列表失败: {e}")
    return []

@search_bangumi.handle()
async def handle_bangumi(event: MessageEvent, arg: Message = CommandArg()):
    target = arg.extract_plain_text().strip()
    if not target:
        await search_bangumi.finish("请提供B站用户名或UID，例如：追番 187685621 或 追番 哔哩哔哩番剧")

    uid = None
    display_name = target  # 默认显示名

    if target.isdigit():
        uid = int(target)
        display_name = f"UID:{uid}"
    else:
        await search_bangumi.send(f"正在搜索用户 '{target}' 的 UID...")
        uid = await get_uid_by_name(target)
        if not uid:
            await search_bangumi.finish(f"未找到用户 '{target}'，请确认用户名是否正确。")
        display_name = target # 如果是搜索到的，就显示原搜索名

    await search_bangumi.send(f"正在获取 UID:{uid} 的追番列表...")
    bangumi_data = await get_bangumi_list(uid)

    if not bangumi_data:
        await search_bangumi.finish("该用户没有公开的追番列表，或者获取失败。")

    # 格式化输出
    if html_to_pic:
        # 使用 HTML 渲染，更美观且稳定
        html_content = f"""
        <html>
        <head>
            <style>
                body {{
                    font-family: 'Microsoft YaHei', sans-serif;
                    background-color: #f4f4f4;
                    margin: 0;
                    padding: 20px;
                    display: inline-block; /* 关键：让 body 尺寸随内容自适应 */
                }}
                .container {{
                    background: white;
                    border-radius: 12px;
                    box-shadow: 0 4px 15px rgba(0,0,0,0.1);
                    width: 1000px;
                    overflow: hidden;
                }}
                .header {{
                    background: #fb7299;
                    color: white;
                    padding: 20px;
                    text-align: center;
                    font-size: 28px;
                    font-weight: bold;
                }}
                .list {{
                    padding: 15px;
                    display: grid;
                    grid-template-columns: repeat(3, 1fr);
                    gap: 15px;
                }}
                .item {{
                    display: flex;
                    flex-direction: column;
                    background: #fff;
                    border: 1px solid #eee;
                    border-radius: 8px;
                    overflow: hidden;
                    transition: transform 0.2s;
                }}
                .cover {{
                    width: 100%;
                    height: 180px;
                    object-fit: cover;
                }}
                .info {{
                    padding: 10px;
                    display: flex;
                    flex-direction: column;
                    gap: 5px;
                }}
                .title {{
                    font-size: 16px;
                    font-weight: bold;
                    color: #333;
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                }}
                .progress {{
                    font-size: 13px;
                    color: #666;
                }}
                .score {{
                    align-self: flex-start;
                    background: #ffa726;
                    color: white;
                    padding: 2px 8px;
                    border-radius: 4px;
                    font-size: 11px;
                }}
                .footer {{
                    text-align: center;
                    padding: 15px;
                    color: #999;
                    font-size: 12px;
                    background: #fafafa;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">{display_name} 的追番列表</div>
                <div class="list">
        """
        
        # 展示前 30 条
        display_data = bangumi_data[:30]
        for item in display_data:
            title = item.get("title", "未知")
            cover = item.get("cover", "")
            if cover.startswith("//"):
                cover = "https:" + cover
            elif not cover.startswith("http"):
                cover = "https://" + cover
                
            progress = item.get("progress", "未看") or "未看"
            total = item.get("total_count", "?")
            if total == -1: total = "连载中"
            
            score = item.get("rating", {}).get("score", "无")
            
            html_content += f"""
                <div class="item">
                    <img class="cover" src="{cover}">
                    <div class="info">
                        <div class="title">{title}</div>
                        <div class="progress">进度：{progress} / {total}</div>
                        <div class="score">⭐ {score}</div>
                    </div>
                </div>
            """

        html_content += f"""
                </div>
                <div class="footer">仅展示前 {len(display_data)} 条（共 {len(bangumi_data)} 条记录）</div>
            </div>
        </body>
        </html>
        """

        try:
            # 计算动态高度：基础高度(头尾)约 200px + 每行约 320px
            rows = (len(display_data) + 2) // 3
            dynamic_height = 250 + rows * 320
            
            # 移除不支持的 selector 参数，改用动态计算高度
            pic = await html_to_pic(html_content, viewport={"width": 1100, "height": dynamic_height})
            await search_bangumi.finish(MessageSegment.image(pic))
        except FinishedException:
            raise
        except Exception as e:
            logger.error(f"HTML 渲染失败: {e}")
            # 退化为文本输出

    # 文本输出保底
    reply = f"UID: {uid} 的追番列表（前10条）：\n"
    for item in bangumi_data[:10]:
        title = item.get("title", "未知")
        progress = item.get("progress", "未看") or "未看"
        reply += f"- {title} ({progress})\n"
    
    if len(bangumi_data) > 10:
        reply += f"\n共 {len(bangumi_data)} 条记录"
    
    await search_bangumi.finish(reply)
