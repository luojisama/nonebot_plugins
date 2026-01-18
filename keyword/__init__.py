from nonebot import get_plugin_config, on_message, on_command, get_driver, logger
from nonebot.plugin import PluginMetadata
from nonebot.exception import FinishedException
from nonebot.adapters.onebot.v11 import Message, MessageSegment, MessageEvent, Bot, GroupMessageEvent, PrivateMessageEvent
from nonebot.params import CommandArg
import uuid

from .config import Config
from .models import KeywordRule, MatchType, ReplyType, Reply
from .utils import load_keywords, save_keywords

__plugin_meta__ = PluginMetadata(
    name="关键词回复",
    description="支持精确和模糊匹配的关键词回复插件",
    usage="使用 JSON 存储关键词及回复内容",
    config=Config,
)

config = get_plugin_config(Config)
superusers = get_driver().config.superusers

# 关键词匹配器
keywords_matcher = on_message(priority=99, block=False)

@keywords_matcher.handle()
async def handle_keywords(bot: Bot, event: MessageEvent):
    msg = event.get_plaintext().strip()
    if not msg:
        return

    rules = load_keywords()
    
    for rule in rules:
        matched = False
        if rule.match_type == MatchType.EXACT:
            if msg in rule.keywords:
                matched = True
        elif rule.match_type == MatchType.FUZZY:
            if any(kw in msg for kw in rule.keywords):
                matched = True
        
        if matched:
            reply_msg = Message()
            for reply in rule.replies:
                if reply.type == ReplyType.TEXT:
                    reply_msg += MessageSegment.text(reply.data)
                elif reply.type == ReplyType.IMAGE:
                    reply_msg += MessageSegment.image(reply.data)
                elif reply.type == ReplyType.FACE:
                    reply_msg += MessageSegment.face(int(reply.data))
            
            await keywords_matcher.finish(reply_msg)

# 管理命令
add_kw = on_command("添加关键词", priority=5, block=True)
list_kw = on_command("查看关键词", priority=5, block=True)
del_kw = on_command("删除关键词", priority=5, block=True)

