import httpx
import asyncio
from typing import List, Optional
from nonebot import logger
from pydantic import BaseModel
from bs4 import BeautifulSoup
import re
from pathlib import Path

class EarthquakeInfo(BaseModel):
    id: str
    time: str
    magnitude: str
    depth: str
    location: str
    latitude: str
    longitude: str

class EarthquakeSource:
    def __init__(self):
        self.api_url = "https://news.ceic.ac.cn/index.html"
        self.last_id_path = Path("data/earthquake_monitor/last_id.txt")
        self.last_earthquake_id: Optional[str] = self._load_last_id()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

    def _load_last_id(self) -> Optional[str]:
        if self.last_id_path.exists():
            try:
                return self.last_id_path.read_text(encoding="utf-8").strip()
            except Exception:
                return None
        return None

    def _save_last_id(self, eq_id: str):
        try:
            self.last_id_path.parent.mkdir(parents=True, exist_ok=True)
            self.last_id_path.write_text(eq_id, encoding="utf-8")
        except Exception as e:
            logger.error(f"保存最后地震ID失败: {e}")

    def is_domestic(self, location: str) -> bool:
        """判断是否为国内地震"""
        keywords = [
            "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
            "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
            "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州",
            "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆", "台湾",
            "香港", "澳门", "中国", "东海", "南海", "黄海", "渤海"
        ]
        return any(k in location for k in keywords)

    async def fetch_latest(self) -> List[EarthquakeInfo]:
        """获取最新的地震信息 (解析 HTML)"""
        async with httpx.AsyncClient(timeout=10, headers=self.headers) as client:
            try:
                resp = await client.get(self.api_url)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "lxml")
                    table = soup.find("table", class_="news-table")
                    if not table:
                        return []
                    
                    results = []
                    rows = table.find_all("tr")[1:]  # 跳过表头
                    for row in rows:
                        tds = row.find_all("td")
                        if len(tds) < 6:
                            continue
                        
                        # 提取 ID (从链接中提取)
                        link = tds[5].find("a")
                        eq_id = ""
                        if link and "href" in link.attrs:
                            href = link.attrs["href"]
                            # https://news.ceic.ac.cn/CC.20260119212422.html -> CC.20260119212422
                            match = re.search(r"([^/]+)\.html$", href)
                            if match:
                                eq_id = match.group(1)
                        
                        if not eq_id:
                            # 如果没提取到 ID，用时间和地点生成一个
                            eq_id = f"{tds[1].get_text().strip()}_{tds[5].get_text().strip()}"

                        info = EarthquakeInfo(
                            id=eq_id,
                            time=tds[1].get_text().strip(),
                            magnitude=tds[0].get_text().strip(),
                            depth=tds[4].get_text().strip(),
                            location=tds[5].get_text().strip(),
                            latitude=tds[2].get_text().strip(),
                            longitude=tds[3].get_text().strip()
                        )
                        results.append(info)
                    return results
            except Exception as e:
                logger.error(f"解析地震 HTML 失败: {e}")
        return []

    async def get_new_earthquakes(self) -> List[EarthquakeInfo]:
        """获取未推送过的新地震 (仅筛选国内)"""
        latest_list = await self.fetch_latest()
        if not latest_list:
            return []

        if self.last_earthquake_id is None:
            # 第一次运行，记录最新的 ID 但不推送
            self.last_earthquake_id = latest_list[0].id
            self._save_last_id(self.last_earthquake_id)
            return []

        new_earthquakes = []
        for eq in latest_list:
            if eq.id == self.last_earthquake_id:
                break
            new_earthquakes.append(eq)

        if new_earthquakes:
            # 更新最新ID为列表第一个（即最新的那个），无论是否推送，都必须更新游标
            self.last_earthquake_id = latest_list[0].id
            self._save_last_id(self.last_earthquake_id)

        # 筛选国内地震进行推送
        domestic_earthquakes = [eq for eq in new_earthquakes if self.is_domestic(eq.location)]
        return domestic_earthquakes

    async def get_history(self, count: int = 5) -> List[EarthquakeInfo]:
        """获取历史地震信息 (仅筛选国内)"""
        latest_list = await self.fetch_latest()
        domestic_list = [eq for eq in latest_list if self.is_domestic(eq.location)]
        return domestic_list[:count]

earthquake_source = EarthquakeSource()
