from pydantic import BaseModel
from pathlib import Path

class Config(BaseModel):
    steam_api_key: str = ""
    steam_bind_path: Path = Path(__file__).parent / "data" / "binds.json"