@add_kw.handle()
async def handle_add(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if event.get_user_id() not in superusers:
        await add_kw.finish("只有超级用户可以使用该功能。")
    
    # 提取前两个参数：类型和关键词
    # 我们需要找到第一个 text 段并从中提取
    msg_list = list(args)
    if not msg_list or msg_list[0].type != "text":
        await add_kw.finish("用法: 添加关键词 [类型:精确/模糊] [关键词] [回复内容(可含图片表情)]")
        return
    
    first_text = msg_list[0].data["text"].strip()
    parts = first_text.split(maxsplit=2)
    
    if len(parts) < 2:
        await add_kw.finish("参数不足。用法: 添加关键词 [类型:精确/模糊] [关键词] [回复内容]")
        return
    
    m_type_str = parts[0]
    kw = parts[1]
    
    if m_type_str not in ["精确", "模糊"]:
        await add_kw.finish("类型错误。请使用: 精确 或 模糊")
        return
    m_type = MatchType.EXACT if m_type_str == "精确" else MatchType.FUZZY
    
    # 构造回复内容
    replies = []
    
    # 处理第一个 text 段中剩余的内容
    if len(parts) == 3:
        remaining_text = parts[2].lstrip()
        if remaining_text:
            replies.append(Reply(type=ReplyType.TEXT, data=remaining_text))
    
    # 处理后续的段
    for seg in msg_list[1:]:
        if seg.type == "text":
            replies.append(Reply(type=ReplyType.TEXT, data=seg.data["text"]))
        elif seg.type == "image":
            # 优先使用 url，如果没有则尝试 file (可能是本地路径或 Base64)
            data = seg.data.get("url") or seg.data.get("file")
            if data:
                replies.append(Reply(type=ReplyType.IMAGE, data=data))
        elif seg.type == "face":
            replies.append(Reply(type=ReplyType.FACE, data=str(seg.data["id"])))
            
    if not replies:
        await add_kw.finish("回复内容不能为空。")
        return
        
    new_rule = KeywordRule(
        id=str(uuid.uuid4()),
        keywords=[kw],
        match_type=m_type,
        replies=replies
    )
    
    rules = load_keywords()
    rules.append(new_rule)
    save_keywords(rules)
    
    await add_kw.finish(f"已添加关键词: {kw} ({m_type_str})，包含 {len(replies)} 个回复分段")

@list_kw.handle()
async def handle_list(bot: Bot, event: MessageEvent):
    if event.get_user_id() not in superusers:
        await list_kw.finish("只有超级用户可以使用该功能。")
    
    rules = load_keywords()
    if not rules:
        await list_kw.finish("目前没有关键词。")
        return
    
    messages = []
    
    # 限制显示的关键词数量，防止合并转发过大导致失败
    max_rules = 50 
    display_rules = rules[:max_rules]
    
    # 添加说明节点
    messages.append({
        "type": "node",
        "data": {
            "name": "关键词管理",
            "uin": bot.self_id,
            "content": f"当前显示前 {len(display_rules)} 个关键词（共 {len(rules)} 个）："
        }
    })
    
    for rule in display_rules:
        m_type_display = "精确" if rule.match_type == MatchType.EXACT else "模糊"
        header = f"ID: {rule.id[:8]}\n类型: {m_type_display}\n关键词: {','.join(rule.keywords)}\n回复内容: "
        
        reply_msg = Message()
        for reply in rule.replies:
            if reply.type == ReplyType.TEXT:
                reply_msg += MessageSegment.text(reply.data)
            elif reply.type == ReplyType.IMAGE:
                # 关键修复：NapCat 在合并转发中处理图片 URL 容易超时或 400
                # 如果是 URL，可以尝试直接作为文本展示或保持原样
                # 这里我们保持原样，但在发送失败时提供回退方案
                reply_msg += MessageSegment.image(reply.data)
            elif reply.type == ReplyType.FACE:
                reply_msg += MessageSegment.face(int(reply.data))
        
        messages.append({
            "type": "node",
            "data": {
                "name": "关键词规则",
                "uin": bot.self_id,
                "content": Message(header) + reply_msg
            }
        })
    
    try:
        if isinstance(event, GroupMessageEvent):
            await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=messages)
        else:
            await bot.call_api("send_private_forward_msg", user_id=event.user_id, messages=messages)
    except FinishedException:
        raise
    except Exception as e:
        # 回退方案：如果合并转发失败（通常是因为图片过多或网络问题），改用纯文本列表
        logger.error(f"合并转发失败，尝试纯文本回退: {e}")
        text_list = []
        for rule in display_rules:
            m_type_display = "精确" if rule.match_type == MatchType.EXACT else "模糊"
            kws = ",".join(rule.keywords)
            # 缩短关键词显示长度
            if len(kws) > 20: kws = kws[:17] + "..."
            text_list.append(f"• [{rule.id[:8]}] {kws} ({m_type_display})")
        
        summary = "\n".join(text_list)
        msg = f"⚠️ 合并转发发送失败 (NapCat 限制)\n\n当前显示前 {len(display_rules)} 个规则：\n{summary}"
        if len(rules) > max_rules:
            msg += f"\n\n... 更多规则请通过 ID 删除后查看"
        
        await list_kw.finish(msg)

@del_kw.handle()
async def handle_del(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if event.get_user_id() not in superusers:
        await del_kw.finish("只有超级用户可以使用该功能。")
    
    kw_id = args.extract_plain_text().strip()
    if not kw_id:
        await del_kw.finish("用法: 删除关键词 [ID前8位]")
        return
    
    rules = load_keywords()
    new_rules = [r for r in rules if not r.id.startswith(kw_id)]
    
    if len(rules) == len(new_rules):
        await del_kw.finish("未找到匹配的关键词 ID。")
    else:
        save_keywords(new_rules)
        await del_kw.finish(f"已删除 {len(rules) - len(new_rules)} 个关键词。")


