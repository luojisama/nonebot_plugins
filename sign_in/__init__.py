import random
from datetime import datetime
from nonebot import on_command, get_driver, get_plugin_config
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent, PrivateMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from nonebot_plugin_htmlrender import html_to_pic
from pathlib import Path

from .config import Config, get_level_name, get_coin_level_name
from .utils import get_user_data, update_user_data, get_hitokoto, load_data

TEMPLATES_PATH = Path(__file__).parent / "templates"

__plugin_meta__ = PluginMetadata(
    name="签到系统",
    description="支持签到、好感度查询及设置的插件",
    usage="签到: 每日签到增加好感度\n查询好感度: 查看当前好感度等级\n设置好感度: 超级用户设置指定用户好感度",
    config=Config,
)

config = get_plugin_config(Config)
superusers = get_driver().config.superusers

# 匹配器定义
sign_in = on_command("签到", priority=5, block=True)
favorability_rank = on_command("好感度排行", aliases={"好感度榜", "排行榜"}, priority=5, block=True)
query_favorability = on_command("查询好感度", aliases={"好感度", "我的好感度", "个人信息"}, priority=5, block=True)
set_favorability = on_command("设置好感度", priority=5, block=True)
set_coins = on_command("设置金币", priority=5, block=True)
set_ap = on_command("设置行动值", priority=5, block=True)
take_action = on_command("行动", aliases={"进行行动", "互动"}, priority=5, block=True)
open_shop = on_command("商店", aliases={"绪山商店", "绪山百货"}, priority=5, block=True)
buy_item = on_command("购买", priority=5, block=True)
use_item = on_command("使用", aliases={"使用道具", "吃", "穿", "玩"}, priority=5, block=True)
view_inventory = on_command("背包", aliases={"我的背包", "仓库"}, priority=5, block=True)

# 《别当欧尼酱了》参考行动 - 基于时间段
ONIMAI_ACTIONS = {
    "late_night": [  # 00:00 - 05:00
        "和真寻酱在深夜偷偷联机打游戏（真寻酱：再玩一局，最后一局！）",
        "发现真寻酱在厨房偷吃深夜宵夜（真寻酱：呜哇！被发现了！）",
        "真寻酱在电脑前打瞌睡，头一点一点的（要把她抱到床上去吗？）",
        "陪真寻酱看深夜动画（真寻酱兴奋地讲解着剧情）",
        "真寻酱因为熬夜太晚，黑眼圈都出来了（被美波里训斥了呢）"
    ],
    "morning": [     # 05:00 - 09:00
        "试图叫醒赖床的真寻酱（真寻酱：再让我睡5分钟...就5分钟...）",
        "真寻酱睡眼惺忪地刷牙，头发乱蓬蓬的（像一只小猫一样呢）",
        "和真寻酱一起吃早餐（真寻酱似乎还没完全清醒）",
        "帮真寻酱梳头（真寻酱害羞地低下了头）",
        "真寻酱在玄关手忙脚乱地穿鞋（要迟到了要迟到了！）"
    ],
    "daytime": [     # 09:00 - 18:00
        "和真寻一起玩游戏（真寻酱似乎有点不服输呢）",
        "尝试美波里特制的“奇怪饮料”（感觉身体轻飘飘的...）",
        "被美波里强行换上女装（真寻酱：为什么我也要穿啊！）",
        "去商店街买可丽饼（真寻酱吃得满嘴都是奶油）",
        "辅导真寻酱写作业（真寻酱在草稿纸上画小人）",
        "和真寻酱一起买衣服（真寻酱在试衣间磨磨蹭蹭）",
        "一起喝下午茶（真寻酱对草莓蛋糕完全没有抵抗力）",
        "看真寻酱努力练习女子力的样子（真是个努力的好孩子呢）",
        "和真寻酱一起去游戏中心（真寻酱在抓娃娃机前大显身手）"
    ],
    "evening": [     # 18:00 - 24:00
        "一起去洗澡（真寻酱：哇啊啊不要看过来！）",
        "和真寻酱一起看晚间电视（真寻酱被电视里的内容逗得哈哈大笑）",
        "帮真寻酱吹头发（真寻酱舒服得快要睡着了）",
        "真寻酱穿上了宽松的睡衣（真寻酱：这个睡衣...是不是有点太大了？）",
        "和真寻酱商量明天的计划（真寻酱一脸期待的样子）",
        "和真寻酱一起睡午觉（真寻酱的睡颜真可爱呢）",
        "真寻酱在被窝里玩手机被抓住了（真寻酱：这就关机，这就关机！）"
    ]
}

