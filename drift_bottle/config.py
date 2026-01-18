from pydantic import BaseModel
from pathlib import Path

class Config(BaseModel):
    drift_bottle_data_dir: Path = Path("data/drift_bottle")
    drift_bottle_image_dir: Path = Path("data/drift_bottle/images")
    drift_bottle_json_path: Path = Path("data/drift_bottle/bottles.json")
