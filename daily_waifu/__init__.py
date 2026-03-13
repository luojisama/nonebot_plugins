import os
import random
import time
import asyncio
from pathlib import Path

from nonebot import get_driver, on_command, on_message
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageEvent, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from .data_source import MudaeState, ensure_dir, safe_remove
from .model import PluginConfig
from .util.character_manager import CharacterManager

__plugin_meta__ = PluginMetadata(
    name="每日老婆(Mudae)",
    description="基于 daily_waifu 的 Mudae 抽卡后宫玩法迁移版",
    usage=(
        "菜单/帮助\n"
        "抽卡/ck, 结婚(回复抽卡消息), 我的后宫 [页码], 离婚 <ID>, 交换 <我的ID> <对方ID>\n"
        "最爱 <ID>, 许愿 <ID>, 愿望单, 删除许愿 <ID>, 查询 <ID> [图号], 搜索 <关键词>\n"
        "添加图片 <ID>, 清除图片 <ID>, 群排行\n"
        "管理员: 强制离婚/清理后宫/系统设置/刷新/终极轮回"
    ),
    config=PluginConfig,
)

DRAW_MSG_TTL = 45

_driver_cfg = get_driver().config
_cfg_input = {
    key: getattr(_driver_cfg, key)
    for key in PluginConfig.model_fields
    if hasattr(_driver_cfg, key)
}
plugin_config = PluginConfig(**_cfg_input)
store = MudaeState(plugin_config.data_path)
manager = CharacterManager()
manager.load_characters()
manager.load_bonds()

ensure_dir(plugin_config.image_dir)
GROUP_LOCKS: dict[str, asyncio.Lock] = {}
SUPERUSERS = {str(x) for x in getattr(get_driver().config, "superusers", set())}


def _gid(event: GroupMessageEvent) -> str:
    return str(event.group_id)


def _uid(event: MessageEvent) -> str:
    return str(event.user_id)


def _lock(gid: str) -> asyncio.Lock:
    if gid not in GROUP_LOCKS:
        GROUP_LOCKS[gid] = asyncio.Lock()
    return GROUP_LOCKS[gid]


def _args_text(args: Message) -> str:
    return args.extract_plain_text().strip()


def _exact_text_cmd(*commands: str) -> Rule:
    cmd_set = {x.strip() for x in commands if x and x.strip()}

    async def _checker(event: MessageEvent) -> bool:
        text = event.get_plaintext().strip()
        if not text:
            return False
        if text in cmd_set:
            return True
        if text.startswith("/"):
            return text[1:].strip() in cmd_set
        return False

    return Rule(_checker)


def _reply_id(event: MessageEvent) -> str | None:
    for seg in event.message:
        seg_type = seg.get("type") if isinstance(seg, dict) else getattr(seg, "type", None)
        if seg_type == "reply":
            seg_data = seg.get("data", {}) if isinstance(seg, dict) else getattr(seg, "data", {})
            rid = seg_data.get("id") or seg_data.get("message_id")
            return str(rid) if rid is not None else None
    reply = getattr(event, "reply", None)
    if reply is not None:
        if isinstance(reply, dict):
            rid = reply.get("message_id") or reply.get("id")
        else:
            rid = getattr(reply, "message_id", None) or getattr(reply, "id", None)
        if rid is not None:
            return str(rid)
    return None


def _is_admin(event: MessageEvent) -> bool:
    uid = _uid(event)
    if uid in SUPERUSERS:
        return True
    if isinstance(event, GroupMessageEvent):
        role = (event.sender.role or "") if event.sender else ""
        return role in {"admin", "owner"}
    return False


def _is_owner_or_super(event: MessageEvent) -> bool:
    uid = _uid(event)
    if uid in SUPERUSERS:
        return True
    if isinstance(event, GroupMessageEvent):
        role = (event.sender.role or "") if event.sender else ""
        return role == "owner"
    return False


def _char_name(cid: str) -> str:
    char = manager.get_character_by_id(cid)
    if not char:
        return str(cid)
    return str(char.get("name") or cid)


