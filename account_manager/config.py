from pydantic import BaseModel
from typing import Optional

class Config(BaseModel):
    qzone_cookie: Optional[str] = None
