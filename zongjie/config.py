from pydantic import BaseModel, Field

class Config(BaseModel):
    zongjie_api_key: str = ""
    zongjie_base_url: str = "https://api.bltcy.ai"
    zongjie_model: str = "gpt-4o-mini"
    zongjie_api_type: str = "openai"  # openai 或 gemini
    zongjie_history_count: int = 150