def _group_cfg(gid: str) -> dict:
    cfg = store.get_group_cfg(gid)
    if "draw_hourly_limit" not in cfg:
        cfg["draw_hourly_limit"] = plugin_config.draw_hourly_limit
    if "claim_cooldown" not in cfg:
        cfg["claim_cooldown"] = plugin_config.claim_cooldown
    if "harem_max_size" not in cfg:
        cfg["harem_max_size"] = plugin_config.harem_max_size
    if "draw_cooldown" not in cfg:
        cfg["draw_cooldown"] = plugin_config.draw_cooldown
    if "ntr_chance" not in cfg:
        cfg["ntr_chance"] = plugin_config.ntr_chance
    return cfg


def _extract_message_id(resp: object) -> str | None:
    if resp is None:
        return None
    if isinstance(resp, (int, str)):
        value = str(resp).strip()
        return value or None
    if isinstance(resp, dict):
        mid = resp.get("message_id") or resp.get("msg_id") or resp.get("id")
        if mid is not None:
            return str(mid)
        data = resp.get("data")
        if isinstance(data, dict):
            mid = data.get("message_id") or data.get("msg_id") or data.get("id")
            if mid is not None:
                return str(mid)
    for key in ("message_id", "msg_id", "id"):
        mid = getattr(resp, key, None)
        if mid is not None:
            return str(mid)
    data = getattr(resp, "data", None)
    if isinstance(data, dict):
        mid = data.get("message_id") or data.get("msg_id") or data.get("id")
        if mid is not None:
            return str(mid)
    return None


async def _send_group(bot: Bot, group_id: int, message: Message) -> str | None:
    resp = await bot.send_group_msg(group_id=group_id, message=message)
    return _extract_message_id(resp)


def _gender_title(char: dict) -> str:
    gender = str(char.get("gender") or "")
    if gender == "女":
        return "老婆"
    if gender == "男":
        return "老公"
    return "对象"


def _effective_heat(base_heat: float, wishers: int, bond_ratio: float = 1.0) -> tuple[int, float]:
    value = base_heat * (1.1 ** wishers) * bond_ratio
    return int(round(value)), value


menu_cmd = on_command("菜单", aliases={"帮助", "menu", "help"}, priority=plugin_config.daily_waifu_priority, block=True)
draw_cmd = on_command("抽卡", aliases={"ck"}, priority=plugin_config.daily_waifu_priority, block=True)
marry_cmd = on_message(rule=_exact_text_cmd("结婚"), priority=plugin_config.daily_waifu_priority, block=True)
harem_cmd = on_command("我的后宫", priority=plugin_config.daily_waifu_priority, block=True)
divorce_cmd = on_command("离婚", priority=plugin_config.daily_waifu_priority, block=True)
exchange_cmd = on_command("交换", priority=plugin_config.daily_waifu_priority, block=True)
accept_exchange_cmd = on_message(
    rule=_exact_text_cmd("同意交换"),
    priority=plugin_config.daily_waifu_priority,
    block=True,
)
favorite_cmd = on_command("最爱", priority=plugin_config.daily_waifu_priority, block=True)
wish_cmd = on_command("许愿", priority=plugin_config.daily_waifu_priority, block=True)
wish_list_cmd = on_command("愿望单", priority=plugin_config.daily_waifu_priority, block=True)
remove_wish_cmd = on_command("删除许愿", priority=plugin_config.daily_waifu_priority, block=True)
query_cmd = on_command("查询", priority=plugin_config.daily_waifu_priority, block=True)
search_cmd = on_command("搜索", priority=plugin_config.daily_waifu_priority, block=True)
add_image_cmd = on_command("添加图片", priority=plugin_config.daily_waifu_priority, block=True)
clear_image_cmd = on_command("清除图片", priority=plugin_config.daily_waifu_priority, block=True)
force_divorce_cmd = on_command("强制离婚", priority=plugin_config.daily_waifu_priority, block=True)
clear_harem_cmd = on_command("清理后宫", priority=plugin_config.daily_waifu_priority, block=True)
config_cmd = on_command("系统设置", priority=plugin_config.daily_waifu_priority, block=True)
refresh_cmd = on_command("刷新", priority=plugin_config.daily_waifu_priority, block=True)
rank_cmd = on_command("群排行", priority=plugin_config.daily_waifu_priority, block=True)
reset_cmd = on_command("终极轮回", priority=plugin_config.daily_waifu_priority, block=True)


