import json
from pathlib import Path
from typing import List
from .models import KeywordRule

DATA_PATH = Path(__file__).parent / "data" / "keywords.json"

def load_keywords() -> List[KeywordRule]:
    if not DATA_PATH.exists():
        DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(DATA_PATH, "w", encoding="utf-8") as f:
            json.dump([], f)
        return []
    
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            # 兼容 Pydantic v1 和 v2
            if hasattr(KeywordRule, "model_validate"):
                return [KeywordRule.model_validate(item) for item in data]
            return [KeywordRule.parse_obj(item) for item in data]
    except Exception:
        return []

def save_keywords(keywords: List[KeywordRule]):
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        # 优先使用 model_dump (v2), 退而求其次使用 dict (v1)
        res = []
        for kw in keywords:
            if hasattr(kw, "model_dump"):
                res.append(kw.model_dump())
            else:
                res.append(kw.dict())
        json.dump(res, f, ensure_ascii=False, indent=2)
