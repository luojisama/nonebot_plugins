import httpx
import time
from typing import List, Optional, Dict
from nonebot import logger
from pydantic import BaseModel
from bs4 import BeautifulSoup
import re

class TyphoonInfo(BaseModel):
    id: str        # 台风编号 (如 2601)
    name: str      # 中文名
    en_name: str   # 英文名
    time: str      # 报时
    level: str     # 强度等级
    pressure: str  # 中心气压
    wind_speed: str # 最大风速
    location: str  # 当前位置 (经纬度)
    ref_pos: str   # 参考位置

class TyphoonSource:
    def __init__(self):
        # 中央气象台台风快讯页面
        self.url = "https://www.nmc.cn/publish/typhoon/typhoon_new.html"
        self.last_typhoon_id: Optional[str] = None
        self.last_update_time: Optional[str] = None
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

    async def fetch_latest(self) -> Optional[TyphoonInfo]:
        """从台风快讯页面解析最新台风信息"""
        async with httpx.AsyncClient(timeout=10, headers=self.headers) as client:
            try:
                resp = await client.get(self.url)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "lxml")
                    writing = soup.find("div", class_="writing")
                    if not writing:
                        return None
                    
                    text = writing.get_text("\n")
                    
                    # 使用正则提取各项数据
                    # 时    间： 19 日 20 时
                    # 命    名： “洛鞍”，NOKAEN
                    # 编    号： 2601 号
                    # 中心位置： 北纬17.0度、东经127.2度
                    # 强度等级： 热带风暴
                    # 最大风力： 8级， 18米/秒（约65公里/小时）
                    # 中心气压： 998 hPa
                    # 参考位置： 距离菲律宾马尼拉东偏北方向约720公里
                    
                    def extract(pattern, text):
                        match = re.search(pattern, text)
                        return match.group(1).strip() if match else "未知"

                    name_en = extract(r"命\s*名：\s*“([^”]+)”，([^ \n\r\t]+)", text)
                    name = extract(r"命\s*名：\s*“([^”]+)”", text)
                    en_name = extract(r"命\s*名：\s*“[^”]+”，([^ \n\r\t]+)", text)
                    
                    # 如果 en_name 没匹配到，尝试单独提取
                    if en_name == "未知":
                        en_name = extract(r"，([^ \n\r\t]+)\n", text)

                    tf_id = extract(r"编\s*号：\s*(\d+)\s*号", text)
                    update_time = extract(r"时\s*间：\s*([^\n]+)", text)
                    location = extract(r"中心位置：\s*([^\n]+)", text)
                    level = extract(r"强度等级：\s*([^\n]+)", text)
                    wind = extract(r"最大风力：\s*([^\n]+)", text)
                    pressure = extract(r"中心气压：\s*([^\n]+)", text)
                    ref_pos = extract(r"参考位置：\s*([^\n]+)", text)

                    return TyphoonInfo(
                        id=tf_id,
                        name=name,
                        en_name=en_name,
                        time=update_time,
                        level=level,
                        pressure=pressure,
                        wind_speed=wind,
                        location=location,
                        ref_pos=ref_pos
                    )
            except Exception as e:
                logger.error(f"解析台风 HTML 失败: {e}")
        return None

    async def get_new_updates(self) -> List[TyphoonInfo]:
        """获取最新的台风更新"""
        latest = await self.fetch_latest()
        if not latest:
            return []
        
        # 检查是否为新更新 (ID 不同或更新时间不同)
        if latest.id != self.last_typhoon_id or latest.time != self.last_update_time:
            # 只有当这是第一次运行且没有记录时，不推送（避免重启刷屏）
            if self.last_typhoon_id is None:
                self.last_typhoon_id = latest.id
                self.last_update_time = latest.time
                return []
            
            self.last_typhoon_id = latest.id
            self.last_update_time = latest.time
            return [latest]
            
        return []

typhoon_source = TyphoonSource()
