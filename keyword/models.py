from enum import Enum
from typing import List
from pydantic import BaseModel

class MatchType(str, Enum):
    EXACT = "exact"
    FUZZY = "fuzzy"

class ReplyType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    FACE = "face"

class Reply(BaseModel):
    type: ReplyType
    data: str  # 文字内容, 图片URL/路径, 或表情ID

class KeywordRule(BaseModel):
    id: str  # 唯一标识符
    keywords: List[str]
    match_type: MatchType = MatchType.FUZZY
    replies: List[Reply]
