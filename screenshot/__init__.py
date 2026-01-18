import re
from nonebot import on_command, logger
from nonebot.adapters.onebot.v11 import MessageSegment, Message
from nonebot.params import CommandArg
from nonebot_plugin_htmlrender import get_new_page
from nonebot.plugin import PluginMetadata
from nonebot.exception import FinishedException

__plugin_meta__ = PluginMetadata(
    name="网页快照",
    description="将网页转换为图片",
    usage="/截图 [URL]",
)

screenshot = on_command("截图", aliases={"screenshot", "webshot"}, priority=5, block=True)

@screenshot.handle()
async def handle_screenshot(arg: Message = CommandArg()):
    url = arg.extract_plain_text().strip()
    
    if not url:
        await screenshot.finish("请发送要截图的网址，例如：/截图 https://www.baidu.com")
    
    # 简单的 URL 格式校验
    if not re.match(r'^https?://', url):
        url = "http://" + url
        
    await screenshot.send(f"正在抓取网页: {url}，请稍候...")
    
    try:
        # 使用 htmlrender 的 get_new_page 手动截图
        async with get_new_page(viewport={"width": 1280, "height": 720}) as page:
            # 修改为 wait_until="load" 提高稳定性，并保留 60s 超时
            await page.goto(url, wait_until="load", timeout=60000)
            # 等待一小会儿确保 JS 渲染（可选，但通常 load 已经足够）
            import asyncio
            await asyncio.sleep(1) 
            pic = await page.screenshot(full_page=True)
        
        await screenshot.finish(MessageSegment.image(pic))
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"网页截图失败: {e}")
        await screenshot.finish(f"截图失败了，请检查网址是否正确或稍后再试。错误: {str(e)}")
