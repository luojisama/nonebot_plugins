from pydantic import BaseModel

class Config(BaseModel):
    bili_bangumi_page_size: int = 10
    bili_bangumi_cache_time: int = 3600  # 缓存时间（秒）