def get_action_by_time() -> str:
    """根据当前时间获取行动描述"""
    hour = datetime.now().hour
    if 0 <= hour < 5:
        category = "late_night"
    elif 5 <= hour < 9:
        category = "morning"
    elif 9 <= hour < 18:
        category = "daytime"
    else:
        category = "evening"
    
    return random.choice(ONIMAI_ACTIONS[category])

# 商店物品定义
STORE_ITEMS = {
    "1": {
        "name": "美波里的特制药水", 
        "price": 80, 
        "desc": "让真寻变身的神秘药水",
        "effect_desc": "恢复 2-5 点行动值",
        "type": "ap",
        "value": (2, 5)
    },
    "2": {
        "name": "真寻的小裙子", 
        "price": 50, 
        "desc": "真寻酱最喜欢的可爱裙子",
        "effect_desc": "增加 5-10 点好感度",
        "type": "fav",
        "value": (5, 10)
    },
    "3": {
        "name": "真寻酱的薯片", 
        "price": 15, 
        "desc": "打游戏时的最佳伴侣",
        "effect_desc": "恢复 1 点行动值",
        "type": "ap",
        "value": (1, 1)
    },
    "4": {
        "name": "美波里的游戏机", 
        "price": 300, 
        "desc": "性能强劲的高级游戏机",
        "effect_desc": "增加 20-40 点好感度",
        "type": "fav",
        "value": (20, 40)
    },
    "5": {
        "name": "《别当欧尼酱了》漫画", 
        "price": 60, 
        "desc": "补充女子力的原作能量",
        "effect_desc": "恢复 3 点行动值",
        "type": "ap",
        "value": (3, 3)
    },
    "6": {
        "name": "真寻的防晒霜", 
        "price": 40, 
        "desc": "出门散步的防晒必备品",
        "effect_desc": "增加 5 点好感度",
        "type": "fav",
        "value": (5, 5)
    },
    "7": {
        "name": "真寻的运动衫",
        "price": 150,
        "desc": "真寻常穿的蓝色运动衫，很有安全感",
        "effect_desc": "增加 15-25 点好感度",
        "type": "fav",
        "value": (15, 25)
    },
    "8": {
        "name": "美波里的实验手册",
        "price": 200,
        "desc": "记载了各种奇怪药水的配方",
        "effect_desc": "恢复 5-10 点行动值",
        "type": "ap",
        "value": (5, 10)
    },
    "9": {
        "name": "特制便当",
        "price": 40,
        "desc": "美波里精心准备的爱心便当",
        "effect_desc": "恢复 2 点行动值",
        "type": "ap",
        "value": (2, 2)
    },
    "10": {
        "name": "游戏点卡",
        "price": 100,
        "desc": "可以用来购买真寻酱喜欢的游戏",
        "effect_desc": "增加 10-15 点好感度",
        "type": "fav",
        "value": (10, 15)
    },
    "11": {
        "name": "补签卡",
        "price": 5,
        "desc": "由美波里提供的神秘卡片，可以弥补错过的时光",
        "effect_desc": "增加 1 天累计签到并增加随机好感度",
        "type": "special",
        "value": "replenish"
    }
}

# 成就定义
ACHIEVEMENTS = [
    {"id": "beginner", "name": "初级欧尼酱", "days": 7, "reward_coins": 30, "desc": "累计签到 7 天"},
    {"id": "intermediate", "name": "合格欧尼酱", "days": 30, "reward_coins": 100, "desc": "累计签到 30 天"},
    {"id": "advanced", "name": "资深欧尼酱", "days": 100, "reward_coins": 500, "desc": "累计签到 100 天"},
    {"id": "master", "name": "最强欧尼酱", "days": 365, "reward_coins": 2000, "desc": "累计签到 365 天"}
]

