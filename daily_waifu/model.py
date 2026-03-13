from pydantic import BaseModel, Field


class PluginConfig(BaseModel):
    """Config for daily_waifu (mudae mode)."""

    daily_waifu_priority: int = Field(default=5)
    data_path: str = Field(default="data/daily_waifu/mudae_state.json")
    image_dir: str = Field(default="data/daily_waifu/images")
    draw_hourly_limit: int = Field(default=5)
    claim_cooldown: int = Field(default=3600)
    harem_max_size: int = Field(default=10)
    custom_images_limit: int = Field(default=5)
    draw_cooldown: int = Field(default=2)
    ntr_chance: int = Field(default=10)
