import random
import time
import uuid
import asyncio
from datetime import datetime
from nonebot import on_command, get_driver, get_plugin_config, require, get_bot, logger
from nonebot.permission import SUPERUSER
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent, PrivateMessageEvent, Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from nonebot_plugin_htmlrender import html_to_pic
from pathlib import Path

from .config import Config, get_level_name, get_coin_level_name
from .utils import get_user_data, update_user_data, get_hitokoto, load_data, save_data

TEMPLATES_PATH = Path(__file__).parent / "templates"

__plugin_meta__ = PluginMetadata(
    name="签到系统",
    description="支持签到、好感度查询及设置的插件",
    usage="""基础功能：
签到: 每日签到获取金币/好感度/行动值
查询好感度: 查看个人信息卡片
好感度排行: 查看群内好感度排名
商店: 购买道具
背包: 查看持有道具
使用 <道具名>: 使用道具
打工: 消耗打工次数赚取金币
行动: 消耗行动值增加好感度
发红包 <金额> <数量>: 发送拼手气红包
发世界红包 <金额> <数量>: 发送全服红包
抢红包: 领取红包
偷窃 <@某人>: 消耗1行动值尝试偷窃金币（慎用！）
银行 [存/取] <金额>: 存取金币赚利息
改名 <新称号>: 使用改名卡修改称号
转账 <@某人/QQ号> <金额>: 给群友转账
富豪榜: 查看金币排行榜
决斗 <@某人>: 消耗2行动值发起PK
掷骰子 [赌注]: 与真寻比大小赚金币
买彩票: 20金币一张，次日开奖
彩票池: 查看彩票奖池信息""",
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
do_work = on_command("打工", aliases={"工", "上班"}, priority=5, block=True)

# 新增功能
send_red_packet = on_command("发红包", priority=5, block=True)
send_global_red_packet = on_command("发世界红包", priority=5, block=True)
grab_red_packet = on_command("抢红包", priority=5, block=True)
rob_user = on_command("偷窃", aliases={"抢劫", "偷"}, priority=5, block=True)
bank_cmd = on_command("银行", aliases={"真寻银行"}, priority=5, block=True)
bank_history_cmd = on_command("银行明细", aliases={"账单", "流水", "余额明细"}, priority=5, block=True)
rename_card = on_command("改名", aliases={"修改称号", "设置称号"}, priority=5, block=True)

from datetime import datetime, timedelta

# ... (rest of imports)

import json
from pathlib import Path

# 红包数据路径
RED_PACKET_DATA_FILE = Path("data/sign_in/active_red_packets.json")

def load_red_packets():
    """加载活跃红包数据"""
    if not RED_PACKET_DATA_FILE.exists():
        return {}
    try:
        with open(RED_PACKET_DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # 转换时间字符串为 datetime 对象
            for pid, packet in data.items():
                if "create_time" in packet:
                    try:
                        packet["create_time"] = datetime.fromisoformat(packet["create_time"])
                    except:
                        packet["create_time"] = datetime.now()
            return data
    except Exception as e:
        logger.error(f"加载红包数据失败: {e}")
        return {}

def save_red_packets():
    """保存活跃红包数据"""
    RED_PACKET_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        # 复制数据以避免修改原始数据
        data_to_save = {}
        for pid, packet in active_red_packets.items():
            packet_copy = packet.copy()
            # 转换 datetime 对象为 ISO 格式字符串
            if "create_time" in packet_copy and isinstance(packet_copy["create_time"], datetime):
                packet_copy["create_time"] = packet_copy["create_time"].isoformat()
            data_to_save[pid] = packet_copy
            
        with open(RED_PACKET_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"保存红包数据失败: {e}")

# 全局变量
# red_packets: {uuid: {"sender_id": str, "sender_name": str, "total_amount": int, "total_count": int, "remain_amount": int, "remain_count": int, "grabbed": {user_id: amount}, "group_id": str, "remark": str, "create_time": datetime, "exclusive_user": str}}
active_red_packets = load_red_packets()

# 红包黑名单数据路径
BLACKLIST_FILE = Path("data/sign_in/red_packet_blacklist.json")

def load_blacklist():
    if not BLACKLIST_FILE.exists():
        return []
    try:
        with open(BLACKLIST_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return []

def save_blacklist(blacklist):
    BLACKLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(BLACKLIST_FILE, 'w', encoding='utf-8') as f:
        json.dump(blacklist, f, ensure_ascii=False, indent=4)

# 红包黑名单群组
GLOBAL_RED_PACKET_BLACKLIST = load_blacklist()

# 红包黑名单指令
add_red_packet_blacklist = on_command("添加红包黑名单", priority=5, block=True)
remove_red_packet_blacklist = on_command("移除红包黑名单", priority=5, block=True)
view_red_packet_blacklist = on_command("红包黑名单", priority=5, block=True)

# 新增功能指令
transfer_coins = on_command("转账", priority=5, block=True)
wealth_rank = on_command("富豪榜", aliases={"金币榜", "富豪排行"}, priority=5, block=True)
duel_user = on_command("决斗", aliases={"PK", "挑战"}, priority=5, block=True)
play_dice = on_command("掷骰子", aliases={"摇骰子", "比大小"}, priority=5, block=True)
buy_lottery = on_command("买彩票", priority=5, block=True)
lottery_info = on_command("彩票池", aliases={"奖池"}, priority=5, block=True)
bank_rank = on_command("银行排行榜", aliases={"存款榜", "银行榜"}, priority=5, block=True, permission=SUPERUSER)

# 运势定义
FORTUNES = {
    "大吉": {"work_rate": 1.3, "steal_rate": 0.15, "desc": "运势极佳，诸事顺遂！"},
    "中吉": {"work_rate": 1.2, "steal_rate": 0.08, "desc": "运势不错，是个好兆头。"},
    "小吉": {"work_rate": 1.1, "steal_rate": 0.03, "desc": "平平淡淡才是真。"},
    "吉": {"work_rate": 1.0, "steal_rate": 0.0, "desc": "不好不坏，努力会有回报。"},
    "末吉": {"work_rate": 0.95, "steal_rate": -0.03, "desc": "运气稍差，小心行事。"},
    "凶": {"work_rate": 0.9, "steal_rate": -0.08, "desc": "诸事不宜，最好在家休息。"},
    "大凶": {"work_rate": 0.8, "steal_rate": -0.15, "desc": "...今天还是别出门了吧。"}
}

# 彩票数据路径
LOTTERY_DATA_FILE = Path("data/sign_in/lottery_data.json")
LOTTERY_TICKET_PRICE = 20
# Dynamic payout ratios by participant count.
# The sum is < 1.0 to keep a sink and avoid long-term inflation.
LOTTERY_PAYOUT_RATES = {
    1: [0.90],          # 1 winner
    2: [0.60, 0.25],    # 2 winners
    3: [0.50, 0.25, 0.10],  # 3 winners
}
# Apply a recycle tax when rollover pool is too large and participation is low.
LOTTERY_POOL_SOFT_CAP = 3000
LOTTERY_POOL_LOW_PARTICIPANT_THRESHOLD = 8
LOTTERY_POOL_RECYCLE_TAX_RATE = 0.25
# Prevent very low participation from cashing out an oversized historical pool at once.
LOTTERY_MAX_POT_MULTIPLIER_PER_PARTICIPANT = 12

def load_lottery_data():
    if not LOTTERY_DATA_FILE.exists():
        return {"pool": 0, "participants": [], "base_pool_value": 500, "last_count": 0}
    try:
        with open(LOTTERY_DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Compatibility defaults
            data.setdefault("pool", 0)
            data.setdefault("participants", [])
            data.setdefault("base_pool_value", 500)
            data.setdefault("last_count", 0)
            return data
    except:
        return {"pool": 0, "participants": [], "base_pool_value": 500, "last_count": 0}

def save_lottery_data(data):
    LOTTERY_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOTTERY_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# 隐藏成就定义
HIDDEN_ACHIEVEMENTS = [
    {"id": "philanthropist", "name": "慈善家", "condition": lambda u: u["achievement_progress"]["red_packet_total"] >= 10000, "reward_coins": 1000, "desc": "累计发红包超过 10,000 金币"},
    {"id": "phantom_thief", "name": "怪盗基德", "condition": lambda u: u["achievement_progress"]["steal_success"] >= 50, "reward_coins": 2000, "desc": "累计成功偷窃 50 次"},
    {"id": "bad_luck", "name": "非酋", "condition": lambda u: u["achievement_progress"]["consecutive_fails"] >= 3, "reward_coins": 500, "desc": "连续 3 次打工收益最低或偷窃失败"}
]

async def check_hidden_achievements(bot: Bot, event: MessageEvent, user_id: str):
    """检查并发放隐藏成就"""
    user_data = get_user_data(user_id)
    achievements = user_data.get("achievements", [])
    
    new_achievements = []
    for ach in HIDDEN_ACHIEVEMENTS:
        if ach["id"] not in achievements:
            try:
                if ach["condition"](user_data):
                    achievements.append(ach["id"])
                    new_achievements.append(ach)
                    # 发放奖励
                    user_data["coins"] += ach["reward_coins"]
                    update_user_data(user_id, reason="隐藏成就奖励", coins=user_data["coins"], achievements=achievements)
            except Exception as e:
                logger.error(f"检查成就 {ach['id']} 失败: {e}")
                
    if new_achievements:
        msg = "🎉 恭喜达成隐藏成就！\n"
        for ach in new_achievements:
            msg += f"【{ach['name']}】: {ach['desc']} (奖励 {ach['reward_coins']} 金币)\n"
        await bot.send(event, msg)

@add_red_packet_blacklist.handle()
async def handle_add_blacklist(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if not str(event.user_id) in superusers:
        await add_red_packet_blacklist.finish("只有管理员才能操作哦！")
        
    group_id = args.extract_plain_text().strip()
    if not group_id:
        if isinstance(event, GroupMessageEvent):
            group_id = str(event.group_id)
        else:
            await add_red_packet_blacklist.finish("请输入群号！")
            
    if group_id in GLOBAL_RED_PACKET_BLACKLIST:
        await add_red_packet_blacklist.finish("这个群已经在黑名单里啦！")
        
    GLOBAL_RED_PACKET_BLACKLIST.append(group_id)
    save_blacklist(GLOBAL_RED_PACKET_BLACKLIST)
    await add_red_packet_blacklist.finish(f"已将群 {group_id} 加入红包黑名单！")

@remove_red_packet_blacklist.handle()
async def handle_remove_blacklist(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if not str(event.user_id) in superusers:
        await remove_red_packet_blacklist.finish("只有管理员才能操作哦！")
        
    group_id = args.extract_plain_text().strip()
    if not group_id:
        if isinstance(event, GroupMessageEvent):
            group_id = str(event.group_id)
        else:
            await remove_red_packet_blacklist.finish("请输入群号！")
            
    if group_id not in GLOBAL_RED_PACKET_BLACKLIST:
        await remove_red_packet_blacklist.finish("这个群不在黑名单里哦！")
        
    GLOBAL_RED_PACKET_BLACKLIST.remove(group_id)
    save_blacklist(GLOBAL_RED_PACKET_BLACKLIST)
    await remove_red_packet_blacklist.finish(f"已将群 {group_id} 移出红包黑名单！")

@view_red_packet_blacklist.handle()
async def handle_view_blacklist(bot: Bot, event: MessageEvent):
    if not str(event.user_id) in superusers:
        await view_red_packet_blacklist.finish("只有管理员才能操作哦！")
        
    if not GLOBAL_RED_PACKET_BLACKLIST:
        await view_red_packet_blacklist.finish("当前没有红包黑名单群组。")
        
    msg = "当前红包黑名单群组：\n" + "\n".join(GLOBAL_RED_PACKET_BLACKLIST)
    await view_red_packet_blacklist.finish(msg)


@scheduler.scheduled_job("cron", hour=0, minute=0, id="sign_in_daily_reset")
async def reset_daily_works():
    """每日重置打工次数"""
    try:
        data = load_data()
        count = 0
        for user_id in data:
            if not user_id.startswith("group_"):
                # 重置打工次数为 1 (默认值)
                data[user_id]["remaining_works"] = 1
                count += 1
        save_data(data)
        logger.info(f"已重置 {count} 名用户的每日打工次数")
    except Exception as e:
        logger.error(f"重置每日打工次数失败: {e}")

@scheduler.scheduled_job("interval", minutes=30)
async def check_expired_red_packets():
    """定期检查过期红包并退回"""
    now = datetime.now()
    expired_packets = []
    
    # 转换为list以避免迭代时修改字典大小
    for pid, packet in list(active_red_packets.items()):
        # 默认24小时过期
        create_time = packet.get("create_time")
        if not create_time:
            # 兼容旧数据，如果没有create_time，视为不过期或补上当前时间
            packet["create_time"] = now
            continue
            
        if now - create_time > timedelta(hours=24):
            expired_packets.append(pid)
            
    if expired_packets:
        for pid in expired_packets:
            if pid in active_red_packets:
                packet = active_red_packets[pid]
                sender_id = packet["sender_id"]
                remain_amount = packet["remain_amount"]
                
                if remain_amount > 0:
                    user_data = get_user_data(sender_id)
                    update_user_data(sender_id, reason="红包退回", coins=user_data.get("coins", 0) + remain_amount)
                    # 这里可以选择是否发通知，考虑到可能打扰用户，暂时不发，或者只记录日志
                    # await bot.send_private_msg(user_id=int(sender_id), message=f"你的红包已过期，退回 {remain_amount} 金币。")
                    
                del active_red_packets[pid]
                
        save_red_packets()
        logger.info(f"已清理 {len(expired_packets)} 个过期红包")

# 启动时清理一次
try:
    # 延迟10秒执行，确保scheduler已启动
    scheduler.add_job(check_expired_red_packets, "date", run_date=datetime.now() + timedelta(seconds=10))
except Exception:
    # 忽略可能的scheduler未启动错误
    pass

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

ONIMAI_WORKS = [
    "在波特罗的咖啡店帮忙（真寻酱：欢迎光临！诶，不要一直盯着我看啦...）",
    "帮美波里整理实验数据（虽然完全看不懂，但还是努力帮忙了）",
    "作为美波里的素描模特（真寻酱：这、这种姿势太羞耻了！）",
    "帮忙打扫家里的卫生（真寻酱拿着鸡毛掸子到处跑）",
    "去便利店跑腿买东西（真寻酱：嘿嘿，顺便给自己买了布丁）",
    "在学校帮忙整理图书（真寻酱踮起脚尖放书的样子真可爱）",
    "帮美波里测试新发明的...游戏机？（真寻酱玩得不亦乐乎）",
    "帮忙照顾邻居家的宠物（真寻酱被大狗舔得满脸口水）",
    "在祭典摊位上帮忙（真寻酱穿着浴衣招揽客人）",
    "帮忙修剪家里的花草（真寻酱脸上沾到了泥土）"
]

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
        "name": "美波里的奇怪药水", 
        "price": 80, 
        "desc": "美波里研制的不知名液体，喝了会有神奇的效果？",
        "effect_desc": "增加 1-2 次打工次数",
        "type": "work",
        "value": (1, 2)
    },
    "2": {
        "name": "真寻的羞耻小裙子", 
        "price": 50, 
        "desc": "虽然很羞耻，但是穿上会被夸可爱...",
        "effect_desc": "增加 5-10 点好感度",
        "type": "fav",
        "value": (5, 10)
    },
    "3": {
        "name": "深夜的罪恶薯片", 
        "price": 15, 
        "desc": "半夜偷偷吃零食最棒了！",
        "effect_desc": "恢复 1 点行动值",
        "type": "ap",
        "value": (1, 1)
    },
    "4": {
        "name": "最新款游戏机", 
        "price": 300, 
        "desc": "为了玩最新的3A大作，必须入手！",
        "effect_desc": "增加 20-40 点好感度",
        "type": "fav",
        "value": (20, 40)
    },
    "5": {
        "name": "乃爱借给你的漫画", 
        "price": 60, 
        "desc": "乃爱强烈安利的少女漫画，意外地好看？",
        "effect_desc": "恢复 3 点行动值",
        "type": "ap",
        "value": (3, 3)
    },
    "6": {
        "name": "出门必备防晒霜", 
        "price": 40, 
        "desc": "不想被晒黑的话就涂上吧（虽然不想出门）",
        "effect_desc": "增加 5 点好感度",
        "type": "fav",
        "value": (5, 5)
    },
    "7": {
        "name": "安心感满满的运动衫",
        "price": 150,
        "desc": "还是穿这件最自在...有哥哥的味道？",
        "effect_desc": "增加 15-25 点好感度",
        "type": "fav",
        "value": (15, 25)
    },
    "8": {
        "name": "看不懂的实验报告",
        "price": 200,
        "desc": "帮美波里整理这些乱七八糟的数据...",
        "effect_desc": "增加 2-4 次打工次数",
        "type": "work",
        "value": (2, 4)
    },
    "9": {
        "name": "美波里的爱心便当",
        "price": 40,
        "desc": "营养均衡的美味便当，包含了妹妹的爱",
        "effect_desc": "恢复 2 点行动值",
        "type": "ap",
        "value": (2, 2)
    },
    "10": {
        "name": "氪金点数",
        "price": 100,
        "desc": "再抽一发！这次一定是SSR！",
        "effect_desc": "增加 10-15 点好感度",
        "type": "fav",
        "value": (10, 15)
    },
    "11": {
        "name": "时光倒流药水", 
        "price": 5, 
        "desc": "如果能回到那天...把漏掉的签到补上就好了",
        "effect_desc": "增加 1 天累计签到并增加 0-1 点好感度 (若未漏签则无效果)",
        "type": "special",
        "value": "replenish"
    },
    "12": {
        "name": "真寻的姓名贴", 
        "price": 300, 
        "desc": "贴上这个，大家就知道该怎么称呼你了",
        "effect_desc": "永久修改称号",
        "type": "special",
        "value": "rename"
    },
    "13": {
        "name": "防盗盾",
        "price": 10,
        "desc": "坚固的盾牌，放在背包里就能生效",
        "effect_desc": "被动道具，抵挡一次偷窃（自动消耗）",
        "type": "special",
        "value": "shield"
    }
}

# 成就定义
ACHIEVEMENTS = [
    {"id": "beginner", "name": "初级哥哥", "days": 7, "reward_coins": 30, "desc": "累计签到 7 天"},
    {"id": "intermediate", "name": "合格哥哥", "days": 30, "reward_coins": 100, "desc": "累计签到 30 天"},
    {"id": "advanced", "name": "资深哥哥", "days": 100, "reward_coins": 500, "desc": "累计签到 100 天"},
    {"id": "master", "name": "最强哥哥", "days": 365, "reward_coins": 2000, "desc": "累计签到 365 天"}
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
    first_sign_in: str = "",
    remaining_works: int = 0,
    custom_title: str = ""
) -> bytes:
    """渲染签到/好感度卡片"""
    level_name = custom_title if custom_title else get_level_name(favorability)
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
        "{coin_status}": "可购买" if coins > 0 else "积累中",
        "{remaining_works}": str(remaining_works),
        "{work_status}": "可打工" if remaining_works > 0 else "休息中"
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
            <div class="coin-info">
                <div class="coin-value">💰 {user['coins']}</div>
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

async def render_inventory_card(user_id: str, user_name: str, coins: int, inventory: list) -> bytes:
    """渲染背包卡片"""
    template_path = TEMPLATES_PATH / "inventory_card.html"
    with open(template_path, 'r', encoding='utf-8') as f:
        html_content = f.read()
        
    # 统计数量
    item_counts = {}
    for item in inventory:
        item_counts[item] = item_counts.get(item, 0) + 1
        
    items_html = ""
    if not item_counts:
        items_html = '<div class="empty-tips">背包里空空如也呢...<br>去美波里的商店买点东西吧？</div>'
    else:
        for item_name, count in item_counts.items():
            # 查找详细信息
            item_info = None
            for si in STORE_ITEMS.values():
                if si["name"] == item_name:
                    item_info = si
                    break
            
            desc = "未知物品"
            effect = "未知效果"
            if item_info:
                desc = item_info["desc"]
                effect = item_info["effect_desc"]
                
            items_html += f'''
            <div class="item-card">
                <div class="item-info">
                    <div class="item-header">
                        <span class="item-name">{item_name}</span>
                        <span class="item-count">x{count}</span>
                    </div>
                    <div class="item-effect">✨ {effect}</div>
                    <div class="item-desc">{desc}</div>
                </div>
            </div>
            '''
            
    avatar_url = f"http://q.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=640"
    
    replacements = {
        "{avatar_url}": avatar_url,
        "{user_name}": user_name,
        "{coins}": str(coins),
        "{items_html}": items_html
    }
    
    for k, v in replacements.items():
        html_content = html_content.replace(k, v)
        
    # 动态高度
    height = 300 + len(item_counts) * 80 if item_counts else 400
    return await html_to_pic(html_content, viewport={"width": 500, "height": height})

async def render_work_card(
    user_id: str,
    user_name: str,
    coins: int,
    earned_coins: int,
    work_desc: str,
    remaining_works: int
) -> bytes:
    """渲染打工结果卡片"""
    coin_level_name = get_coin_level_name(coins)
    avatar_url = f"http://q.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=640"
    
    template_path = TEMPLATES_PATH / "work_card.html"
    with open(template_path, 'r', encoding='utf-8') as f:
        html_content = f.read()
        
    replacements = {
        "{avatar_url}": avatar_url,
        "{user_name}": user_name,
        "{work_desc}": work_desc,
        "{earned_coins}": str(earned_coins),
        "{coins}": str(coins),
        "{coin_level_name}": coin_level_name,
        "{remaining_works}": str(remaining_works),
        "{work_status}": "可打工" if remaining_works > 0 else "休息中",
        "{sign_time}": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    for k, v in replacements.items():
        html_content = html_content.replace(k, v)
        
    return await html_to_pic(html_content, viewport={"width": 500, "height": 600})

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
        
        # 优先使用存储的昵称，如果没有则暂时使用 ID
        nickname = user_data.get("nickname", "")
        if not nickname:
            nickname = user_id
            
        rank_list.append({
            "user_id": user_id,
            "nickname": nickname,
            "favorability": fav,
            "coins": user_data.get("coins", 0),
            "level_name": get_level_name(fav)
        })
    
    rank_list.sort(key=lambda x: x["favorability"], reverse=True)
    rank_data = rank_list[:15]
    
    # 尝试补全昵称
    for user in rank_data:
        # 如果昵称是 ID 或者为空，尝试获取
        if user["nickname"] == user["user_id"] or not user["nickname"]:
            name = ""
            
            # 优先尝试获取陌生人信息 (QQ昵称)
            try:
                info = await bot.get_stranger_info(user_id=int(user["user_id"]))
                name = info.get("nickname")
            except Exception:
                pass

            # 如果获取失败，且在群内，尝试获取群成员信息 (取 nickname 而非 card)
            if not name and isinstance(event, GroupMessageEvent):
                try:
                    info = await bot.get_group_member_info(group_id=event.group_id, user_id=int(user["user_id"]))
                    name = info.get("nickname")
                except Exception:
                    pass
            
            if name:
                user["nickname"] = name
                # 顺便更新数据库
                update_user_data(user["user_id"], nickname=name)
    
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
        await sign_in.finish(MessageSegment.at(user_id) + " 美波里说坏孩子不能签到哦！(已被永久拉黑)")
        
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
        # 每日重置打工次数为 1
        user_data["remaining_works"] = 1
    
    if last_sign_in == today:
        # 重复签到，提示并发送图片
        pic = await render_sign_card(
            user_id, user_name, user_data["favorability"], 
            is_query=True, title_override="今日已签到",
            action_points=current_ap,
            coins=user_data.get("coins", 0),
            total_sign_ins=total_sign_ins,
            remaining_works=user_data.get("remaining_works", 1)
        )
        
        # 获取今日运势
        fortune = user_data.get("fortune", {})
        fortune_str = ""
        if fortune and fortune.get("date") == today:
            fortune_str = f"\n今日运势: {fortune.get('type')} ({fortune.get('desc')})"
            
        await sign_in.finish(MessageSegment.at(user_id) + f" 哥哥今天已经签到过啦！明天再来吧~{fortune_str}\n首次签到: {first_sign_in}\n累计签到: {total_sign_ins}天\n当前行动值: {current_ap}\n商店金币: {user_data.get('coins', 0)}\n发送“行动”或“商店”看看吧~" + MessageSegment.image(pic))
    
    # 随机运势
    fortune_type = random.choice(list(FORTUNES.keys()))
    fortune_data = FORTUNES[fortune_type]
    fortune_info = {
        "type": fortune_type,
        "desc": fortune_data["desc"],
        "date": today
    }
    
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
    sign_in_reason = "每日签到"
    if new_achievements:
        sign_in_reason = "每日签到(含成就)"
        
    update_user_data(
        user_id, 
        reason=sign_in_reason,
        favorability=new_favorability, 
        last_sign_in=today, 
        first_sign_in=first_sign_in,
        action_points=new_ap, 
        coins=new_coins,
        total_sign_ins=new_total_sign_ins,
        achievements=earned_achievements,
        nickname=user_name,
        fortune=fortune_info
    )
    
    # 渲染图片
    pic = await render_sign_card(
        user_id, user_name, new_favorability, 
        inc=inc, action_points=new_ap, coins=new_coins,
        total_sign_ins=new_total_sign_ins,
        first_sign_in=first_sign_in,
        remaining_works=user_data.get("remaining_works", 1),
        custom_title=user_data.get("custom_title", "")
    )
    
    # 自动点赞 (尝试点赞 10 次)
    try:
        await bot.send_like(user_id=int(user_id), times=10)
    except Exception as e:
        # 点赞失败不影响签到流程
        pass

    await sign_in.finish(
        MessageSegment.at(user_id) + f" 签到成功！哥哥今天也要元气满满哦！{achievement_msg}\n今日运势: {fortune_type} ({fortune_data['desc']})\n奖励：1点行动值 & {coin_inc}金币。\n累计签到: {new_total_sign_ins}天\n首次签到: {first_sign_in}\n好感度 +{inc:.2f}，当前总好感度: {new_favorability:.2f}\n当前金币: {new_coins}\n发送“商店”可以购买商品，“行动”可消耗行动值增加好感度~" + 
        MessageSegment.image(pic)
    )

@query_favorability.handle()
async def handle_query(bot: Bot, event: MessageEvent):
    user_id = event.get_user_id()
    user_data = get_user_data(user_id)
    
    # 黑名单检查
    if user_data.get("is_perm_blacklisted"):
        await query_favorability.finish(MessageSegment.at(user_id) + " 美波里说坏孩子不能看个人信息哦！")
        
    user_name = event.sender.nickname or user_id
    
    # 渲染图片
    pic = await render_sign_card(
        user_id, user_name, user_data["favorability"], 
        is_query=True, action_points=user_data.get("action_points", 0),
        coins=user_data.get("coins", 0),
        total_sign_ins=user_data.get("total_sign_ins", 0),
        first_sign_in=user_data.get("first_sign_in", ""),
        remaining_works=user_data.get("remaining_works", 1),
        custom_title=user_data.get("custom_title", "")
    )
    
    await query_favorability.finish(MessageSegment.image(pic))

@take_action.handle()
async def handle_action(bot: Bot, event: MessageEvent):
    user_id = event.get_user_id()
    user_data = get_user_data(user_id)
    
    # 黑名单检查
    if user_data.get("is_perm_blacklisted"):
        await take_action.finish(MessageSegment.at(user_id) + " 美波里说坏孩子不能和真寻互动哦！")
        
    user_name = event.sender.nickname or user_id
    current_ap = user_data.get("action_points", 0)
    
    if current_ap <= 0:
        await take_action.finish(MessageSegment.at(user_id) + " 累了嘛？行动值不足啦，明天签到恢复哦！")
    
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
        first_sign_in=user_data.get("first_sign_in", ""),
        remaining_works=user_data.get("remaining_works", 1),
        custom_title=user_data.get("custom_title", "")
    )
    
    await take_action.finish(
        MessageSegment.at(user_id) + f" 互动时间：{action_desc}\n好感度 +{inc:.2f}！\n当前好感度: {new_favorability:.2f}\n剩余行动值: {new_ap}" + 
        MessageSegment.image(pic)
    )

@open_shop.handle()
async def handle_shop(bot: Bot, event: MessageEvent):
    user_id = event.get_user_id()
    user_data = get_user_data(user_id)
    
    # 黑名单检查
    if user_data.get("is_perm_blacklisted"):
        await open_shop.finish(MessageSegment.at(user_id) + " 美波里说坏孩子不能进商店哦！")
        
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
        
    args_text = args.extract_plain_text().strip()
    if not args_text:
        await buy_item.finish("请输入要购买的商品编号哦，例如：购买 1，或者批量购买：购买 1 10")

    parts = args_text.split()
    item_id = parts[0]
    count = 1
    
    if len(parts) > 1:
        try:
            # 尝试解析第二个参数为数量
            val = int(parts[1])
            if val > 0:
                count = val
        except ValueError:
            pass
            
    # 智能识别: 如果第一个参数是数字且不在商品列表，但第二个参数在商品列表，则交换
    # (例如用户输入了 "购买 10 1" 意为买10个1号商品)
    if item_id not in STORE_ITEMS and len(parts) > 1 and parts[1] in STORE_ITEMS:
        try:
            c = int(parts[0])
            if c > 0:
                count = c
                item_id = parts[1]
        except:
            pass

    if item_id not in STORE_ITEMS:
        await buy_item.finish("这个商品编号好像不存在呢...")
        
    item = STORE_ITEMS[item_id]
    total_price = item["price"] * count
    
    if user_data.get("coins", 0) < total_price:
        await buy_item.finish(f"金币不足哦！购买 {count} 个 {item['name']} 需要 {total_price} 金币，你只有 {user_data.get('coins', 0)} 金币。")
        
    # 扣钱并添加进背包
    new_coins = user_data["coins"] - total_price
    inventory = user_data.get("inventory", [])
    
    # 批量添加
    inventory.extend([item["name"]] * count)
    
    update_user_data(user_id, reason="商店购买", coins=new_coins, inventory=inventory)
    
    await buy_item.finish(f"🛍️ 购买成功！你花费 {total_price} 金币购买了 {count} 个【{item['name']}】。\n效果: {item['effect_desc']}\n发送“使用 {item['name']}”即可生效哦！")

@use_item.handle()
async def handle_use(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = event.get_user_id()
    user_data = get_user_data(user_id)
    
    # 黑名单检查
    if user_data.get("is_perm_blacklisted"):
        await use_item.finish(MessageSegment.at(user_id) + " 你已被列入永久黑名单，禁止使用道具。")
        
    args_text = args.extract_plain_text().strip()
    
    if not args_text:
        await use_item.finish("你想使用哪个道具呢？请在指令后面加上道具名称哦，例如：使用 真寻酱的薯片")
    
    parts = args_text.split()
    item_name = parts[0]
    count = 1
    
    # 尝试解析数量
    if len(parts) > 1:
        try:
            val = int(parts[1])
            if val > 0:
                count = val
        except ValueError:
            # 可能是道具名包含空格？暂不支持含空格的道具名+数量，除非倒序。
            # 这里简单处理：如果第二个参数是数字，就当作数量
            pass
            
    # 如果第一个参数是数量（数字），第二个是道具名
    if item_name.isdigit() and len(parts) > 1:
        try:
            c = int(item_name)
            if c > 0:
                count = c
                item_name = parts[1] # 假设道具名没有空格
        except:
            pass
        
    inventory = user_data.get("inventory", [])
    
    # 统计背包中该道具的数量
    owned_count = inventory.count(item_name)
    
    if owned_count < count:
        if owned_count == 0:
             await use_item.finish(f"你的背包里好像没有【{item_name}】呢...")
        else:
             await use_item.finish(f"你的背包里只有 {owned_count} 个【{item_name}】，不够使用 {count} 个哦！")
        
    # 查找道具配置
    target_item = None
    for item in STORE_ITEMS.values():
        if item["name"] == item_name:
            target_item = item
            break
            
    if not target_item:
        await use_item.finish("这个道具似乎无法被直接使用呢...")
        
    # 特殊道具检查
    if target_item.get("value") == "rename":
        await use_item.finish("改名卡无需直接使用哦！请发送“改名 新称号”来使用它。\n(注意：改名会消耗一张改名卡)")
        
    if target_item.get("value") == "shield":
        await use_item.finish("防盗盾是自动生效的被动道具哦！放在背包里即可抵挡一次偷窃。\n(被偷窃时自动消耗)")

    # 消耗道具
    for _ in range(count):
        inventory.remove(item_name)
    
    # 执行效果 (批量计算)
    msg = f"✨ 批量使用了 {count} 个【{item_name}】！\n"
    
    total_inc_fav = 0.0
    total_inc_ap = 0
    total_inc_coins = 0
    total_inc_days = 0
    total_inc_work = 0
    
    new_fav = user_data.get("favorability", 0.0)
    new_ap = user_data.get("action_points", 0)
    new_total_sign_ins = user_data.get("total_sign_ins", 0)
    new_coins = user_data.get("coins", 0)
    earned_achievements = user_data.get("achievements", [])
    new_remaining_works = user_data.get("remaining_works", 1)
    
    for _ in range(count):
        if target_item["type"] == "fav":
            inc = round(random.uniform(target_item["value"][0], target_item["value"][1]), 2)
            total_inc_fav += inc
            
        elif target_item["type"] == "ap":
            inc = random.randint(target_item["value"][0], target_item["value"][1])
            total_inc_ap += inc

        elif target_item["type"] == "work":
            inc = random.randint(target_item["value"][0], target_item["value"][1])
            total_inc_work += inc
            
        elif target_item["type"] == "special" and target_item["value"] == "replenish":
            # 补签逻辑优化：
            # 1. 计算理论最大签到天数 (从首次签到至今)
            # 2. 如果 累计签到 < 理论天数，则允许补签，否则无效
            
            first_sign_str = user_data.get("first_sign_in", "")
            if not first_sign_str:
                # 如果从未签到过，无法补签
                continue
                
            try:
                first_date = datetime.strptime(first_sign_str, "%Y-%m-%d")
                now_date = datetime.now()
                days_since_first = (now_date - first_date).days + 1 # +1 包含今天
                
                current_total = new_total_sign_ins # 使用累加后的值
                
                if current_total < days_since_first:
                    # 可以补签
                    total_inc_days += 1
                    inc = round(random.uniform(0, 1), 2)
                    total_inc_fav += inc
                    new_total_sign_ins += 1 # 实时更新，以便后续循环判断
                else:
                    # 无法补签 (已经是全勤)
                    # 不增加天数，不增加好感度，但道具消耗了 (或者可以退回？为了简单，视为消耗但无效)
                    pass
            except Exception:
                pass

    # 应用累加值
    new_fav = round(float(new_fav) + total_inc_fav, 2)
    new_ap += total_inc_ap
    new_remaining_works += total_inc_work
    
    if total_inc_days > 0:
        new_total_sign_ins += total_inc_days
        # 补签成就检查 (一次性检查最终状态)
        for ach in ACHIEVEMENTS:
            if new_total_sign_ins >= ach["days"] and ach["id"] not in earned_achievements:
                earned_achievements.append(ach["id"])
                new_coins += ach["reward_coins"]
                total_inc_coins += ach["reward_coins"]
                msg += f"\n🏆 解锁成就：【{ach['name']}】奖励 {ach['reward_coins']} 金币！"

    # 构建消息
    if total_inc_fav > 0:
        msg += f"好感度共增加了 {total_inc_fav:.2f} 点！当前: {new_fav:.2f}\n"
    if total_inc_ap > 0:
        msg += f"行动值共恢复了 {total_inc_ap} 点！当前: {new_ap}\n"
    if total_inc_work > 0:
        msg += f"打工次数共增加了 {total_inc_work} 次！当前: {new_remaining_works}\n"
    if total_inc_days > 0:
        msg += f"累计签到增加 {total_inc_days} 天！当前: {new_total_sign_ins} 天\n"
        
    new_coins += total_inc_coins # 成就奖励
    
    use_reason = "使用道具"
    if total_inc_coins > 0:
        use_reason = "使用道具(含成就)"
    
    # 更新数据
    update_user_data(
        user_id, 
        reason=use_reason,
        favorability=new_fav, 
        action_points=new_ap, 
        total_sign_ins=new_total_sign_ins,
        coins=new_coins,
        inventory=inventory,
        achievements=earned_achievements,
        remaining_works=new_remaining_works
    )
    
    # 渲染新的卡片 (只显示最终状态，不再显示单次inc)
    pic = await render_sign_card(
        user_id, event.sender.nickname or user_id, new_fav, 
        inc=0, title_override="使用道具", # inc=0 不显示单次增加
        action_points=new_ap, 
        coins=new_coins,
        total_sign_ins=new_total_sign_ins,
        first_sign_in=user_data.get("first_sign_in", ""),
        remaining_works=new_remaining_works,
        custom_title=user_data.get("custom_title", "")
    )
    
    await use_item.finish(MessageSegment.at(user_id) + msg + MessageSegment.image(pic))

@view_inventory.handle()
async def handle_inventory(bot: Bot, event: MessageEvent):
    user_id = event.get_user_id()
    user_data = get_user_data(user_id)
    
    # 黑名单检查
    if user_data.get("is_perm_blacklisted"):
        await view_inventory.finish(MessageSegment.at(user_id) + " 美波里说坏孩子不能看背包哦！")
        
    inventory = user_data.get("inventory", [])
    coins = user_data.get("coins", 0)
    user_name = event.sender.nickname or user_id
    
    # 渲染图片
    pic = await render_inventory_card(user_id, user_name, coins, inventory)
    
    await view_inventory.finish(MessageSegment.image(pic))

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
    
    update_user_data(target_user_id, reason="管理员设置", coins=new_val)
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

@do_work.handle()
async def handle_work(bot: Bot, event: MessageEvent):
    user_id = event.get_user_id()
    user_data = get_user_data(user_id)
    
    # 黑名单检查
    if user_data.get("is_perm_blacklisted"):
        await do_work.finish(MessageSegment.at(user_id) + " 你已被列入永久黑名单，无法打工。")
        
    today = datetime.now().strftime("%Y-%m-%d")
    
    # 检查是否签到 (每日重置依赖签到)
    last_sign_in = user_data.get("last_sign_in", "")
    if last_sign_in != today:
         await do_work.finish(MessageSegment.at(user_id) + " 请先签到以获取今日打工次数哦！")
    
    remaining_works = user_data.get("remaining_works", 1)
    
    if remaining_works <= 0:
        await do_work.finish(MessageSegment.at(user_id) + " 你今天的打工次数已经用完啦！\n可以使用道具增加打工次数哦~")
        
    # 随机打工描述
    work_desc = random.choice(ONIMAI_WORKS)
    
    # 运势加成
    fortune = user_data.get("fortune", {})
    work_rate = 1.0
    if fortune and fortune.get("date") == today:
        fortune_type = fortune.get("type")
        if fortune_type in FORTUNES:
             work_rate = FORTUNES[fortune_type]["work_rate"]
    
    # 随机获得 0-10 金币
    base_coins = random.randint(0, 10)
    earned_coins = int(base_coins * work_rate)
    
    current_coins = user_data.get("coins", 0)
    new_coins = current_coins + earned_coins
    
    # 成就追踪: 连续 3 次打工收益最低(0)或偷窃失败
    achievement_progress = user_data.get("achievement_progress", {"red_packet_total": 0, "steal_success": 0, "consecutive_fails": 0})
    if earned_coins == 0:
        achievement_progress["consecutive_fails"] += 1
    else:
        # 如果赚钱了，连续失败中断
        achievement_progress["consecutive_fails"] = 0
        
    # 扣除次数
    new_remaining_works = remaining_works - 1
    
    # 更新数据
    update_user_data(
        user_id, 
        reason="打工",
        coins=new_coins, 
        last_work_time=today, 
        remaining_works=new_remaining_works,
        achievement_progress=achievement_progress
    )
    
    # 检查隐藏成就 (非酋)
    await check_hidden_achievements(bot, event, user_id)
    
    # 渲染卡片
    pic = await render_work_card(
        user_id, 
        event.sender.nickname or user_id,
        new_coins,
        earned_coins,
        work_desc,
        new_remaining_works
    )
    
    msg = f" 辛苦啦！打工结束，获得了 {earned_coins} 金币。\n剩余打工次数: {new_remaining_works}"
    if earned_coins == 0:
        msg = f" 哎呀...今天运气不好，没赚到钱... (获得 0 金币)\n剩余打工次数: {new_remaining_works}"
    elif earned_coins == 10:
        msg = f" 哇！今天运气爆棚，赚得盆满钵满！(获得 10 金币)\n剩余打工次数: {new_remaining_works}"
        
    await do_work.finish(MessageSegment.at(user_id) + msg + MessageSegment.image(pic))

# --- 新功能实现 ---

# 改名卡功能
@rename_card.handle()
async def handle_rename(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = event.get_user_id()
    user_data = get_user_data(user_id)
    
    new_title = args.extract_plain_text().strip()
    if not new_title:
        await rename_card.finish("你想要什么新称号呢？请在指令后加上哦，例如：改名 超级哥哥")
        
    if len(new_title) > 10:
        await rename_card.finish("呜...这个称号太长了，真寻记不住啦！(请控制在 10 个字符以内)")
        
    inventory = user_data.get("inventory", [])
    if "真寻的姓名贴" not in inventory:
        await rename_card.finish("你的背包里没有改名卡呢，去找美波里买一张吧？")
        
    inventory.remove("真寻的姓名贴")
    update_user_data(user_id, custom_title=new_title, inventory=inventory)
    await rename_card.finish(f"改名成功！以后就叫你 {new_title} 啦~")

# 红包功能
active_red_packets = {}

@send_red_packet.handle()
async def handle_send_red_packet(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await send_red_packet.finish("笨蛋哥哥，红包只能在群里发哦！")
        
    user_id = event.get_user_id()
    group_id = str(event.group_id)
    user_data = get_user_data(user_id)
    
    args_text = args.extract_plain_text().strip()
    parts = args_text.split()
    
    # 支持备注：发红包 100 5 备注信息 [at用户]
    if len(parts) < 2:
        await send_red_packet.finish("笨蛋哥哥，发红包的姿势不对哦！试试这样：发红包 金额 数量 [备注]")
        
    try:
        amount = int(parts[0])
        count = int(parts[1])
    except ValueError:
        await send_red_packet.finish("金额和数量要是数字才行呢...")
    
    remark = "大吉大利，恭喜发财"
    exclusive_user = None
    
    # 检查是否at了用户
    at_list = []
    if event.message:
        for seg in event.message:
            if seg.type == "at":
                at_list.append(seg.data["qq"])
    
    if at_list:
        exclusive_user = str(at_list[0])
        if str(exclusive_user) == str(user_id):
            await send_red_packet.finish("不能给自己发专属红包哦！")
        if count != 1:
             await send_red_packet.finish("专属红包只能发 1 个哦！")

    # 处理备注
    if len(parts) >= 3:
        # 移除掉at部分
        remark_parts = []
        for part in parts[2:]:
             if not part.startswith("[CQ:at"):
                 remark_parts.append(part)
        if remark_parts:
            remark = " ".join(remark_parts)
        
    if amount <= 0 or count <= 0:
        await send_red_packet.finish("不可以发空红包骗人哦！")
        
    if count > amount:
        await send_red_packet.finish("太小气啦！每个人至少要分到 1 金币吧！")
        
    if user_data.get("coins", 0) < amount:
        await send_red_packet.finish(f"私房钱不够了呢... (需要 {amount} 金币)")
        
    # 更新数据
    achievement_progress = user_data.get("achievement_progress", {"red_packet_total": 0, "steal_success": 0, "consecutive_fails": 0})
    achievement_progress["red_packet_total"] += amount
    
    update_user_data(user_id, reason="发红包", coins=user_data["coins"] - amount, achievement_progress=achievement_progress)
    
    # 检查隐藏成就
    await check_hidden_achievements(bot, event, user_id)
    
    packet_id = str(uuid.uuid4())
    active_red_packets[packet_id] = {
        "sender_id": user_id,
        "sender_name": event.sender.nickname or user_id,
        "total_amount": amount,
        "total_count": count,
        "remain_amount": amount,
        "remain_count": count,
        "grabbed": {},
        "group_id": group_id,
        "remark": remark,
        "create_time": datetime.now(),
        "exclusive_user": exclusive_user
    }
    
    save_red_packets()
    
    msg = f"🧧 {event.sender.nickname or user_id} 给大家发福利啦！{amount} 金币的大红包！(共 {count} 个)\n备注: {remark}"
    if exclusive_user:
        msg = f"🧧 {event.sender.nickname or user_id} 发了一个专属红包！{amount} 金币！\n指定领取人: {MessageSegment.at(exclusive_user)}\n备注: {remark}"
    else:
        msg += "\n快发送“抢红包”来抢吧！"
        
    await send_red_packet.finish(msg)

@send_global_red_packet.handle()
async def handle_send_global_red_packet(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = event.get_user_id()
    user_data = get_user_data(user_id)
    
    if isinstance(event, GroupMessageEvent):
        if str(event.group_id) in GLOBAL_RED_PACKET_BLACKLIST:
             await send_global_red_packet.finish("本群已被禁止参与世界红包活动！")
    
    args_text = args.extract_plain_text().strip()
    parts = args_text.split()
    
    # 支持备注：发世界红包 100 5 备注信息
    if len(parts) < 2:
        await send_global_red_packet.finish("笨蛋哥哥，发世界红包的姿势不对哦！试试这样：发世界红包 金额 数量 [备注]")
        
    try:
        amount = int(parts[0])
        count = int(parts[1])
    except ValueError:
        await send_global_red_packet.finish("金额和数量要是数字才行呢...")
        
    remark = "世界和平，普天同庆"
    if len(parts) >= 3:
        remark = " ".join(parts[2:])
        
    if amount <= 0 or count <= 0:
        await send_global_red_packet.finish("不可以发空红包骗人哦！")
        
    if count > amount:
        await send_global_red_packet.finish("太小气啦！每个人至少要分到 1 金币吧！")
        
    if user_data.get("coins", 0) < amount:
        await send_global_red_packet.finish(f"私房钱不够了呢... (需要 {amount} 金币)")
        
    # 更新数据
    achievement_progress = user_data.get("achievement_progress", {"red_packet_total": 0, "steal_success": 0, "consecutive_fails": 0})
    achievement_progress["red_packet_total"] += amount
    
    update_user_data(user_id, reason="发世界红包", coins=user_data["coins"] - amount, achievement_progress=achievement_progress)
    
    # 检查隐藏成就
    await check_hidden_achievements(bot, event, user_id)
    
    packet_id = str(uuid.uuid4())
    active_red_packets[packet_id] = {
        "sender_id": user_id,
        "sender_name": event.sender.nickname or user_id,
        "total_amount": amount,
        "total_count": count,
        "remain_amount": amount,
        "remain_count": count,
        "grabbed": {},
        "group_id": "GLOBAL",
        "remark": remark,
        "create_time": datetime.now(),
        "exclusive_user": None # 世界红包暂不支持专属
    }
    
    save_red_packets()
    
    msg = f"🌏 {event.sender.nickname or user_id} 发了一个世界红包！{amount} 金币！(共 {count} 个)\n备注: {remark}\n来自群: {group_name}({group_id})\n祝大家: 恭喜发财，大吉大利！心想事成，万事如意！\n所有群都可以抢哦！快发送“抢红包”来抢吧！"
    await send_global_red_packet.send(msg)
    
    # 广播给其他非黑名单群组
    try:
        group_list = await bot.get_group_list()
        broadcast_msg = f"🌏 [世界红包通知] {event.sender.nickname or user_id} 发了一个世界红包！{amount} 金币！(共 {count} 个)\n备注: {remark}\n来自群: {group_name}({group_id})\n祝大家: 恭喜发财，大吉大利！心想事成，万事如意！\n快发送“抢红包”来抢吧！"
        
        for group in group_list:
            gid = str(group['group_id'])
            
            # 跳过黑名单群组
            if gid in GLOBAL_RED_PACKET_BLACKLIST:
                continue
                
            # 跳过当前群组（已经发过了）
            if isinstance(event, GroupMessageEvent) and gid == str(event.group_id):
                continue
                
            try:
                await bot.send_group_msg(group_id=int(gid), message=broadcast_msg)
                await asyncio.sleep(0.5) # 防止刷屏
            except Exception:
                continue
                
    except Exception:
        pass

@grab_red_packet.handle()
async def handle_grab_red_packet(bot: Bot, event: MessageEvent):
    if not isinstance(event, GroupMessageEvent):
        await grab_red_packet.finish("只能在群里抢红包哦！")
        
    user_id = event.get_user_id()
    group_id = str(event.group_id)
    
    if group_id in GLOBAL_RED_PACKET_BLACKLIST:
        # 黑名单群组只能抢本群红包，不能抢世界红包
        can_grab_global = False
    else:
        can_grab_global = True
    
    if not active_red_packets:
        await grab_red_packet.finish("现在没有红包可以抢呢，要不要发一个？")
        
    target_packet_id = None
    target_packet = None
    
    # 优先级：专属红包 > 世界红包 > 群内红包
    
    # 0. 优先抢专属红包
    for pid, packet in active_red_packets.items():
        if packet.get("exclusive_user") == user_id and packet["remain_count"] > 0:
            target_packet_id = pid
            target_packet = packet
            break
    
    # 1. 先尝试抢世界红包 (如果允许，且非专属)
    if not target_packet and can_grab_global:
        for pid, packet in active_red_packets.items():
            if packet.get("group_id") == "GLOBAL" and packet["remain_count"] > 0 and user_id not in packet["grabbed"] and not packet.get("exclusive_user"):
                target_packet_id = pid
                target_packet = packet
                break
                
    # 2. 如果没抢到世界红包，尝试抢本群红包 (且非专属)
    if not target_packet:
        for pid, packet in active_red_packets.items():
            if packet.get("group_id") == group_id and packet["remain_count"] > 0 and user_id not in packet["grabbed"] and not packet.get("exclusive_user"):
                target_packet_id = pid
                target_packet = packet
                break
            
    if not target_packet:
        # 检查是否抢过 (优先检查世界红包)
        if can_grab_global:
            for pid, packet in active_red_packets.items():
                if packet.get("group_id") == "GLOBAL" and packet["remain_count"] > 0 and user_id in packet["grabbed"]:
                    await grab_red_packet.finish("你已经抢过这个世界红包啦，做人不能太贪心哦！")
        
        # 再检查本群红包
        for pid, packet in active_red_packets.items():
            if packet.get("group_id") == group_id and packet["remain_count"] > 0 and user_id in packet["grabbed"]:
                await grab_red_packet.finish("你已经抢过这个红包啦，做人不能太贪心哦！")
        
        # 检查是否有已抢完的
        has_finished_packet = False
        if can_grab_global:
            for pid, packet in active_red_packets.items():
                if packet.get("group_id") == "GLOBAL":
                    has_finished_packet = True
                    break
        
        if not has_finished_packet:
            for pid, packet in active_red_packets.items():
                if packet.get("group_id") == group_id:
                    has_finished_packet = True
                    break
                 
        if has_finished_packet:
            await grab_red_packet.finish("呜...手慢了，红包都被抢光了...")
        else:
            await grab_red_packet.finish("现在没有红包可以抢呢，要不要发一个？")
            
    # 再次检查专属红包权限 (防止漏网之鱼)
    if target_packet.get("exclusive_user") and target_packet.get("exclusive_user") != user_id:
         await grab_red_packet.finish("这不是发给你的专属红包哦！")
        
    if target_packet["remain_count"] == 1:
        grab_amount = target_packet["remain_amount"]
    else:
        avg = target_packet["remain_amount"] / target_packet["remain_count"]
        grab_amount = random.randint(1, int(avg * 2))
        max_allowed = target_packet["remain_amount"] - (target_packet["remain_count"] - 1)
        grab_amount = min(grab_amount, max_allowed)
        
    target_packet["remain_amount"] -= grab_amount
    target_packet["remain_count"] -= 1
    target_packet["grabbed"][user_id] = grab_amount
    
    user_data = get_user_data(user_id)
    update_user_data(user_id, reason="抢红包", coins=user_data.get("coins", 0) + grab_amount)
    
    msg = f"好耶！你抢到了 {grab_amount} 金币！"
    msg += f"\n(来自 {target_packet['sender_name']} 的红包)"
    if target_packet.get("remark"):
        msg += f"\n备注: {target_packet['remark']}"
    
    if target_packet["remain_count"] == 0:
        msg += f"\n红包已抢完！"
        del active_red_packets[target_packet_id]
        
    save_red_packets()
        
    await grab_red_packet.finish(MessageSegment.at(user_id) + msg)

# 抢劫功能
@rob_user.handle()
async def handle_rob(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = event.get_user_id()
    user_data = get_user_data(user_id)
    
    msg = args
    if msg["at"]:
        target_id = str(msg["at"][0].data["qq"])
    else:
        await rob_user.finish("想对谁恶作剧呢？请艾特对方哦！")
        
    if target_id == user_id:
        await rob_user.finish("笨蛋！为什么要偷自己的东西啊...")
        
    last_rob = user_data.get("last_rob_time", 0)
    now = time.time()
    if now - last_rob < 3600:
        remain = int((3600 - (now - last_rob)) / 60)
        await rob_user.finish(f"美波里正在盯着你呢！再安分 {remain} 分钟吧...")
        
    if user_data.get("action_points", 0) < 1:
        await rob_user.finish("累得动不了了... 需要 1 点行动值才能恶作剧哦。")
        
    target_data = get_user_data(target_id)
    if not target_data:
        await rob_user.finish("那个人比你还穷呢，没什么好偷的...")
        
    new_ap = user_data["action_points"] - 1
    update_user_data(user_id, action_points=new_ap, last_rob_time=now)
    
    target_inventory = target_data.get("inventory", [])
    if "防盗盾" in target_inventory:
        target_inventory.remove("防盗盾")
        update_user_data(target_id, reason="防盗盾消耗", inventory=target_inventory)
        await rob_user.finish(f"恶作剧失败！对方用【防盗盾】挡住了你！")
        
    # 运势加成
    fortune = user_data.get("fortune", {})
    steal_rate_bonus = 0.0
    today = datetime.now().strftime("%Y-%m-%d")
    if fortune and fortune.get("date") == today:
        fortune_type = fortune.get("type")
        if fortune_type in FORTUNES:
             steal_rate_bonus = FORTUNES[fortune_type]["steal_rate"]

    base_rate = 0.3
    final_rate = base_rate + steal_rate_bonus
    # 确保概率在合理范围 0.05 - 0.95
    final_rate = max(0.05, min(0.95, final_rate))
    
    success = random.random() < final_rate
    
    achievement_progress = user_data.get("achievement_progress", {"red_packet_total": 0, "steal_success": 0, "consecutive_fails": 0})

    if success:
        steal_amount = random.randint(10, 50)
        steal_amount = min(steal_amount, target_data.get("coins", 0))
        
        if steal_amount <= 0:
             await rob_user.finish("对方口袋里空空如也，只好灰溜溜地回来了...")
             
        achievement_progress["steal_success"] += 1
        achievement_progress["consecutive_fails"] = 0
             
        update_user_data(user_id, reason="偷窃成功", coins=user_data["coins"] + steal_amount, achievement_progress=achievement_progress)
        update_user_data(target_id, reason="被偷窃", coins=target_data["coins"] - steal_amount)
        
        await check_hidden_achievements(bot, event, user_id)
        
        await rob_user.finish(MessageSegment.at(user_id) + f" 嘿嘿... 你成功从 {target_id} 那里拿走了 {steal_amount} 金币！(真寻：不要教坏小孩子啊！)")
    else:
        penalty = 20
        current_coins = user_data["coins"]
        penalty = min(penalty, current_coins)
        
        achievement_progress["consecutive_fails"] += 1
        
        update_user_data(user_id, reason="偷窃失败赔偿", coins=current_coins - penalty, achievement_progress=achievement_progress)
        update_user_data(target_id, reason="获得偷窃赔偿", coins=target_data["coins"] + penalty)
        
        await check_hidden_achievements(bot, event, user_id)
        
        await rob_user.finish(MessageSegment.at(user_id) + f" 哇啊！被发现了！被狠狠教训了一顿，赔偿了 {penalty} 金币... (美波里：不可以做坏事哦！)")

# 银行功能
@bank_cmd.handle()
async def handle_bank(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    user_id = event.get_user_id()
    user_data = get_user_data(user_id)
    
    args_text = args.extract_plain_text().strip()
    parts = args_text.split()
    
    if not parts:
        bank_coins = user_data.get("bank_coins", 0)
        wallet_coins = user_data.get("coins", 0)
        await bank_cmd.finish(f"🏦 欢迎光临真寻银行！\n要把私房钱存这里吗？\n当前存款: {bank_coins} 金币\n钱包余额: {wallet_coins} 金币\n\n指令帮助：\n银行 存 [金额]\n银行 取 [金额]\n发送“银行明细”可查看近期流水")
        
    op = parts[0]
    
    if op == "存":
        if len(parts) < 2:
            await bank_cmd.finish("要存多少钱呢？")
        try:
            amount = int(parts[1])
        except:
            await bank_cmd.finish("数字写错了啦...")
            
        if amount <= 0:
            await bank_cmd.finish("不可以存负数哦！")
            
        if user_data.get("coins", 0) < amount:
            await bank_cmd.finish("身上没有那么多钱呢...")
            
        # 记录流水
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        history = user_data.get("bank_history", [])
        history.append({"time": now_str, "type": "存款", "amount": amount, "balance": user_data.get("bank_coins", 0) + amount})
        # 只保留最近 50 条
        if len(history) > 50:
            history = history[-50:]
            
        update_user_data(user_id, reason="银行存款", coins=user_data["coins"] - amount, bank_coins=user_data.get("bank_coins", 0) + amount, bank_history=history)
        await bank_cmd.finish(f"✅ 已帮你存好 {amount} 金币啦！(安心感满满~)\n当前存款: {user_data.get('bank_coins', 0) + amount}")
        
    elif op == "取":
        if len(parts) < 2:
            await bank_cmd.finish("要取多少钱去买零食？")
        try:
            amount = int(parts[1])
        except:
            await bank_cmd.finish("数字写错了啦...")
            
        if amount <= 0:
            await bank_cmd.finish("不可以取负数哦！")
            
        if user_data.get("bank_coins", 0) < amount:
            await bank_cmd.finish("存款不够啦！是不是记错了？")
            
        # 记录流水
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        history = user_data.get("bank_history", [])
        history.append({"time": now_str, "type": "取款", "amount": -amount, "balance": user_data.get("bank_coins", 0) - amount})
        # 只保留最近 50 条
        if len(history) > 50:
            history = history[-50:]
            
        update_user_data(user_id, reason="银行取款", coins=user_data["coins"] + amount, bank_coins=user_data.get("bank_coins", 0) - amount, bank_history=history)
        await bank_cmd.finish(f"✅ 成功取出 {amount} 金币！不要乱花哦~\n钱包余额: {user_data.get('coins', 0) + amount}")
        
    else:
        await bank_cmd.finish("真寻听不懂你在说什么... 试试“银行 存”或“银行 取”？")

@bank_history_cmd.handle()
async def handle_bank_history(bot: Bot, event: MessageEvent):
    """查看钱包及银行流水"""
    user_id = event.get_user_id()
    user_data = get_user_data(user_id)
    
    # 获取两个历史记录并合并 (或者只显示 wallet_history，因为 wallet_history 包含了银行存取款)
    # bank_history 只有存取款和利息，wallet_history 包含了所有 coins 变动
    # 用户想要 "余额明细"，主要是看钱花哪了
    # 我们可以主要显示 wallet_history，并补充 bank_history 中的利息记录（因为利息不直接进钱包）
    
    wallet_hist = user_data.get("wallet_history", [])
    bank_hist = user_data.get("bank_history", [])
    
    # 提取 bank_history 中的利息记录 (type="利息")，因为它们不影响 wallet coins，但在 wallet_history 中没有
    # 不过用户可能想看总资产变动？
    # 简单起见，我们将两者合并按时间排序展示，或者只展示 wallet_history (因为用户主要问的是 商店/红包等)
    # 考虑到用户也可能关心利息，我们将两者合并展示。
    
    combined_hist = []
    
    # 标记来源以便区分
    for r in wallet_hist:
        # 兼容旧数据
        if "type" not in r: continue
        r_copy = r.copy()
        r_copy["_source"] = "wallet"
        combined_hist.append(r_copy)
        
    for r in bank_hist:
        if "type" not in r: continue
        # 如果是 存/取款，在 wallet_history 中已经有了（作为 银行存/取款），避免重复
        # wallet_history: type="银行存款", amount=-100
        # bank_history: type="存款", amount=100
        # 我们可以只保留 bank_history 中的 "利息"
        if r["type"] == "利息":
            r_copy = r.copy()
            r_copy["_source"] = "bank"
            combined_hist.append(r_copy)
            
    # 按时间排序 (假设时间格式一致 "%Y-%m-%d %H:%M:%S")
    try:
        combined_hist.sort(key=lambda x: x["time"])
    except:
        pass
        
    # 只取最近 50 条
    if len(combined_hist) > 50:
        combined_hist = combined_hist[-50:]
        
    if not combined_hist:
        await bank_history_cmd.finish("目前还没有资金变动记录哦~")
        
    # 构建合并转发消息节点
    nodes = []
    # 倒序显示，最新的在上面
    for record in reversed(combined_hist):
        is_income = record["amount"] > 0
        amount_str = f"{record['amount']:+d}"
        
        # 图标区分
        if record.get("_source") == "bank":
            icon = "🏦" # 银行利息
            balance_str = f"(银行余额: {record['balance']})"
        else:
            if is_income:
                icon = "💰" # 收入
            else:
                icon = "💸" # 支出
            balance_str = f"(钱包余额: {record['balance']})"
            
        content = f"{record['time']} | {icon} {record['type']}: {amount_str} {balance_str}"
        
        nodes.append(
            MessageSegment.node_custom(
                user_id=int(bot.self_id),
                nickname="真寻账本",
                content=content
            )
        )
    
    # 添加头部汇总信息
    wallet_coins = user_data.get("coins", 0)
    bank_coins = user_data.get("bank_coins", 0)
    total_assets = wallet_coins + bank_coins
    
    header_content = (
        f"📜 {event.sender.nickname or user_id} 的资产明细\n"
        f"👛 钱包余额: {wallet_coins}\n"
        f"🏦 银行存款: {bank_coins}\n"
        f"💎 总资产: {total_assets}\n"
        f"----------------\n"
        f"最近 50 笔变动记录 (含钱包流水及银行利息)"
    )
    
    nodes.insert(0, MessageSegment.node_custom(
        user_id=int(bot.self_id),
        nickname="真寻账本",
        content=header_content
    ))

    # 发送合并转发消息
    if isinstance(event, GroupMessageEvent):
        await bot.send_group_forward_msg(group_id=event.group_id, messages=nodes)
    else:
        await bot.send_private_forward_msg(user_id=event.user_id, messages=nodes)

@transfer_coins.handle()
async def handle_transfer(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """转账功能"""
    user_id = event.get_user_id()
    msg = args
    
    target_id = None
    amount = 0
    
    # 尝试从 at 获取目标
    if msg["at"]:
        target_id = str(msg["at"][0].data["qq"])
        # 解析金额
        text = msg.extract_plain_text().strip()
        try:
            amount = int(text)
        except:
            await transfer_coins.finish("请输入正确的金额！")
    else:
        # 尝试从文本解析 QQ号 和 金额
        text = msg.extract_plain_text().strip()
        parts = text.split()
        if len(parts) >= 2:
            target_id = parts[0]
            if not target_id.isdigit():
                 await transfer_coins.finish("QQ号格式不正确哦！")
            try:
                amount = int(parts[1])
            except:
                await transfer_coins.finish("请输入正确的金额！")
        else:
            await transfer_coins.finish("用法: 转账 @某人 金额 或 转账 QQ号 金额")
            
    if not target_id:
        await transfer_coins.finish("要转给谁呢？")
        
    if target_id == user_id:
        await transfer_coins.finish("左手倒右手... 有意义吗？")
        
    if amount <= 0:
        await transfer_coins.finish("不可以转负数哦！")
        
    user_data = get_user_data(user_id)
    if user_data.get("coins", 0) < amount:
        await transfer_coins.finish("你的钱不够啦！")
        
    target_data = get_user_data(target_id)
    
    # 扣款
    update_user_data(user_id, reason="转账支出", coins=user_data["coins"] - amount)
    # 收款
    update_user_data(target_id, reason="转账收入", coins=target_data.get("coins", 0) + amount)
    
    await transfer_coins.finish(MessageSegment.at(user_id) + f" 成功转账 {amount} 金币给 " + (MessageSegment.at(target_id) if target_id.isdigit() else target_id) + " ！\n(真寻：好大方！)")

@wealth_rank.handle()
async def handle_wealth_rank(bot: Bot, event: MessageEvent):
    """富豪榜"""
    data = load_data()
    rank_data = []
    
    for uid, udata in data.items():
        if uid.startswith("group_"):
            continue
        # 计算总资产 (钱包 + 银行)
        total_coins = udata.get("coins", 0) + udata.get("bank_coins", 0)
        if total_coins > 0:
            rank_data.append({
                "user_id": uid,
                "nickname": udata.get("nickname", "未知用户"),
                "coins": total_coins,
                "favorability": udata.get("favorability", 0) # 用于渲染兼容
            })
            
    # 按金币排序
    rank_data.sort(key=lambda x: x["coins"], reverse=True)
    top_10 = rank_data[:10]
    
    if not top_10:
        await wealth_rank.finish("还没有人有钱呢...")
        
    # 渲染排行榜 (复用 render_rank_card，但需要修改 render_rank_card 支持显示金币)
    # 由于 render_rank_card 默认显示好感度，我们这里简单修改一下 html 内容或者直接发文本
    # 为了美观，还是发文本吧，或者修改 render_rank_card
    
    msg = "💰 绪山富豪榜 Top 10 💰\n"
    for idx, user in enumerate(top_10, 1):
        name = user["nickname"] or user["user_id"]
        msg += f"{idx}. {name}: {user['coins']} 金币\n"
        
    await wealth_rank.finish(msg)

@bank_rank.handle()
async def handle_bank_rank(bot: Bot, event: MessageEvent):
    """银行存款排行榜 (仅管理员)"""
    data = load_data()
    rank_data = []
    
    for uid, udata in data.items():
        if uid.startswith("group_"):
            continue
        bank_coins = udata.get("bank_coins", 0)
        if bank_coins > 0:
            rank_data.append({
                "user_id": uid,
                "nickname": udata.get("nickname", "未知用户"),
                "bank_coins": bank_coins
            })
            
    # 按存款排序
    rank_data.sort(key=lambda x: x["bank_coins"], reverse=True)
    top_10 = rank_data[:10]
    
    if not top_10:
        await bank_rank.finish("还没有人存钱呢...")
        
    msg = "🏦 绪山银行存款榜 Top 10 🏦\n"
    for idx, user in enumerate(top_10, 1):
        name = user["nickname"] or user["user_id"]
        msg += f"{idx}. {name}({user['user_id']}): {user['bank_coins']} 金币\n"
        
    await bank_rank.finish(msg)

@duel_user.handle()
async def handle_duel(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """决斗功能"""
    user_id = event.get_user_id()
    msg = args
    
    if not msg["at"]:
        await duel_user.finish("要向谁发起挑战呢？请艾特对方！")
        
    target_id = str(msg["at"][0].data["qq"])
    if target_id == user_id:
        await duel_user.finish("和自己打架？真寻看不懂...")
        
    user_data = get_user_data(user_id)
    if user_data.get("action_points", 0) < 2:
        await duel_user.finish("体力不足！决斗需要 2 点行动值。")
        
    # 消耗体力
    update_user_data(user_id, action_points=user_data["action_points"] - 2)
    
    target_data = get_user_data(target_id)
    
    # 决斗逻辑：简单的随机数比拼，加上好感度加成 (好感度越高运气越好?)
    # 基础战力 50-100
    user_power = random.randint(50, 100) + int(user_data.get("favorability", 0) / 10)
    target_power = random.randint(50, 100) + int(target_data.get("favorability", 0) / 10)
    
    # 赌注：赢家拿走输家 10% 钱包金币 (上限 200)
    
    user_coins = user_data.get("coins", 0)
    target_coins = target_data.get("coins", 0)
    
    if user_power > target_power:
        # 赢了
        win_coins = min(int(target_coins * 0.1), 200)
        if win_coins < 10: win_coins = 10 # 保底
        
        # 如果对方没钱
        if target_coins < win_coins:
            win_coins = target_coins
            
        update_user_data(user_id, reason="决斗胜利", coins=user_coins + win_coins, 
                         duel_stats={"win": user_data.get("duel_stats", {}).get("win", 0) + 1, 
                                     "loss": user_data.get("duel_stats", {}).get("loss", 0)})
        update_user_data(target_id, reason="决斗失败", coins=target_coins - win_coins,
                         duel_stats={"win": target_data.get("duel_stats", {}).get("win", 0), 
                                     "loss": target_data.get("duel_stats", {}).get("loss", 0) + 1})
                                     
        await duel_user.finish(
            MessageSegment.at(user_id) + f" 发起了决斗！\n"
            f"⚔️ {user_power} vs {target_power} 🛡️\n"
            f"🎉 恭喜你获胜！赢得了 {win_coins} 金币！\n(真寻：好厉害！)"
        )
    else:
        # 输了
        lose_coins = min(int(user_coins * 0.1), 200)
        if lose_coins < 10: lose_coins = 10
        
        if user_coins < lose_coins:
            lose_coins = user_coins
            
        update_user_data(user_id, reason="决斗失败", coins=user_coins - lose_coins,
                         duel_stats={"win": user_data.get("duel_stats", {}).get("win", 0), 
                                     "loss": user_data.get("duel_stats", {}).get("loss", 0) + 1})
        update_user_data(target_id, reason="决斗胜利", coins=target_coins + lose_coins,
                         duel_stats={"win": target_data.get("duel_stats", {}).get("win", 0) + 1, 
                                     "loss": target_data.get("duel_stats", {}).get("loss", 0)})
                                     
        await duel_user.finish(
            MessageSegment.at(user_id) + f" 发起了决斗！\n"
            f"⚔️ {user_power} vs {target_power} 🛡️\n"
            f"💔 惜败... 输掉了 {lose_coins} 金币。\n(真寻：下次加油...)"
        )

@play_dice.handle()
async def handle_dice(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """掷骰子"""
    user_id = event.get_user_id()
    text = args.extract_plain_text().strip()
    
    bet = 10
    if text.isdigit():
        bet = int(text)
        
    if bet <= 0:
        await play_dice.finish("赌注不能是负数哦！")
        
    user_data = get_user_data(user_id)
    if user_data.get("coins", 0) < bet:
        await play_dice.finish("你的钱不够啦！")
        
    # 玩家点数
    player_roll = random.randint(1, 6) + random.randint(1, 6)
    # 庄家点数
    bot_roll = random.randint(1, 6) + random.randint(1, 6)
    
    msg = f"🎲 你的点数: {player_roll}\n🤖 真寻的点数: {bot_roll}\n"
    
    if player_roll > bot_roll:
        win = bet
        update_user_data(user_id, reason="掷骰子胜利", coins=user_data["coins"] + win)
        msg += f"🎉 你赢了！获得 {win} 金币！"
    elif player_roll < bot_roll:
        loss = bet
        update_user_data(user_id, reason="掷骰子失败", coins=user_data["coins"] - loss)
        msg += f"💔 你输了... 失去了 {loss} 金币。"
    else:
        msg += "🤝 平局！退回赌注。"
        
    await play_dice.finish(msg)

@buy_lottery.handle()
async def handle_buy_lottery(bot: Bot, event: MessageEvent):
    """买彩票"""
    user_id = event.get_user_id()
    user_data = get_user_data(user_id)
    
    # 检查是否已购买
    lottery_data = load_lottery_data()
    # 兼容性处理：支持字符串列表和字典列表
    participants_ids = []
    for p in lottery_data.get("participants", []):
        if isinstance(p, str):
            participants_ids.append(p)
        elif isinstance(p, dict):
            participants_ids.append(p.get("user_id"))

    if user_id in participants_ids:
        await buy_lottery.finish("你今天已经买过彩票啦！明天再来吧。")
        
    price = LOTTERY_TICKET_PRICE
    if user_data.get("coins", 0) < price:
        await buy_lottery.finish(f"彩票一张 {price} 金币，你的钱不够哦！")
        
    # 扣款
    update_user_data(user_id, reason="购买彩票", coins=user_data["coins"] - price)
    
    # 增加奖池
    lottery_data["pool"] += price
    
    # 记录群组信息以便中奖通知
    group_id = str(event.group_id) if isinstance(event, GroupMessageEvent) else None
    lottery_data["participants"].append({"user_id": user_id, "group_id": group_id})
    save_lottery_data(lottery_data)
    
    # 计算预计总奖池（含基础池）
    base_pool = lottery_data.get("base_pool_value", 500)
    display_pool = lottery_data["pool"] + base_pool
    
    await buy_lottery.finish(
        f"🎫 购买成功！花费 {price} 金币。\n"
        f"当前可分配奖池: {lottery_data['pool']} 金币\n"
        f"展示奖池(含基础池): {display_pool} 金币\n"
        f"将于次日凌晨 00:00 开奖，敬请期待！"
    )

@lottery_info.handle()
async def handle_lottery_info(bot: Bot, event: MessageEvent):
    """查看彩票信息"""
    lottery_data = load_lottery_data()
    pool = lottery_data.get("pool", 0)
    base_pool = lottery_data.get("base_pool_value", 500)
    total_pool = pool + base_pool
    count = len(lottery_data.get("participants", []))
    
    await lottery_info.finish(
        f"🎰 [绪山彩票中心]\n"
        f"可分配奖池: {pool} 金币\n"
        f"展示奖池: {total_pool} 金币 (基础池: {base_pool} + 累积: {pool})\n"
        f"已参与人数: {count} 人\n\n"
        f"发送“买彩票”即可参与，每张 {LOTTERY_TICKET_PRICE} 金币！"
    )

@scheduler.scheduled_job("cron", hour=0, minute=0, id="lottery_draw")
async def draw_lottery_daily():
    """每日彩票开奖"""
    lottery_data = load_lottery_data()
    participants = lottery_data.get("participants", [])
    current_pool = lottery_data.get("pool", 0)
    base_pool = lottery_data.get("base_pool_value", 500)
    last_count = lottery_data.get("last_count", 0)

    # 规范化 participants 为 (user_id, group_id) 字典列表
    normalized_participants = []
    for p in participants:
        if isinstance(p, str):
            normalized_participants.append({"user_id": p, "group_id": None})
        elif isinstance(p, dict):
            normalized_participants.append(p)

    # 基础池仍保留动态变化（用于展示），但不直接注入真实可分配奖池
    current_count = len(normalized_participants)
    delta = current_count - last_count
    growth_rate = 10 if base_pool <= 2000 else 5
    new_base_pool = base_pool + (delta * growth_rate)
    new_base_pool = max(500, min(5000, new_base_pool))
    lottery_data["base_pool_value"] = new_base_pool
    lottery_data["last_count"] = current_count

    if not normalized_participants:
        logger.info("今日无人购买彩票，奖池累积。")
        save_lottery_data(lottery_data)
        return

    # 人少且滚存过高时，回收部分奖池，避免持续膨胀
    recycle_tax = 0
    if current_pool > LOTTERY_POOL_SOFT_CAP and current_count < LOTTERY_POOL_LOW_PARTICIPANT_THRESHOLD:
        recycle_tax = int((current_pool - LOTTERY_POOL_SOFT_CAP) * LOTTERY_POOL_RECYCLE_TAX_RATE)
        current_pool = max(0, current_pool - recycle_tax)

    # Low participation payout cap: prevent oversized historical pool from being released in one draw.
    max_distributable_pot = LOTTERY_TICKET_PRICE * max(1, current_count) * LOTTERY_MAX_POT_MULTIPLIER_PER_PARTICIPANT
    if current_pool > max_distributable_pot:
        recycle_tax += (current_pool - max_distributable_pot)
        current_pool = max_distributable_pot

    # 真实可分配奖池仅使用购票累积池，禁止基础池直接增发
    total_pot = current_pool

    random.shuffle(normalized_participants)
    winner_count = min(3, len(normalized_participants))
    winners = normalized_participants[:winner_count]
    payout_rates = LOTTERY_PAYOUT_RATES[winner_count]

    payout_details = []  # (user_id, amount, rank_desc, group_id)
    total_payout = 0

    for i, winner_info in enumerate(winners):
        rank = i + 1
        prize = 0
        rank_desc = ""
        winner_id = winner_info["user_id"]
        winner_group = winner_info.get("group_id")

        if rank == 1:
            prize = int(total_pot * payout_rates[0])
            rank_desc = "一等奖"
        elif rank == 2 and len(payout_rates) >= 2:
            prize = int(total_pot * payout_rates[1])
            rank_desc = "二等奖"
        elif rank == 3 and len(payout_rates) >= 3:
            prize = int(total_pot * payout_rates[2])
            rank_desc = "三等奖"

        if prize > 0:
            user_data = get_user_data(winner_id)
            update_user_data(winner_id, reason="彩票中奖", coins=user_data.get("coins", 0) + prize)
            payout_details.append((winner_id, prize, rank_desc, winner_group))
            total_payout += prize

    remaining_pot = max(0, total_pot - total_payout)

    lottery_data["pool"] = remaining_pot
    lottery_data["participants"] = []
    save_lottery_data(lottery_data)

    logger.info(
        f"彩票开奖！参与人数: {current_count}, 可分配奖池: {total_pot}, "
        f"总派奖: {total_payout}, 滚存: {remaining_pot}, 回收税: {recycle_tax}"
    )

    # 尝试通知中奖者
    try:
        bot = get_bot()
    except Exception:
        logger.warning("彩票开奖时无法获取 Bot 实例，跳过通知。")
        return

    for uid, prize, desc, gid in payout_details:
        msg = f"🎉 恭喜！你在彩票抽奖中中了【{desc}】！\n获得奖金: {prize} 金币！"
        sent = False
        try:
            await bot.send_private_msg(user_id=int(uid), message=msg)
            sent = True
            await asyncio.sleep(1)
        except Exception as e:
            logger.warning(f"彩票私聊通知失败({uid}): {e}")

        if not sent and gid:
            try:
                await bot.send_group_msg(group_id=int(gid), message=MessageSegment.at(uid) + " " + msg)
                await asyncio.sleep(1)
            except Exception as e2:
                logger.warning(f"彩票群聊通知失败({gid}): {e2}")
# 每日利息结算
@scheduler.scheduled_job("cron", hour=4, minute=0, id="bank_interest")
async def bank_interest_job():
    data = load_data()
    count = 0
    total_interest = 0
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    for user_id, user_data in data.items():
        if user_id.startswith("group_"):
            continue
        bank_coins = user_data.get("bank_coins", 0)
        if bank_coins > 0:
            interest = int(bank_coins * 0.01)
            # 每日利息上限 50 金币，防止通货膨胀
            interest = min(interest, 50)
            if interest > 0:
                user_data["bank_coins"] = bank_coins + interest
                
                # 记录利息流水
                history = user_data.get("bank_history", [])
                history.append({"time": now_str, "type": "利息", "amount": interest, "balance": user_data["bank_coins"]})
                if len(history) > 50:
                    history = history[-50:]
                user_data["bank_history"] = history
                
                count += 1
                total_interest += interest
                
    if count > 0:
        save_data(data)

