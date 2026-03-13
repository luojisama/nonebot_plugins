from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageSegment, Message
from nonebot.params import CommandArg
from nonebot.matcher import Matcher
import random
import asyncio
from typing import List, Dict, Optional

from .data_source import get_cards
from .render import render_gacha_result

# Commands
ll_draw_one = on_command("lovelive单抽", aliases={"ll单抽", "LL单抽"}, priority=5, block=True)
ll_draw_ten = on_command("lovelive十连", aliases={"ll十连", "LL十连"}, priority=5, block=True)

# Group Aliases
GROUP_ALIASES = {
    "μ's": "μ's", "muse": "μ's", "u's": "μ's", "缪斯": "μ's",
    "aqours": "Aqours", "水团": "Aqours",
    "nijigasaki": "Nijigasaki High Scho", "niji": "Nijigasaki High Scho", "虹咲": "Nijigasaki High Scho", "虹团": "Nijigasaki High Scho",
    "liella": "Liella!", "liella!": "Liella!", "星团": "Liella!"
}

def parse_group(text: str) -> Optional[str]:
    if not text:
        return None
    text = text.strip().lower()
    return GROUP_ALIASES.get(text)

# Probabilities
PROB_UR = 0.01
PROB_SSR = 0.04
PROB_SR = 0.15
# R is remaining

def get_rarity(guaranteed_sr: bool = False) -> str:
    rand = random.random()
    if guaranteed_sr:
        # Re-normalize for SR+ (UR: 1, SSR: 4, SR: 95) -> Total 100
        # Wait, if normal UR is 1%, SSR 4%, SR 15%, R 80%.
        # Guaranteed SR means R is removed.
        # Ratio: UR:SSR:SR = 1:4:15. Sum = 20.
        # Probabilities: UR = 1/20 = 5%, SSR = 4/20 = 20%, SR = 15/20 = 75%.
        # Actually in many games, guaranteed SR usually just upgrades R to SR, or uses a specific pool.
        # Let's use standard SIF rates for guaranteed slot if known, otherwise 1/4/95 is safer or 5/20/75.
        # Let's go with: UR 1%, SSR 4%, SR 95% (Conservative) or stick to ratio.
        # If we stick to ratio 1:4:15 => UR 5%, SSR 20%, SR 75%. That's generous.
        # Let's stick to base probabilities but floor at SR.
        # If rand < 0.01 -> UR
        # If rand < 0.05 -> SSR
        # Else -> SR
        if rand < 0.01: return "UR"
        elif rand < 0.05: return "SSR"
        else: return "SR"
    else:
        if rand < PROB_UR: return "UR"
        elif rand < PROB_UR + PROB_SSR: return "SSR"
        elif rand < PROB_UR + PROB_SSR + PROB_SR: return "SR"
        else: return "R"

@ll_draw_one.handle()
async def handle_draw_one(matcher: Matcher, args: Message = CommandArg()):
    group_text = args.extract_plain_text().strip()
    group = parse_group(group_text)
    
    if group_text and not group:
        await matcher.finish("未找到该团队，目前支持：缪斯、水团、虹咲、星团")

    rarity = get_rarity()
    cards = await get_cards(rarity, 1, group=group)
    
    if not cards:
        await matcher.finish("抽卡失败，无法获取数据，请稍后再试。")
        
    # 单抽直接发送图片
    card = cards[0]
    await matcher.finish(MessageSegment.image(card['image']))

@ll_draw_ten.handle()
async def handle_draw_ten(matcher: Matcher, args: Message = CommandArg()):
    group_text = args.extract_plain_text().strip()
    group = parse_group(group_text)
    
    if group_text and not group:
        await matcher.finish("未找到该团队，目前支持：缪斯、水团、虹咲、星团")

    await matcher.send(f"正在进行LoveLive{' ' + group_text if group_text else ''}十连...")
    
    rarities = []
    # 9 normal pulls
    for _ in range(9):
        rarities.append(get_rarity(guaranteed_sr=False))
    # 1 guaranteed SR+
    rarities.append(get_rarity(guaranteed_sr=True))
    
    # Count rarities to batch requests
    rarity_counts = {}
    for r in rarities:
        rarity_counts[r] = rarity_counts.get(r, 0) + 1
        
    tasks = []
    for r, count in rarity_counts.items():
        tasks.append(get_cards(r, count, group=group))
        
    results = await asyncio.gather(*tasks)
    
    all_cards = []
    for batch in results:
        all_cards.extend(batch)
        
    # Shuffle to mix the guaranteed card
    random.shuffle(all_cards)
    
    if not all_cards:
        await matcher.finish("抽卡失败，无法获取数据，请稍后再试。")
        
    try:
        img_bytes = await render_gacha_result(all_cards)
    except Exception as e:
        await matcher.finish(f"图片生成失败: {e}")
    
    await matcher.finish(MessageSegment.image(img_bytes))
