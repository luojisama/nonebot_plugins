from pydantic import BaseModel
from typing import List

class Config(BaseModel):
    # API 基础地址配置
    daily_tools_api_primary: str = "https://60s.viki.moe"
    daily_tools_api_backup: str = "http://127.0.0.1:4399"
    
    # Epic 免费游戏 API
    epic_free_games_api: str = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale=zh-CN&country=CN&allowCountries=CN"
