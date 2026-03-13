from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent, PrivateMessageEvent, Message
from nonebot.params import CommandArg
from nonebot.typing import T_State
from nonebot.matcher import Matcher
from nonebot.plugin import PluginMetadata

from .data_source import search_all
from .models import Resource

__plugin_meta__ = PluginMetadata(
    name="资源搜索",
    description="多平台资源搜索插件 (Mikan, ACG.RIP)",
    usage="资源搜索 [关键词]",
    type="application",
    homepage="",
    config=None,
    supported_adapters={"~onebot.v11"},
)

search_cmd = on_command("资源搜索", aliases={"搜资源", "找资源", "磁力搜索"}, priority=10, block=True)

@search_cmd.handle()
async def handle_first_receive(matcher: Matcher, args: Message = CommandArg()):
    if args.extract_plain_text():
        matcher.set_arg("keyword", args)

@search_cmd.got("keyword", prompt="请输入要搜索的关键词：")
async def handle_search(bot: Bot, event: MessageEvent, matcher: Matcher, state: T_State):
    keyword = str(state["keyword"]).strip()
    if not keyword:
        await matcher.finish("关键词不能为空！")
    
    await matcher.send(f"正在搜索 '{keyword}' ...")
    
    try:
        resources = await search_all(keyword)
    except Exception as e:
        await matcher.finish(f"搜索出错: {e}")
        return

    if not resources:
        await matcher.finish("未找到相关资源，请尝试更换关键词。")
    
    # Limit to 50 results for forward message
    resources = resources[:50]
    
    # Construct forward message nodes
    nodes = []
    for res in resources:
        content = f"[{res.source}] {res.title}"
        if res.size:
            content += f"\n大小: {res.size}"
        if res.date:
            content += f"\n日期: {res.date}"
        content += f"\n链接: {res.link}"
        if res.magnet:
             content += f"\n磁力: {res.magnet}"
             
        nodes.append({
            "type": "node",
            "data": {
                "name": "ResourceSearch",
                "uin": str(event.self_id),
                "content": content
            }
        })
    
    try:
        if isinstance(event, GroupMessageEvent):
            await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=nodes)
        elif isinstance(event, PrivateMessageEvent):
            await bot.call_api("send_private_forward_msg", user_id=event.user_id, messages=nodes)
        else:
            # Fallback for other events
            msg_list = []
            for r in resources[:5]:
                msg = f"[{r.source}] {r.title}"
                if r.size:
                    msg += f"\nSize: {r.size}"
                # Prioritize magnet link
                if r.magnet:
                    msg += f"\n{r.magnet}"
                else:
                    msg += f"\n{r.link}"
                msg_list.append(msg)
            await matcher.finish("\n\n".join(msg_list))
    except Exception as e:
        # Fallback if forward message fails
        await matcher.send(f"发送合并转发消息失败，转为文本发送（前5条）：")
        
        msg_list = []
        for r in resources[:5]:
            msg = f"[{r.source}] {r.title}"
            if r.size:
                msg += f"\nSize: {r.size}"
            # Prioritize magnet link
            if r.magnet:
                msg += f"\n{r.magnet}"
            else:
                msg += f"\n{r.link}"
            msg_list.append(msg)
            
        await matcher.finish("\n\n".join(msg_list))
