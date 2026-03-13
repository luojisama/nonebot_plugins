import json
import os
import httpx
from datetime import datetime
from pathlib import Path
from .config import Config

config = Config()

async def get_hitokoto() -> tuple[str, str]:
    """获取一言 (尝试主 API 和备用 API)"""
    async with httpx.AsyncClient() as client:
        # 尝试主 API
        try:
            response = await client.get(config.hitokoto_api_url, timeout=3.0)
            if response.status_code == 200:
                data = response.json()
                return data.get('hitokoto', '生活原本沉闷，但跑起来就有风。'), data.get('from', '网络')
        except Exception:
            pass
        
        # 尝试备用 API
        try:
            response = await client.get(config.hitokoto_backup_api_url, timeout=3.0)
            if response.status_code == 200:
                data = response.json()
                # 备用 API 格式: {"data": {"hitokoto": "..."}}
                if "data" in data and isinstance(data["data"], dict):
                    return data["data"].get("hitokoto", "生活原本沉闷，但跑起来就有风。"), "网络"
        except Exception:
            pass
            
    return "生活原本沉闷，但跑起来就有风。", "网络"

def load_data() -> dict:
    """加载用户数据"""
    if not config.sign_in_data_path.exists():
        return {}
    try:
        with open(config.sign_in_data_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def save_data(data: dict):
    """保存用户数据"""
    config.sign_in_data_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config.sign_in_data_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def get_user_data(user_id: str) -> dict:
    """获取单个用户或群聊的数据"""
    data = load_data()
    # 如果是群聊 ID (通常以 group_ 开头或纯数字)，提供不同的默认值
    default = {
        "favorability": 0.0, 
        "last_sign_in": "", 
        "first_sign_in": "",
        "action_points": 0, 
        "coins": 0, 
        "inventory": [],
        "total_sign_ins": 0,
        "achievements": [],
        "blacklist_count": 0,
        "is_perm_blacklisted": False,
        "nickname": "",
        "last_work_time": "",
        "remaining_works": 1,
        "custom_title": "",     # 自定义头衔
        "bank_coins": 0,        # 银行存款
        "last_rob_time": 0,     # 上次抢劫时间 (timestamp)
        "bank_history": [],      # 银行流水明细
        "wallet_history": []     # 钱包流水明细 (所有涉及 coins 的变动)
    }
    if user_id.startswith("group_"):
        default = {"favorability": 100.0, "daily_fav_count": 0.0, "last_update": ""}
    
    # 兼容旧数据，补齐缺失字段
    if user_id in data and not user_id.startswith("group_"):
        changed = False
        # 批量检查并设置默认值
        for key, value in default.items():
            if key not in data[user_id]:
                data[user_id][key] = value
                changed = True
        
        if changed:
            save_data(data)
            
    return data.get(user_id, default)

def update_user_data(user_id: str, reason: str = None, **kwargs):
    """
    更新单个用户或群聊的数据
    reason: 资金变动原因，如果提供了该参数且 coins 发生变动，将自动记录到 wallet_history
    """
    data = load_data()
    if user_id not in data:
        default = {
            "favorability": 0.0, 
            "last_sign_in": "", 
            "first_sign_in": "",
            "action_points": 0, 
            "coins": 0, 
            "inventory": [],
            "total_sign_ins": 0,
            "achievements": [],
            "blacklist_count": 0,
            "is_perm_blacklisted": False,
            "nickname": "",
            "last_work_time": "",
            "remaining_works": 1,
            "bank_coins": 0,
            "custom_title": "",
            "last_rob_time": 0,
            "fortune": {},            # 运势
            "duel_stats": {"win": 0, "loss": 0}, # 决斗数据
            "lottery": {"tickets": 0, "last_buy_date": ""}, # 彩票数据
            "achievement_progress": {"red_packet_total": 0, "steal_success": 0, "consecutive_fails": 0}, # 成就进度
            "bank_history": [],       # 银行流水明细
            "wallet_history": []      # 钱包流水明细
        }
        if user_id.startswith("group_"):
            default = {"favorability": 100.0, "daily_fav_count": 0.0, "last_update": ""}
        data[user_id] = default
    
    # 检查是否需要记录钱包流水
    if reason and "coins" in kwargs and not user_id.startswith("group_"):
        old_coins = data[user_id].get("coins", 0)
        new_coins = kwargs["coins"]
        diff = new_coins - old_coins
        
        if diff != 0:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            history = data[user_id].get("wallet_history", [])
            history.append({
                "time": now_str,
                "type": reason,
                "amount": diff,
                "balance": new_coins
            })
            # 保留最近 50 条
            if len(history) > 50:
                history = history[-50:]
            data[user_id]["wallet_history"] = history

    # 更新数据
    for key, value in kwargs.items():
        if value is not None:
            data[user_id][key] = value
        
    save_data(data)
