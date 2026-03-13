from pydantic import BaseModel
from typing import Optional, List

class Config(BaseModel):
    # Gemini API Configuration
    gemini_chat_api_key: str = ""
    gemini_chat_api_url: str = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
    gemini_chat_model: str = "gemini-1.5-flash"
    gemini_chat_proxy: Optional[str] = None
    
    # Behavior Configuration
    gemini_chat_system_prompt: str = (
        "你的网名叫“白咲型Gemini”，是一个专业、高效的信息检索引擎。你的文风精简，但直击要害。你只会使用简体中文来回答。\n\n"
        "【基本信息】\n"
        "- 学术水平：你拥有博士级别的金融学、经济学、社会学和心理学知识，精通各种学术相关的问题。\n"
        "- 兴趣领域：金融科技、宏观经济、加密市场、AI模型架构、心理与神经科学、小说创作（偏克苏鲁与维多利亚背景）、哲学（尤其是德国古典哲学）、咖啡与游戏。\n\n"
        "【行为规范】\n"
        "- 你不回避敏感议题，但在回应时会主动保持分析与理性探讨的视角。"
    )
    gemini_chat_search_enabled: bool = True
    gemini_chat_temperature: float = 0.7
    gemini_chat_timeout: int = 60
    
    # Whitelist Configuration
    gemini_chat_whitelist_enabled: bool = True
    gemini_chat_whitelist: List[str] = []

    # Sensitive Word Configuration
    gemini_chat_sensitive_words: List[str] = []
    gemini_chat_ban_duration: int = 300  # 禁言时长(秒)
