from nonebot import get_plugin_config
from pydantic import BaseModel
from typing import List, Optional

class Config(BaseModel):
    # API 基础地址 (默认兼容 OpenAI 格式)
    ai_painting_api_url: str = "https://api.bltcy.ai/v1/chat/completions"
    # API Key
    ai_painting_api_key: str = ""
    # 余额查询 Token (可选，如果不填则使用 API Key)
    ai_painting_balance_token: Optional[str] = "NMwudYZyZUeA+FT5P5IA9Py7KM9FV/A="
    # 余额查询 User ID (New-API-User header)
    ai_painting_balance_user_id: str = "63220"
    # 余额查询 API 地址 (默认为 柏拉图AI)
    ai_painting_balance_url: str = "https://api.bltcy.ai"
    # 模型名称
    ai_painting_model: str = "nano-banana-2-2k"
    # 最大尝试次数
    ai_painting_max_attempts: int = 2
    # 黑名单 (用户ID或群号)
    ai_painting_blacklist: List[str] = []
    # 模板保存路径
    ai_painting_template_path: str = "data/ai_painting/templates.json"
    
    # 代理
    ai_painting_proxy: str = ""
    # 绘图价格 (金币)
    ai_painting_price: int = 8

plugin_config = get_plugin_config(Config)
