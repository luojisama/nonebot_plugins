from pydantic import BaseModel
from typing import Optional
from pathlib import Path

class Config(BaseModel):
    # API 相关配置
    user_persona_api_url: Optional[str] = "https://api.openai.com/v1"
    user_persona_api_key: Optional[str] = None
    user_persona_model: str = "gpt-4o-mini"
    
    # 消息记录配置
    user_persona_history_max: int = 70  # 满 70 条自动生成
    user_persona_data_path: str = "data/user_persona/data.json"
