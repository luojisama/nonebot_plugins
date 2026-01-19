from pydantic import BaseModel, Field
from typing import List, Optional

class PluginConfig(BaseModel):
    daily_waifu_priority: int = 5
    daily_waifu_limit_per_day: int = 100
    daily_waifu_cache_path: str = "data/daily_waifu/cache.json"
    
class CharacterInfo(BaseModel):
    name: str
    source: str
    image_url: str
    desc: Optional[str] = None
    extra: Optional[str] = None
