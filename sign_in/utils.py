import json
import os
import httpx
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
        "is_perm_blacklisted": False
    }
    if user_id.startswith("group_"):
        default = {"favorability": 100.0, "daily_fav_count": 0.0, "last_update": ""}
    
    # 兼容旧数据，补齐缺失字段
    if user_id in data and not user_id.startswith("group_"):
        changed = False
        if "coins" not in data[user_id]:
            data[user_id]["coins"] = 0
            changed = True
        if "inventory" not in data[user_id]:
            data[user_id]["inventory"] = []
            changed = True
        if "total_sign_ins" not in data[user_id]:
            data[user_id]["total_sign_ins"] = 0
            changed = True
        if "first_sign_in" not in data[user_id]:
            data[user_id]["first_sign_in"] = ""
            changed = True
        if "achievements" not in data[user_id]:
            data[user_id]["achievements"] = []
            changed = True
        if "blacklist_count" not in data[user_id]:
            data[user_id]["blacklist_count"] = 0
            changed = True
        if "is_perm_blacklisted" not in data[user_id]:
            data[user_id]["is_perm_blacklisted"] = False
            changed = True
        if changed:
            save_data(data)
            
    return data.get(user_id, default)

def update_user_data(user_id: str, favorability: float = None, last_sign_in: str = None, first_sign_in: str = None, daily_fav_count: float = None, last_update: str = None, action_points: int = None, coins: int = None, inventory: list = None, total_sign_ins: int = None, achievements: list = None, blacklist_count: int = None, is_perm_blacklisted: bool = None):
    """更新单个用户或群聊的数据"""
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
            "is_perm_blacklisted": False
        }
        if user_id.startswith("group_"):
            default = {"favorability": 100.0, "daily_fav_count": 0.0, "last_update": ""}
        data[user_id] = default
    
    if favorability is not None:
        data[user_id]["favorability"] = favorability
    if last_sign_in is not None:
        data[user_id]["last_sign_in"] = last_sign_in
    if first_sign_in is not None:
        data[user_id]["first_sign_in"] = first_sign_in
    if daily_fav_count is not None:
        data[user_id]["daily_fav_count"] = daily_fav_count
    if last_update is not None:
        data[user_id]["last_update"] = last_update
    if action_points is not None:
        data[user_id]["action_points"] = action_points
    if coins is not None:
        data[user_id]["coins"] = coins
    if inventory is not None:
        data[user_id]["inventory"] = inventory
    if total_sign_ins is not None:
        data[user_id]["total_sign_ins"] = total_sign_ins
    if achievements is not None:
        data[user_id]["achievements"] = achievements
    if blacklist_count is not None:
        data[user_id]["blacklist_count"] = blacklist_count
    if is_perm_blacklisted is not None:
        data[user_id]["is_perm_blacklisted"] = is_perm_blacklisted
        
    save_data(data)