@menu_cmd.handle()
async def _(event: MessageEvent):
    lines = [
        "普通指令:",
        "菜单/帮助",
        "抽卡/ck",
        "结婚 (回复抽卡消息)",
        "离婚 <角色ID>",
        "最爱 <角色ID>",
        "查询 <角色ID> [图片序号]",
        "搜索 <角色名>",
        "我的后宫 [页码]",
        "群排行",
        "添加图片 <角色ID> (消息里带图)",
        "清除图片 <角色ID>",
        "交换 <我的角色ID> <对方角色ID>",
        "同意交换 (回复交换请求)",
        "许愿 <角色ID>",
        "愿望单",
        "删除许愿 <角色ID>",
        "----------------",
        "管理员指令:",
        "系统设置 <功能> <参数>",
        "清理后宫 <QQ号>",
        "强制离婚 <角色ID>",
        "----------------",
        "群主/超管:",
        "刷新 <QQ号>",
        "终极轮回 确认",
    ]
    await menu_cmd.finish("\n".join(lines))


@draw_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        await draw_cmd.finish("该指令仅支持群聊")
    gid = _gid(event)
    uid = _uid(event)
    store.add_user(gid, uid)
    cfg = _group_cfg(gid)

    async with _lock(gid):
        now = time.time()
        now_tm = time.localtime(now)
        bucket = f"{now_tm.tm_year}-{now_tm.tm_yday}-{now_tm.tm_hour}"
        rec_bucket, rec_count = store.get_draw_status(gid, uid)

        count = rec_count if rec_bucket == bucket else 0
        limit = int(cfg.get("draw_hourly_limit", plugin_config.draw_hourly_limit))
        if count >= limit:
            await draw_cmd.finish("本小时抽卡次数已达上限")

        draw_cooldown = max(int(cfg.get("draw_cooldown", plugin_config.draw_cooldown)), 0)
        if draw_cooldown > 0:
            last_draw = store.get_last_draw(gid)
            if (now - last_draw) < draw_cooldown:
                await draw_cmd.finish("抽卡冷却中，请稍后再试")

        wish_list = store.get_wish_list(gid, uid)
        if wish_list and random.random() < 0.003:
            char = manager.get_character_by_id(random.choice(wish_list))
        else:
            draw_scope = cfg.get("draw_scope")
            char = manager.get_random_character(limit=draw_scope)

        if not char:
            await draw_cmd.finish("角色池为空")

        cid = str(char.get("id"))
        name = str(char.get("name") or cid)
        married_to = store.get_married_to(gid, cid)
        wished_by = store.get_wished_by(gid, cid)
        ntr_chance = int(cfg.get("ntr_chance", plugin_config.ntr_chance))
        allow_ntr = bool(married_to) and random.random() < (ntr_chance * (1 + len(wished_by)) / 100)
        claimable = allow_ntr or (not married_to)

        images = list(char.get("image") or [])
        images.extend(store.get_custom_images(gid, cid))
        image_url = random.choice(images) if images else None

        msg = Message()
        if wished_by and (allow_ntr or not married_to):
            for w_uid in wished_by:
                msg += MessageSegment.at(int(w_uid))
                msg += MessageSegment.text(" ")
            msg += MessageSegment.text(f"已许愿\n{name}")
        else:
            msg += MessageSegment.text(name)

        if married_to:
            msg += MessageSegment.text("\n已婚: ")
            msg += MessageSegment.at(int(married_to))

        if image_url:
            msg += MessageSegment.image(image_url)

        if claimable:
            msg += MessageSegment.text("\n回复本消息并发送“结婚”即可收集")
        else:
            msg += MessageSegment.text("\n该角色当前不可收集")
        if count + 1 >= limit:
            msg += MessageSegment.text("\n本小时次数已用完")

        message_id = await _send_group(bot, event.group_id, msg)

        store.set_draw_status(gid, uid, bucket, count + 1)
        store.set_last_draw(gid, now)
        if message_id and claimable:
            store.track_draw_message(gid, message_id, cid, DRAW_MSG_TTL)


