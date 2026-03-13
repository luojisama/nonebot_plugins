import os
import json
import httpx
from pathlib import Path
from nonebot.adapters.onebot.v11 import MessageSegment
from .config import plugin_config

# 确保数据目录存在
DATA_DIR = Path("data/ai_painting")
DATA_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATE_FILE = Path(plugin_config.ai_painting_template_path)

def load_templates() -> dict:
    if not TEMPLATE_FILE.exists():
        return {}
    try:
        return json.loads(TEMPLATE_FILE.read_text(encoding="utf-8"))
    except:
        return {}

def save_templates(templates: dict):
    TEMPLATE_FILE.write_text(json.dumps(templates, ensure_ascii=False, indent=2), encoding="utf-8")

def get_avatar_url(user_id: str) -> str:
    """获取QQ头像URL"""
    return f"https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"

async def generate_image(prompt: str, n: int = 1, size: str = "1024x1024") -> list[str]:
    """调用 API 生成图片"""
    headers = {
        "Authorization": f"Bearer {plugin_config.ai_painting_api_key}",
        "Content-Type": "application/json"
    }
    
    # 构建兼容 OpenAI 的请求体
    payload = {
        "model": plugin_config.ai_painting_model,
        "prompt": prompt,
        "n": n,
        "size": size,
        "response_format": "url"  # 或者 "b64_json"
    }
    
    # 支持自定义额外参数 (如果用户在 API URL 中使用了 SD WebUI 等非标准格式，这里可能需要调整)
    # 目前仅支持 OpenAI 兼容格式
    
    try:
        async with httpx.AsyncClient(timeout=60, proxy=plugin_config.ai_painting_proxy or None) as client:
            resp = await client.post(plugin_config.ai_painting_api_url, json=payload, headers=headers)
            
            if resp.status_code != 200:
                raise Exception(f"API Error ({resp.status_code}): {resp.text}")
            
            data = resp.json()
            # OpenAI 格式: {"data": [{"url": "..."}, ...]}
            if "data" in data:
                return [item["url"] for item in data["data"]]
            else:
                raise Exception(f"Unknown response format: {data}")
                
    except Exception as e:
        raise e

def is_blacklisted(user_id: str, group_id: str = None) -> bool:
    """检查是否在黑名单"""
    if str(user_id) in plugin_config.ai_painting_blacklist:
        return True
    if group_id and str(group_id) in plugin_config.ai_painting_blacklist:
        return True
    return False
