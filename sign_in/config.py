from pathlib import Path
from pydantic import BaseModel

class Config(BaseModel):
    sign_in_data_path: Path = Path(__file__).parent / "data" / "user_data.json"
    hitokoto_api_url: str = "http://127.0.0.1:4399/v2/hitokoto"
    hitokoto_backup_api_url: str = "https://60s.viki.moe/v2/hitokoto"
    
    # 好感度等级定义
    # (阈值, 等级名称)
    favorability_levels: list[tuple[float, str]] = [
        (0.0, "初见"),
        (25.0, "面熟"),
        (50.0, "初识"),
        (75.0, "普通"),
        (100.0, "熟悉"),
        (125.0, "信赖"),
        (150.0, "知心"),
        (175.0, "深厚"),
        (200.0, "挚友"),
        (225.0, "亲密"),
    ]
    
    # 金币等级定义
    # (阈值, 等级名称)
    coin_levels: list[tuple[int, str]] = [
        (0, "囊中羞涩"),
        (100, "初有资产"),
        (300, "小有积蓄"),
        (500, "财源广进"),
        (1000, "商贾之才"),
        (2000, "腰缠万贯"),
        (5000, "金玉满堂"),
        (10000, "富甲一方"),
        (50000, "富可敌国"),
    ]

def get_level_name(favorability: float) -> str:
    """根据好感度获取等级名称"""
    level_name = "陌生"
    for threshold, name in Config().favorability_levels:
        if favorability >= threshold:
            level_name = name
        else:
            break
    return level_name

def get_coin_level_name(coins: int) -> str:
    """根据金币数量获取等级名称"""
    level_name = "一贫如洗"
    for threshold, name in Config().coin_levels:
        if coins >= threshold:
            level_name = name
        else:
            break
    return level_name
