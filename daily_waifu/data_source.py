import httpx
import random
import asyncio
import json
import os
from datetime import datetime
from typing import Optional, Dict
from pathlib import Path
from nonebot.log import logger
from .model import CharacterInfo

class WaifuDataSource:
    def __init__(self, cache_path: str):
        self.cache_path = Path(cache_path)
        self.cache: Dict[str, Dict] = {}
        self._load_cache()
        
    def _load_cache(self):
        if self.cache_path.exists():
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    self.cache = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load waifu cache: {e}")
                self.cache = {}
    
    def _save_cache(self):
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save waifu cache: {e}")

    def get_today_waifu(self, user_id: str) -> Optional[CharacterInfo]:
        today = datetime.now().strftime("%Y-%m-%d")
        user_cache = self.cache.get(user_id, {})
        
        if user_cache.get("date") == today:
            data = user_cache.get("data")
            if data:
                return CharacterInfo(**data)
        return None

    def save_today_waifu(self, user_id: str, waifu: CharacterInfo):
        today = datetime.now().strftime("%Y-%m-%d")
        self.cache[user_id] = {
            "date": today,
            "data": waifu.dict()
        }
        self._save_cache()

    async def download_image(self, url: str) -> Optional[bytes]:
        if not url:
            return None
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Referer": "https://anilist.co/"
        }
        
        async with httpx.AsyncClient(timeout=20, headers=headers) as client:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.content
            except Exception as e:
                logger.warning(f"Failed to download image {url}: {e}")
        return None

    async def fetch_waifu(self, tag: str = None) -> Optional[CharacterInfo]:
        # 尝试多个源
        # 优先使用 Anilist (因为支持性别过滤)
        sources = [self._fetch_from_anilist]
        
        # 增加重试机制，最多尝试 3 次，每次失败后稍微等待
        for i in range(3):
            for source in sources:
                try:
                    result = await source(tag)
                    if result:
                        return result
                except Exception as e:
                    logger.warning(f"Waifu API source failed (attempt {i+1}): {e}")
                    continue
            if i < 2:
                await asyncio.sleep(1)
                
        return None

    async def _fetch_from_jikan(self, tag: str = None) -> Optional[CharacterInfo]:
        # Jikan (MyAnimeList) API
        # 注意：Jikan 的 Random 接口不支持 tag，这里忽略 tag
        url = "https://api.jikan.moe/v4/random/characters"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                
                # 获取高清图
                images = data.get("images", {}).get("jpg", {})
                img_url = images.get("image_url", "")
                
                # 尝试获取作品信息 (Jikan Random Character 可能不直接返回作品，需要额外查询或解析)
                # 但 V4 Random 接口返回的数据中通常包含 "anime" 或 "manga" 列表
                # 不过 random/characters 现在的返回值结构里可能没有 anime 列表，只有关于角色的基本信息
                # 我们做个简单的检查
                
                source_name = "未知作品"
                # 实际上 Jikan V4 random/characters 返回数据确实不含 anime 关联信息
                # 所以我们可能需要换一个 API 或者接受“未知作品”
                # 为了质量，我们尝试用 search 接口 + 随机页数来模拟
                
                # 策略 B: Jikan Search
                # 如果有 tag (比如 "萝莉" -> search "loli" ? 不靠谱)
                # 让我们回退到使用 Anilist 作为主要带作品信息的源，Jikan 作为备用
                pass

                return CharacterInfo(
                    name=data.get("name", "未知角色") + f" ({data.get('name_kanji', '')})",
                    source=source_name, # Jikan Random Character 确实缺这个
                    image_url=img_url,
                    desc=data.get("about", "")[:100] + "..." if data.get("about") else "暂无介绍"
                )
        return None

    def _clean_description(self, desc: str) -> str:
        import re
        if not desc:
            return ""
        
        # 移除 HTML 标签
        desc = re.sub(r'<[^>]+>', '', desc)
        
        # 移除 Markdown 格式
        desc = desc.replace("__", "").replace("**", "")
        
        # 移除特定不想看到的内容 (如 Tokyo Ghoul 的 Quinque 介绍)
        # 移除以 "Quinque:" 开头的段落或句子直到行尾或特定结束符
        # 这里简单移除包含 "Quinque:" 的行
        lines = desc.split('\n')
        filtered_lines = []
        for line in lines:
            if "Quinque:" in line or "(Koukaku)" in line or "(Ukaku)" in line:
                continue
            # 移除类似 "Kishou Arima is a..." 这种可能的开场白，如果是用户特别反感的
            if "Kishou Arima is a" in line:
                continue
            filtered_lines.append(line)
        desc = '\n'.join(filtered_lines)
        
        # 移除剧透标记
        desc = re.sub(r'~!.*?~!|\|\|.*?\|\|', '', desc)
        
        # 压缩多余换行
        desc = re.sub(r'\n\s*\n', '\n', desc).strip()
        
        return desc[:100] + "..." if len(desc) > 100 else desc

    async def _fetch_from_anilist(self, tag: str = None) -> Optional[CharacterInfo]:
        # Anilist GraphQL
        url = "https://graphql.anilist.co"
        
        # 随机页码 (1-1000)
        page = random.randint(1, 1000)
        
        query = """
        query ($page: Int) {
            Page(page: $page, perPage: 30) {
                characters(sort: FAVOURITES_DESC) {
                    name {
                        full
                        native
                    }
                    gender
                    image {
                        large
                    }
                    description
                    dateOfBirth {
                        month
                        day
                    }
                    media(sort: FAVOURITES_DESC, perPage: 1) {
                        nodes {
                            title {
                                romaji
                                native
                                english
                            }
                        }
                    }
                }
            }
        }
        """
        
        variables = {"page": page}
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        async with httpx.AsyncClient(timeout=20, headers=headers) as client:
            try:
                resp = await client.post(url, json={"query": query, "variables": variables})
                if resp.status_code == 200:
                    data = resp.json()
                    char_list = data.get("data", {}).get("Page", {}).get("characters", [])
                    
                    # 客户端过滤性别
                    female_chars = [c for c in char_list if c.get("gender") == "Female"]
                    
                    if female_chars:
                        char = random.choice(female_chars)
                        
                        # 名字
                        name = char["name"]["full"]
                        if char["name"]["native"]:
                            name += f" ({char['name']['native']})"
                            
                        # 作品
                        source = "未知作品"
                        media_nodes = char.get("media", {}).get("nodes", [])
                        if media_nodes:
                            media = media_nodes[0]
                            source = media["title"].get("native") or media["title"].get("english") or media["title"].get("romaji")
                            
                        # 描述
                        desc = self._clean_description(char.get("description", ""))
                            
                        # 生日
                        dob = char.get("dateOfBirth", {})
                        extra = ""
                        if dob.get("month") and dob.get("day"):
                            extra = f"🎂 生日: {dob['month']}月{dob['day']}日"
                            
                        return CharacterInfo(
                            name=name,
                            source=source,
                            image_url=char["image"]["large"],
                            desc=desc,
                            extra=extra
                        )
                else:
                    logger.warning(f"Anilist API returned error {resp.status_code}: {resp.text}")
            except Exception as e:
                logger.warning(f"Anilist API request failed: {e}")
        return None