async def render_sign_card(
    user_id: str, 
    user_name: str, 
    favorability: float, 
    inc: float = 0, 
    is_query: bool = False,
    title_override: str = None,
    action_points: int = 0,
    coins: int = 0,
    total_sign_ins: int = 0,
    first_sign_in: str = ""
) -> bytes:
    """渲染签到/好感度卡片"""
    level_name = get_level_name(favorability)
    coin_level_name = get_coin_level_name(coins)
    hitokoto_text, hitokoto_from = await get_hitokoto()
    avatar_url = f"http://q.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=640"
    
    # 渲染模板
    template_path = TEMPLATES_PATH / "sign_card.html"
    with open(template_path, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    # 替换变量
    title = title_override or ("好感度查询" if is_query else "今日签到")
    inc_display = "none" if is_query else "block"
    stat_width = "100%" if is_query else "auto"
    time_label = "查询时间" if is_query else "签到时间"
    
    replacements = {
        "{title}": title,
        "{avatar_url}": avatar_url,
        "{user_name}": user_name,
        "{inc}": f"{inc:.2f}",
        "{new_favorability}": f"{favorability:.2f}",
        "{level_name}": level_name,
        "{coin_level_name}": coin_level_name,
        "{hitokoto_text}": hitokoto_text,
        "{hitokoto_from}": hitokoto_from,
        "{sign_time}": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "{inc_display}": inc_display,
        "{stat_width}": stat_width,
        "{time_label}": time_label,
        "{action_points}": str(action_points),
        "{coins}": str(coins),
        "{total_sign_ins}": str(total_sign_ins),
        "{first_sign_in}": first_sign_in or "未知",
        "{ap_status}": "可行动" if action_points > 0 else "休息中",
        "{coin_status}": "可购买" if coins > 0 else "积累中"
    }
    
    for k, v in replacements.items():
        html_content = html_content.replace(k, v)
        
    return await html_to_pic(html_content, viewport={"width": 500, "height": 650})

async def render_rank_card(rank_data: list) -> bytes:
    """渲染排行榜卡片"""
    template_path = TEMPLATES_PATH / "rank_card.html"
    with open(template_path, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    items_html = ""
    for idx, user in enumerate(rank_data, 1):
        avatar_url = f"http://q.qlogo.cn/headimg_dl?dst_uin={user['user_id']}&spec=640"
        items_html += f'''
        <div class="rank-item">
            <div class="rank-num">{idx}</div>
            <img class="user-avatar" src="{avatar_url}" alt="avatar">
            <div class="user-info">
                <div class="user-name">{user['nickname']}</div>
                <div class="user-detail">ID: {user['user_id']}</div>
            </div>
            <div class="fav-info">
                <div class="fav-value">{user['favorability']:.1f}</div>
                <div class="level-badge">{user['level_name']}</div>
            </div>
        </div>
        '''
    
    html_content = html_content.replace("{rank_items}", items_html)
    html_content = html_content.replace("{update_time}", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    
    # 动态高度：基础高度 + 每个条目高度
    height = 150 + len(rank_data) * 80
    return await html_to_pic(html_content, viewport={"width": 500, "height": height})

async def render_shop_card(coins: int) -> bytes:
    """渲染商店卡片"""
    template_path = TEMPLATES_PATH / "shop_card.html"
    with open(template_path, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    items_html = ""
    for item_id, item in STORE_ITEMS.items():
        items_html += f'''
        <div class="item-card">
            <div class="item-info">
                <div class="item-header">
                    <span class="item-id">{item_id}</span>
                    <span class="item-name">{item["name"]}</span>
                </div>
                <div class="item-effect">✨ {item["effect_desc"]}</div>
                <div class="item-desc">{item["desc"]}</div>
            </div>
            <div class="item-price">💰 {item["price"]}</div>
        </div>
        '''
    
    html_content = html_content.replace("{coins}", str(coins))
    html_content = html_content.replace("{items_html}", items_html)
    
    return await html_to_pic(html_content, viewport={"width": 500, "height": 1200})

@favorability_rank.handle()
async def handle_rank(bot: Bot, event: MessageEvent):
    all_data = load_data()
    rank_list = []
    for user_id, user_data in all_data.items():
        # 过滤群聊数据和其他非用户数据
        if user_id.startswith("group_"):
            continue
            
        if user_data.get("is_perm_blacklisted"):
            continue
        fav = user_data.get("favorability", 0)
        if fav <= 0:
            continue
        rank_list.append({
            "user_id": user_id,
            "nickname": user_data.get("nickname", user_id),
            "favorability": fav,
            "level_name": get_level_name(fav)
        })
    
    rank_list.sort(key=lambda x: x["favorability"], reverse=True)
    rank_data = rank_list[:15]
    
    if not rank_data:
        await favorability_rank.finish("暂时没有排行数据~")
        
    pic = await render_rank_card(rank_data)
    await favorability_rank.finish(MessageSegment.image(pic))

@sign_in.handle()
async def handle_sign_in(bot: Bot, event: MessageEvent):
    user_id = event.get_user_id()
    user_data = get_user_data(user_id)
    
    # 黑名单检查
    if user_data.get("is_perm_blacklisted"):
        await sign_in.finish(MessageSegment.at(user_id) + " 你已被列入永久黑名单，无法使用签到功能。")
        
    user_name = event.sender.nickname or user_id
    today = datetime.now().strftime("%Y-%m-%d")
    
    # 检查是否跨天，如果是新的一天则重置行动值
    last_sign_in = user_data.get("last_sign_in", "")
    first_sign_in = user_data.get("first_sign_in", "")
    current_ap = user_data.get("action_points", 0)
    total_sign_ins = user_data.get("total_sign_ins", 0)
    
    # 记录第一次签到时间
    if not first_sign_in:
        first_sign_in = today
    
    if last_sign_in != today:
        current_ap = 0  # 每日清空行动值
    
    if last_sign_in == today:
        # 重复签到，提示并发送图片
        pic = await render_sign_card(
            user_id, user_name, user_data["favorability"], 
            is_query=True, title_override="今日已签到",
            action_points=current_ap,
            coins=user_data.get("coins", 0),
            total_sign_ins=total_sign_ins
        )
        await sign_in.finish(MessageSegment.at(user_id) + f" 你今天已经签到过了哦！\n首次签到: {first_sign_in}\n累计签到: {total_sign_ins}天\n当前行动值: {current_ap}\n商店金币: {user_data.get('coins', 0)}\n发送“行动”或“商店”看看吧~" + MessageSegment.image(pic))
    
    # 随机增加 0-1 的好感度
    inc = round(random.uniform(0, 1), 2)
    new_favorability = round(float(user_data["favorability"]) + inc, 2)
    
    # 奖励：行动值 +1（从0开始），金币 +0-5
    new_ap = 1  # 签到获得今日的 1 点行动值
    
    current_coins = user_data.get("coins", 0)
    coin_inc = random.randint(0, 5)
    
    # 更新总签到天数
    new_total_sign_ins = total_sign_ins + 1
    
    # 检查成就
    new_achievements = []
    earned_achievements = user_data.get("achievements", [])
    achievement_msg = ""
    
    for ach in ACHIEVEMENTS:
        if new_total_sign_ins >= ach["days"] and ach["id"] not in earned_achievements:
            earned_achievements.append(ach["id"])
            new_achievements.append(ach)
            coin_inc += ach["reward_coins"]
            achievement_msg += f"\n🏆 解锁成就：【{ach['name']}】奖励 {ach['reward_coins']} 金币！"
    
    new_coins = current_coins + coin_inc
    
    # 更新数据
    update_user_data(
        user_id, 
        favorability=new_favorability, 
        last_sign_in=today, 
        first_sign_in=first_sign_in,
        action_points=new_ap, 
        coins=new_coins,
        total_sign_ins=new_total_sign_ins,
        achievements=earned_achievements
    )
    
    # 渲染图片
    pic = await render_sign_card(
        user_id, user_name, new_favorability, 
        inc=inc, action_points=new_ap, coins=new_coins,
        total_sign_ins=new_total_sign_ins,
        first_sign_in=first_sign_in
    )
    
    await sign_in.finish(
        MessageSegment.at(user_id) + f" 签到成功！{achievement_msg}\n奖励：1点行动值 & {coin_inc}金币。\n累计签到: {new_total_sign_ins}天\n首次签到: {first_sign_in}\n好感度 +{inc:.2f}，当前总好感度: {new_favorability:.2f}\n当前金币: {new_coins}\n发送“商店”可以购买商品，“行动”可消耗行动值增加好感度~" + 
        MessageSegment.image(pic)
    )

@query_favorability.handle()
async def handle_query(bot: Bot, event: MessageEvent):
    user_id = event.get_user_id()
    user_data = get_user_data(user_id)
    
    # 黑名单检查
    if user_data.get("is_perm_blacklisted"):
        await query_favorability.finish(MessageSegment.at(user_id) + " 你已被列入永久黑名单，无法查询个人信息。")
        
    user_name = event.sender.nickname or user_id
    
    # 渲染图片
    pic = await render_sign_card(
        user_id, user_name, user_data["favorability"], 
        is_query=True, action_points=user_data.get("action_points", 0),
        coins=user_data.get("coins", 0),
        total_sign_ins=user_data.get("total_sign_ins", 0),
        first_sign_in=user_data.get("first_sign_in", "")
    )
    
    await query_favorability.finish(MessageSegment.image(pic))

@take_action.handle()
async def handle_action(bot: Bot, event: MessageEvent):
    user_id = event.get_user_id()
    user_data = get_user_data(user_id)
    
    # 黑名单检查
    if user_data.get("is_perm_blacklisted"):
        await take_action.finish(MessageSegment.at(user_id) + " 你已被列入永久黑名单，无法进行行动。")
        
    user_name = event.sender.nickname or user_id
    current_ap = user_data.get("action_points", 0)
    
    if current_ap <= 0:
        await take_action.finish(MessageSegment.at(user_id) + " 你的行动值不足哦，每日签到可以获得 1 点行动值！")
    
    # 随机行动描述 (基于当前时间)
    action_desc = get_action_by_time()
    # 随机增加 0-1 的好感度
    inc = round(random.uniform(0, 1), 2)
    new_favorability = round(float(user_data["favorability"]) + inc, 2)
    new_ap = current_ap - 1
    
    # 更新数据
    update_user_data(user_id, favorability=new_favorability, action_points=new_ap)
    
    # 渲染卡片
    pic = await render_sign_card(
        user_id, user_name, new_favorability, 
        inc=inc, title_override="进行行动",
        action_points=new_ap,
        coins=user_data.get("coins", 0),
        total_sign_ins=user_data.get("total_sign_ins", 0),
        first_sign_in=user_data.get("first_sign_in", "")
    )
    
    await take_action.finish(
        MessageSegment.at(user_id) + f" 执行行动：{action_desc}\n好感度 +{inc:.2f}！\n当前好感度: {new_favorability:.2f}\n剩余行动值: {new_ap}" + 
        MessageSegment.image(pic)
    )

@open_shop.handle()
async def handle_shop(bot: Bot, event: MessageEvent):
    user_id = event.get_user_id()
    user_data = get_user_data(user_id)
    
    # 黑名单检查
    if user_data.get("is_perm_blacklisted"):
        await open_shop.finish(MessageSegment.at(user_id) + " 你已被列入永久黑名单，禁止进入商店。")
        
    coins = user_data.get('coins', 0)
    
    # 渲染图片
    pic = await render_shop_card(coins)
    
    await open_shop.finish(MessageSegment.image(pic))

@buy_item.handle()
async def handle_buy(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = event.get_user_id()
    user_data = get_user_data(user_id)
    
    # 黑名单检查
    if user_data.get("is_perm_blacklisted"):
        await buy_item.finish(MessageSegment.at(user_id) + " 你已被列入永久黑名单，禁止购买商品。")
        
    item_id = args.extract_plain_text().strip()
    
    if not item_id:
        await buy_item.finish("请输入要购买的商品编号哦，例如：购买 1")
    
    if item_id not in STORE_ITEMS:
        await buy_item.finish("这个商品编号好像不存在呢...")
        
    item = STORE_ITEMS[item_id]
    
    if user_data.get("coins", 0) < item["price"]:
        await buy_item.finish(f"金币不足哦！购买 {item['name']} 需要 {item['price']} 金币，你只有 {user_data.get('coins', 0)} 金币。")
        
    # 扣钱并添加进背包
    new_coins = user_data["coins"] - item["price"]
    inventory = user_data.get("inventory", [])
    inventory.append(item["name"])
    
    update_user_data(user_id, coins=new_coins, inventory=inventory)
    
    await buy_item.finish(f"🛍️ 购买成功！你获得了【{item['name']}】。\n效果: {item['effect_desc']}\n发送“使用 {item['name']}”即可生效哦！")

@use_item.handle()
async def handle_use(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = event.get_user_id()
    user_data = get_user_data(user_id)
    
    # 黑名单检查
    if user_data.get("is_perm_blacklisted"):
        await use_item.finish(MessageSegment.at(user_id) + " 你已被列入永久黑名单，禁止使用道具。")
        
    item_name = args.extract_plain_text().strip()
    
    if not item_name:
        await use_item.finish("你想使用哪个道具呢？请在指令后面加上道具名称哦，例如：使用 真寻酱的薯片")
        
    inventory = user_data.get("inventory", [])
    
    if item_name not in inventory:
        await use_item.finish(f"你的背包里好像没有【{item_name}】呢...")
        
    # 查找道具配置
    target_item = None
    for item in STORE_ITEMS.values():
        if item["name"] == item_name:
            target_item = item
            break
            
    if not target_item:
        await use_item.finish("这个道具似乎无法被直接使用呢...")
        
    # 消耗道具
    inventory.remove(item_name)
    
    # 执行效果
    msg = f"✨ 使用了【{item_name}】！\n"
    
    inc = 0.0
    new_fav = user_data.get("favorability", 0.0)
    new_ap = user_data.get("action_points", 0)
    new_total_sign_ins = user_data.get("total_sign_ins", 0)
    new_coins = user_data.get("coins", 0)
    earned_achievements = user_data.get("achievements", [])
    
    if target_item["type"] == "fav":
        inc = round(random.uniform(target_item["value"][0], target_item["value"][1]), 2)
        new_fav = round(float(new_fav) + inc, 2)
        msg += f"好感度增加了 {inc:.2f} 点！当前好感度: {new_fav:.2f}"
    elif target_item["type"] == "ap":
        inc = random.randint(target_item["value"][0], target_item["value"][1])
        new_ap += inc
        msg += f"行动值恢复了 {inc} 点！当前行动值: {new_ap}"
    elif target_item["type"] == "special" and target_item["value"] == "replenish":
        # 补签增加 1 天累计签到和随机好感度
        new_total_sign_ins += 1
        inc = round(random.uniform(0.5, 1.5), 2)  # 补签给的好感度稍微高一点点
        new_fav = round(float(new_fav) + inc, 2)
        msg += f"补签成功！累计签到天数增加 1 天，好感度增加了 {inc:.2f} 点。\n当前累计: {new_total_sign_ins} 天，总好感度: {new_fav:.2f}"
        
        # 补签可能触发成就
        for ach in ACHIEVEMENTS:
            if new_total_sign_ins >= ach["days"] and ach["id"] not in earned_achievements:
                earned_achievements.append(ach["id"])
                new_coins += ach["reward_coins"]
                msg += f"\n🏆 解锁成就：【{ach['name']}】奖励 {ach['reward_coins']} 金币！"
        
    # 更新数据
    update_user_data(
        user_id, 
        favorability=new_fav, 
        action_points=new_ap, 
        total_sign_ins=new_total_sign_ins,
        coins=new_coins,
        inventory=inventory,
        achievements=earned_achievements
    )
    
    # 渲染新的卡片
    pic = await render_sign_card(
        user_id, event.sender.nickname or user_id, new_fav, 
        inc=inc, title_override="使用道具", 
        action_points=new_ap, 
        coins=new_coins,
        total_sign_ins=new_total_sign_ins,
        first_sign_in=user_data.get("first_sign_in", "")
    )
    
    await use_item.finish(MessageSegment.at(user_id) + msg + MessageSegment.image(pic))

@view_inventory.handle()
async def handle_inventory(bot: Bot, event: MessageEvent):
    user_id = event.get_user_id()
    user_data = get_user_data(user_id)
    
    # 黑名单检查
    if user_data.get("is_perm_blacklisted"):
        await view_inventory.finish(MessageSegment.at(user_id) + " 你已被列入永久黑名单，无法查看背包。")
        
    inventory = user_data.get("inventory", [])
    
    if not inventory:
        await view_inventory.finish("你的背包里空空如也呢，快去签到领金币买点东西吧！")
        
    # 统计数量
    item_counts = {}
    for item in inventory:
        item_counts[item] = item_counts.get(item, 0) + 1
        
    msg = f"🎒 {event.sender.nickname or user_id} 的背包\n"
    msg += "--------------------------\n"
    for item, count in item_counts.items():
        # 查找效果描述
        eff = "未知效果"
        for si in STORE_ITEMS.values():
            if si["name"] == item:
                eff = si["effect_desc"]
                break
        msg += f"• {item} x{count}\n"
        msg += f"  └ 效果: {eff}\n"
    msg += "--------------------------\n"
    msg += f"当前金币: {user_data.get('coins', 0)}\n"
    msg += "发送“使用 [道具名称]”即可使用道具哦~"
    
    await view_inventory.finish(msg)

@set_favorability.handle()
async def handle_set(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if event.get_user_id() not in superusers:
        await set_favorability.finish("权限不足，仅限超级用户使用。")
    
    arg_list = args.extract_plain_text().split()
    if len(arg_list) < 2:
        await set_favorability.finish("参数错误。用法: 设置好感度 [用户QQ] [数值]")
        return
    
    target_user_id = arg_list[0]
    try:
        new_val = round(float(arg_list[1]), 2)
    except ValueError:
        await set_favorability.finish("数值格式不正确。")
        return
    
    update_user_data(target_user_id, favorability=new_val)
    await set_favorability.finish(f"已成功将用户 {target_user_id} 的好感度设置为 {new_val:.2f}")

@set_coins.handle()
async def handle_set_coins(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if event.get_user_id() not in superusers:
        await set_coins.finish("权限不足，仅限超级用户使用。")
    
    arg_list = args.extract_plain_text().split()
    if len(arg_list) < 2:
        await set_coins.finish("参数错误。用法: 设置金币 [用户QQ] [数值]")
        return
    
    target_user_id = arg_list[0]
    try:
        new_val = int(arg_list[1])
    except ValueError:
        await set_coins.finish("金币数值必须是整数哦。")
        return
    
    update_user_data(target_user_id, coins=new_val)
    await set_coins.finish(f"已成功将用户 {target_user_id} 的金币设置为 {new_val}")

@set_ap.handle()
async def handle_set_ap(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if event.get_user_id() not in superusers:
        await set_ap.finish("权限不足，仅限超级用户使用。")
    
    arg_list = args.extract_plain_text().split()
    if len(arg_list) < 2:
        await set_ap.finish("参数错误。用法: 设置行动值 [用户QQ] [数值]")
        return
    
    target_user_id = arg_list[0]
    try:
        new_val = int(arg_list[1])
    except ValueError:
        await set_ap.finish("行动值必须是整数哦。")
        return
    
    update_user_data(target_user_id, action_points=new_val)
    await set_ap.finish(f"已成功将用户 {target_user_id} 的行动值设置为 {new_val}")
