from pydantic import BaseModel
from typing import List, Optional

class Config(BaseModel):
    # API 相关配置 (默认复用拟人插件的或环境变量)
    user_analysis_api_url: Optional[str] = None
    user_analysis_api_key: Optional[str] = None
    user_analysis_model: str = "gpt-4o-mini"
    
    # 消息记录配置
    user_analysis_history_max: int = 200  # 每个用户最多记录的消息数
    user_analysis_history_path: str = "data/user_analysis/history.json"
