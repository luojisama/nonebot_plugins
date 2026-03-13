from pydantic import BaseModel, Field


class Config(BaseModel):
    cs_pro_priority: int = Field(default=5)
    cs_pro_bind_db_path: str = Field(default="data/cs_pro/user_bindings.db")
    cs_pro_http_timeout: int = Field(default=15, ge=5, le=60)

    cs_pro_llm_enabled: bool = Field(default=True)
    cs_pro_llm_api_type: str = Field(default="openai")
    cs_pro_llm_api_url: str = Field(default="https://api.openai.com/v1")
    cs_pro_llm_api_key: str = Field(default="")
    cs_pro_llm_model: str = Field(default="gpt-4o-mini")
    cs_pro_llm_timeout: int = Field(default=30, ge=5, le=120)
    cs_pro_llm_system_prompt: str = Field(
        default=(
            "你是一名职业CS2战队的数据分析师。请根据提供的对局数据，进行深入的战术复盘和表现分析。"
            "输出必须是严格的JSON格式，包含以下字段："
            "1. title: 用8-16个字精准概括该玩家（主角）的本场表现风格（如“进攻端突破核心”或“防守端稳健支柱”）。"
            "2. detail: 撰写500字的详细分析报告。内容应包含："
            "   - 团队整体表现分析（攻防节奏、关键局势）。"
            "   - 主角（Player）的个人表现评价（Rating/ADR/KDA等数据的战术意义）。"
            "   - 队友与对手的关键互动或差距分析。"
            "   - 针对性的改进建议或战术调整方向。"
            "   - 语言风格需专业、客观、犀利，像是在战队复盘会上发言。"
        )
    )
