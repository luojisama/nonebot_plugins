from typing import Optional, List
from pydantic import BaseModel, Extra

class Config(BaseModel, extra=Extra.ignore):
    pixiv_refresh_token: str = ""
    pixiv_proxy: Optional[str] = None  # Default: None (use environment variables)
    pixiv_query_timeout: int = 10
    
    # Global Whitelist (optional, can be managed per group via command)
    pixiv_whitelist_groups: List[str] = []

    # Filtering
    pixiv_min_bookmarks: int = 0
    pixiv_min_view: int = 0
    pixiv_exclude_ai: bool = False

class Subscription(BaseModel):
    id: str  # group_id or user_id
    type: str  # "tag" or "user"
    value: str  # tag name or user_id
    interval: Optional[str] = None  # cron string or interval description
    next_run: Optional[float] = None
    
class Watch(BaseModel):
    id: str # group_id or user_id
    user_id: str # target pixiv user id
    last_illust_id: Optional[str] = None