@marry_cmd.handle()
async def _(event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        await marry_cmd.finish("该指令仅支持群聊")
    rid = _reply_id(event)
    if not rid:
        await marry_cmd.finish("请回复抽卡消息后再发送“结婚”")

    gid = _gid(event)
    uid = _uid(event)
    cfg = _group_cfg(gid)
    now = time.time()

    async with _lock(gid):
        draw_msg = store.get_draw_message(gid, rid)
        if not draw_msg:
            await marry_cmd.finish("该消息不是可结婚的抽卡消息，或已过期")
        ts = float(draw_msg.get("ts", 0))
        if ts and now - ts > DRAW_MSG_TTL:
            store.pop_draw_message(gid, rid)
            await marry_cmd.finish("抽卡消息已过期")

        cid = str(draw_msg.get("char_id"))
        char = manager.get_character_by_id(cid)
        if not char:
            store.pop_draw_message(gid, rid)
            await marry_cmd.finish("角色数据不存在")

        cooldown = int(cfg.get("claim_cooldown", plugin_config.claim_cooldown))
        last_claim = store.get_last_claim(gid, uid)
        if now - last_claim < cooldown:
            wait_sec = int(cooldown - (now - last_claim))
            wait_min = max(1, (wait_sec + 59) // 60)
            await marry_cmd.finish(f"结婚冷却中，剩余约 {wait_min} 分钟")

        partners = store.get_partners(gid, uid)
        harem_max = int(cfg.get("harem_max_size", plugin_config.harem_max_size))
        if len(partners) >= harem_max:
            await marry_cmd.finish(f"你的后宫已满({harem_max})")

        claimed_by = store.get_married_to(gid, cid)
        if claimed_by == uid:
            store.pop_draw_message(gid, rid)
            await marry_cmd.finish(f"{char.get('name')} 已经是你的了")

        if claimed_by:
            prev_fav = store.get_fav(gid, claimed_by)
            if prev_fav and prev_fav == cid and random.random() < 0.7:
                store.pop_draw_message(gid, rid)
                await marry_cmd.finish("失败了！对方把 TA 设为最爱，牛不动")

            prev_partners = store.get_partners(gid, claimed_by)
            prev_partners = [x for x in prev_partners if x != cid]
            store.set_partners(gid, claimed_by, prev_partners)
            if prev_fav == cid:
                store.clear_fav(gid, claimed_by)

        if cid not in partners:
            partners.append(cid)
        store.set_partners(gid, uid, partners)
        store.set_married_to(gid, cid, uid)
        store.set_last_claim(gid, uid, now)
        store.pop_draw_message(gid, rid)

        title = _gender_title(char)
        await marry_cmd.finish(f"{char.get('name')} 现在是你的{title}了")


@harem_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await harem_cmd.finish("该指令仅支持群聊")
    gid = _gid(event)
    uid = _uid(event)
    page_text = _args_text(args)
    page = int(page_text) if page_text.isdigit() else 0

    partners = store.get_partners(gid, uid)
    if not partners:
        store.set_harem_heat(gid, uid, 0)
        await harem_cmd.finish("你的后宫空空如也")

    fav = store.get_fav(gid, uid)
    bond_status = manager.get_bond_collection_status(partners)
    bond_ratio_by_cid: dict[int, float] = {}
    for _, _, _, ratio, owned_ids in bond_status:
        for bc in owned_ids:
            bond_ratio_by_cid[int(bc)] = max(bond_ratio_by_cid.get(int(bc), 1.0), ratio)

    entries: list[str] = []
    total_heat = 0
    for cid in partners:
        char = manager.get_character_by_id(cid)
        if not char:
            continue
        base_heat = float(char.get("heat") or 0)
        wishers = len(store.get_wished_by(gid, cid))
        ratio = bond_ratio_by_cid.get(int(cid), 1.0)
        heat_int, heat_raw = _effective_heat(base_heat, wishers, ratio)
        total_heat += heat_int
        line = f"{char.get('name')} (ID: {cid})"
        if fav and fav == str(cid):
            line = f"[最爱] {line}"
        if base_heat > 0 and (wishers > 0 or ratio > 1):
            pct = (heat_raw - base_heat) / base_heat * 100
            line += f" (+{pct:.1f}%)"
        entries.append(line)

    store.set_harem_heat(gid, uid, total_heat)

    per_page = 10
    total_pages = max(1, (len(entries) + per_page - 1) // per_page)
    if page <= 0:
        page = 1
    if page > total_pages:
        page = total_pages

    start = (page - 1) * per_page
    body = entries[start:start + per_page]
    msg_lines = [f"你的后宫 | 总人气: {total_heat}"]
    msg_lines.extend(body)
    msg_lines.append(f"({page}/{total_pages})")

    if bond_status:
        msg_lines.append("羁绊加成:")
        for name, owned, total, ratio, _ in bond_status[:10]:
            msg_lines.append(f"{name} ({owned}/{total}) +{(ratio - 1) * 100:.0f}%")

    await harem_cmd.finish("\n".join(msg_lines))


@divorce_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await divorce_cmd.finish("该指令仅支持群聊")
    cid = _args_text(args)
    if not cid.isdigit():
        await divorce_cmd.finish("用法: 离婚 <角色ID>")

    gid = _gid(event)
    uid = _uid(event)
    partners = store.get_partners(gid, uid)
    if cid not in partners:
        await divorce_cmd.finish("你并没有和该角色结婚")

    partners = [x for x in partners if x != cid]
    store.set_partners(gid, uid, partners)
    store.clear_married_to(gid, cid)
    if store.get_fav(gid, uid) == cid:
        store.clear_fav(gid, uid)

    await divorce_cmd.finish(f"已与你的 {_char_name(cid)} 离婚")


@exchange_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await exchange_cmd.finish("该指令仅支持群聊")

    parts = [p for p in _args_text(args).split() if p]
    if len(parts) != 2 or (not parts[0].isdigit()) or (not parts[1].isdigit()):
        await exchange_cmd.finish("用法: 交换 <我的角色ID> <对方角色ID>")

    my_cid, other_cid = parts
    gid = _gid(event)
    uid = _uid(event)

    if store.get_married_to(gid, my_cid) != uid:
        await exchange_cmd.finish("你并未拥有第一个角色")

    other_uid = store.get_married_to(gid, other_cid)
    if not other_uid or other_uid == uid:
        await exchange_cmd.finish("对方角色未婚或属于你自己，无法交换")

    msg = MessageSegment.reply(event.message_id)
    msg += MessageSegment.at(int(uid))
    msg += MessageSegment.text(f" 想用 {_char_name(my_cid)} 交换 {_char_name(other_cid)}\n")
    msg += MessageSegment.at(int(other_uid))
    msg += MessageSegment.text(" 同意请回复本消息并发送“同意交换”")

    req_msg_id = await _send_group(bot, event.group_id, Message(msg))
    if not req_msg_id:
        await exchange_cmd.finish("交换请求发送失败")

    store.create_exchange_request(
        gid,
        req_msg_id,
        {
            "from_uid": uid,
            "to_uid": other_uid,
            "from_cid": my_cid,
            "to_cid": other_cid,
        },
        DRAW_MSG_TTL,
    )


@accept_exchange_cmd.handle()
async def _(event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        await accept_exchange_cmd.finish("该指令仅支持群聊")
    rid = _reply_id(event)
    if not rid:
        await accept_exchange_cmd.finish("请回复交换请求消息后执行")

    gid = _gid(event)
    uid = _uid(event)
    req = store.pop_exchange_request(gid, rid)
    if not req:
        await accept_exchange_cmd.finish("交换请求不存在或已过期")
    if str(req.get("to_uid")) != uid:
        await accept_exchange_cmd.finish("你不是该交换请求的目标用户")

    from_uid = str(req.get("from_uid"))
    to_uid = str(req.get("to_uid"))
    from_cid = str(req.get("from_cid"))
    to_cid = str(req.get("to_cid"))

    if store.get_married_to(gid, from_cid) != from_uid or store.get_married_to(gid, to_cid) != to_uid:
        await accept_exchange_cmd.finish("交换失败：角色归属已变化")

    from_list = store.get_partners(gid, from_uid)
    to_list = store.get_partners(gid, to_uid)
    if from_cid not in from_list or to_cid not in to_list:
        await accept_exchange_cmd.finish("交换失败：后宫数据异常")

    from_list = [x for x in from_list if x != from_cid]
    to_list = [x for x in to_list if x != to_cid]
    from_list.append(to_cid)
    to_list.append(from_cid)
    store.set_partners(gid, from_uid, from_list)
    store.set_partners(gid, to_uid, to_list)
    store.set_married_to(gid, from_cid, to_uid)
    store.set_married_to(gid, to_cid, from_uid)

    if store.get_fav(gid, from_uid) == from_cid:
        store.clear_fav(gid, from_uid)
    if store.get_fav(gid, to_uid) == to_cid:
        store.clear_fav(gid, to_uid)

    await accept_exchange_cmd.finish(
        f"交换成功：{_char_name(from_cid)} ↔ {_char_name(to_cid)}"
    )


@favorite_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await favorite_cmd.finish("该指令仅支持群聊")
    cid = _args_text(args)
    if not cid.isdigit():
        await favorite_cmd.finish("用法: 最爱 <角色ID>")

    gid = _gid(event)
    uid = _uid(event)
    if cid not in store.get_partners(gid, uid):
        await favorite_cmd.finish("该角色不在你的后宫中")

    store.set_fav(gid, uid, cid)
    await favorite_cmd.finish(f"已将 {_char_name(cid)} 设为最爱")


@wish_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await wish_cmd.finish("该指令仅支持群聊")
    gid = _gid(event)
    uid = _uid(event)
    cfg = _group_cfg(gid)

    cid = _args_text(args)
    if not cid.isdigit():
        await wish_cmd.finish("用法: 许愿 <角色ID>")
    if not manager.get_character_by_id(cid):
        await wish_cmd.finish("角色ID不存在")

    wish_list = store.get_wish_list(gid, uid)
    if len(wish_list) >= int(cfg.get("harem_max_size", plugin_config.harem_max_size)):
        await wish_cmd.finish("愿望单已满")

    if cid not in wish_list:
        wish_list.append(cid)
        store.set_wish_list(gid, uid, wish_list)

    wished_by = store.get_wished_by(gid, cid)
    if uid not in wished_by:
        wished_by.append(uid)
        store.set_wished_by(gid, cid, wished_by)

    await wish_cmd.finish(f"已许愿 {_char_name(cid)}")


@wish_list_cmd.handle()
async def _(event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        await wish_list_cmd.finish("该指令仅支持群聊")
    gid = _gid(event)
    uid = _uid(event)
    wish_list = store.get_wish_list(gid, uid)
    if not wish_list:
        await wish_list_cmd.finish("你的愿望单为空")

    lines = []
    for cid in wish_list:
        married_to = store.get_married_to(gid, cid)
        line = f"{_char_name(cid)} (ID: {cid})"
        if married_to == uid:
            line += " [已拥有]"
        elif married_to:
            line += " [已婚]"
        lines.append(line)
    await wish_list_cmd.finish("\n".join(lines))


@remove_wish_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await remove_wish_cmd.finish("该指令仅支持群聊")
    gid = _gid(event)
    uid = _uid(event)

    cid = _args_text(args)
    if not cid.isdigit():
        await remove_wish_cmd.finish("用法: 删除许愿 <角色ID>")

    wish_list = [x for x in store.get_wish_list(gid, uid) if x != cid]
    store.set_wish_list(gid, uid, wish_list)
    wished_by = [x for x in store.get_wished_by(gid, cid) if x != uid]
    store.set_wished_by(gid, cid, wished_by)

    await remove_wish_cmd.finish("已从愿望单移除")


async def _character_info_text(gid: str, char: dict, iid: str | None = None) -> Message:
    cid = str(char.get("id"))
    name = str(char.get("name") or cid)
    gender = str(char.get("gender") or "?")

    base_heat = float(char.get("heat") or 0)
    wishers = len(store.get_wished_by(gid, cid))
    heat_int, heat_raw = _effective_heat(base_heat, wishers)
    heat_text = str(heat_int)
    if base_heat > 0 and wishers > 0:
        heat_text += f" (+{((heat_raw - base_heat) / base_heat * 100):.1f}%)"

    bonds = manager.get_bonds_for_character(int(cid))
    images = list(char.get("image") or [])
    images.extend(store.get_custom_images(gid, cid))

    idx = None
    if images:
        if iid and iid.isdigit() and 1 <= int(iid) <= len(images):
            idx = int(iid)
        else:
            idx = random.randint(1, len(images))

    msg = Message()
    msg += MessageSegment.text(f"ID: {cid}\n{name}\n性别: {gender}\n热度: {heat_text}")
    if bonds:
        msg += MessageSegment.text("\n羁绊: " + " | ".join(bonds))

    married_to = store.get_married_to(gid, cid)
    if married_to:
        msg += MessageSegment.text("\n已婚: ")
        msg += MessageSegment.at(int(married_to))

    if idx:
        msg += MessageSegment.image(images[idx - 1])
        msg += MessageSegment.text(f"\n({idx}/{len(images)})")

    return msg


@query_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await query_cmd.finish("该指令仅支持群聊")
    parts = [p for p in _args_text(args).split() if p]
    if not parts:
        await query_cmd.finish("用法: 查询 <角色ID> [图片序号]")

    gid = _gid(event)
    key = parts[0]
    iid = parts[1] if len(parts) > 1 else None

    if key.isdigit():
        char = manager.get_character_by_id(key)
        if not char:
            await query_cmd.finish("角色不存在")
        msg = await _character_info_text(gid, char, iid)
        await query_cmd.finish(msg)

    matches = manager.search_characters_by_name(key)
    if not matches:
        await query_cmd.finish("没有找到匹配角色")
    if len(matches) == 1:
        msg = await _character_info_text(gid, matches[0], iid)
        await query_cmd.finish(msg)

    lines = [f"{c.get('name')} (ID: {c.get('id')})" for c in matches[:10]]
    if len(matches) > 10:
        lines.append("...")
    await query_cmd.finish("\n".join(lines))


@search_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await search_cmd.finish("该指令仅支持群聊")
    keyword = _args_text(args)
    if not keyword:
        await search_cmd.finish("用法: 搜索 <角色名>")

    matches = manager.search_characters_by_name(keyword)
    if not matches:
        await search_cmd.finish("没有找到匹配角色")
    if len(matches) == 1:
        msg = await _character_info_text(_gid(event), matches[0])
        await search_cmd.finish(msg)

    lines = [f"{c.get('name')} (ID: {c.get('id')})" for c in matches[:10]]
    if len(matches) > 10:
        lines.append("...")
    await search_cmd.finish("\n".join(lines))


def _extract_image_urls_from_segments(segments) -> list[str]:
    urls: list[str] = []
    for seg in segments:
        seg_type = seg.get("type") if isinstance(seg, dict) else getattr(seg, "type", None)
        seg_data = seg.get("data", {}) if isinstance(seg, dict) else getattr(seg, "data", {})
        if seg_type != "image":
            continue
        url = seg_data.get("url") or seg_data.get("file")
        if url:
            urls.append(str(url))
    return urls


@add_image_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await add_image_cmd.finish("该指令仅支持群聊")

    cid = _args_text(args)
    if not cid.isdigit():
        await add_image_cmd.finish("用法: 添加图片 <角色ID>")

    gid = _gid(event)
    uid = _uid(event)
    if cid not in store.get_partners(gid, uid):
        await add_image_cmd.finish("角色不在你的后宫中")

    urls = _extract_image_urls_from_segments(event.message)
    rid = _reply_id(event)
    if not urls and rid:
        try:
            msg_data = await bot.get_msg(message_id=int(rid))
            urls = _extract_image_urls_from_segments(msg_data.get("message", []))
        except Exception:
            urls = []

    if not urls:
        await add_image_cmd.finish("请在命令消息内附带图片，或回复一条带图消息")

    old = store.get_custom_images(gid, cid)
    if len(old) >= plugin_config.custom_images_limit:
        await add_image_cmd.finish(f"自定义图片已达上限({plugin_config.custom_images_limit})")

    space = plugin_config.custom_images_limit - len(old)
    merged = old + urls[:space]
    store.set_custom_images(gid, cid, merged)
    await add_image_cmd.finish(f"添加成功，当前自定义图片数量: {len(merged)}")


@clear_image_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await clear_image_cmd.finish("该指令仅支持群聊")

    cid = _args_text(args)
    if not cid.isdigit():
        await clear_image_cmd.finish("用法: 清除图片 <角色ID>")

    gid = _gid(event)
    uid = _uid(event)
    married_to = store.get_married_to(gid, cid)
    if married_to != uid and not _is_admin(event):
        await clear_image_cmd.finish("无权限，只有结婚用户或管理员可以清除")

    paths = store.get_custom_images(gid, cid)
    if not paths:
        await clear_image_cmd.finish("该角色没有自定义图片")

    for p in paths:
        if p.startswith("http://") or p.startswith("https://"):
            continue
        full = str(Path(plugin_config.image_dir) / p)
        safe_remove(full)

    store.set_custom_images(gid, cid, [])
    await clear_image_cmd.finish("已清除该角色自定义图片")


@force_divorce_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await force_divorce_cmd.finish("该指令仅支持群聊")
    if not _is_admin(event):
        await force_divorce_cmd.finish("无权限")

    cid = _args_text(args)
    if not cid.isdigit():
        await force_divorce_cmd.finish("用法: 强制离婚 <角色ID>")

    gid = _gid(event)
    store.clear_married_to(gid, cid)

    for uid in store.get_all_users(gid):
        partners = store.get_partners(gid, uid)
        if cid in partners:
            partners = [x for x in partners if x != cid]
            store.set_partners(gid, uid, partners)
            if store.get_fav(gid, uid) == cid:
                store.clear_fav(gid, uid)

    await force_divorce_cmd.finish(f"{_char_name(cid)} 已强制离婚")


@clear_harem_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await clear_harem_cmd.finish("该指令仅支持群聊")
    if not _is_admin(event):
        await clear_harem_cmd.finish("无权限")

    target_uid = _args_text(args)
    if not target_uid.isdigit():
        await clear_harem_cmd.finish("用法: 清理后宫 <QQ号>")

    gid = _gid(event)
    store.cleanup_user_harem_keep_fav(gid, target_uid)
    await clear_harem_cmd.finish(f"已清理 {target_uid} 的后宫(保留最爱)")


@config_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await config_cmd.finish("该指令仅支持群聊")
    if not _is_admin(event):
        await config_cmd.finish("无权限")

    gid = _gid(event)
    cfg = _group_cfg(gid)
    parts = [p for p in _args_text(args).split() if p]

    menu_lines = [
        "用法:",
        f"系统设置 抽卡冷却 [0~600] (当前 {cfg.get('draw_cooldown')})",
        f"系统设置 抽卡次数 [1~10] (当前 {cfg.get('draw_hourly_limit')})",
        f"系统设置 后宫上限 [5~50] (当前 {cfg.get('harem_max_size')})",
        f"系统设置 抽卡范围 [5000~20000] (当前 {cfg.get('draw_scope', '默认')})",
        f"系统设置 牛头人 [0~100] (当前 {cfg.get('ntr_chance')})",
    ]

    if len(parts) < 2:
        await config_cmd.finish("\n".join(menu_lines))

    feature, value = parts[0], parts[1]
    if not value.isdigit():
        await config_cmd.finish("参数必须是数字")

    num = int(value)
    if feature == "抽卡冷却":
        cfg["draw_cooldown"] = min(600, max(0, num))
    elif feature == "抽卡次数":
        cfg["draw_hourly_limit"] = min(10, max(1, num))
    elif feature == "后宫上限":
        cfg["harem_max_size"] = min(50, max(5, num))
    elif feature == "抽卡范围":
        cfg["draw_scope"] = min(20000, max(5000, num))
    elif feature == "牛头人":
        cfg["ntr_chance"] = min(100, max(0, num))
    else:
        await config_cmd.finish("\n".join(menu_lines))

    store.set_group_cfg(gid, cfg)
    await config_cmd.finish("设置成功")


@refresh_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await refresh_cmd.finish("该指令仅支持群聊")
    if not _is_owner_or_super(event):
        await refresh_cmd.finish("无权限，仅群主或超管可用")

    target_uid = _args_text(args)
    if not target_uid.isdigit():
        await refresh_cmd.finish("用法: 刷新 <QQ号>")

    store.clear_draw_and_claim_cooldown(_gid(event), target_uid)
    await refresh_cmd.finish("已刷新该用户抽卡与结婚冷却")


@rank_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        await rank_cmd.finish("该指令仅支持群聊")
    gid = _gid(event)

    heats = store.get_harem_heats(gid)
    if not heats:
        await rank_cmd.finish("暂无排行数据，先让大家执行一次“我的后宫”")

    top = sorted(heats.items(), key=lambda x: x[1], reverse=True)[:10]
    lines = []
    for idx, (uid, heat) in enumerate(top, start=1):
        name = uid
        try:
            info = await bot.get_group_member_info(group_id=event.group_id, user_id=int(uid))
            name = info.get("card") or info.get("nickname") or uid
        except Exception:
            pass
        lines.append(f"{idx}. {name}: {heat}")

    await rank_cmd.finish("\n".join(lines))


@reset_cmd.handle()
async def _(event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await reset_cmd.finish("该指令仅支持群聊")
    if not _is_owner_or_super(event):
        await reset_cmd.finish("无权限，仅群主或超管可用")

    if _args_text(args) != "确认":
        await reset_cmd.finish("该操作会清空本群婚姻数据(保留最爱)，请使用: 终极轮回 确认")

    gid = _gid(event)
    for uid in store.get_all_users(gid):
        store.cleanup_user_harem_keep_fav(gid, uid)
    store.clear_harem_heats(gid)

    await reset_cmd.finish("已执行终极轮回")
