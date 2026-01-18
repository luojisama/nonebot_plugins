import asyncio
import httpx
from playwright.async_api import async_playwright
from typing import List, Dict, Any
import logging
import urllib.parse
import json
import os
from pathlib import Path

logger = logging.getLogger("nonebot")
DATA_PATH = Path("data/cs_pro")
DATA_PATH.mkdir(parents=True, exist_ok=True)
PW_SESSION_FILE = DATA_PATH / "pw_session.json"

class FiveEEventCrawler:
    def __init__(self):
        self.events_url = "https://event.5eplay.com/csgo/events"
        self.matches_url = "https://event.5eplay.com/csgo/matches?grade=1%2C7%2C2%2C3%2C8%2C9"

    async def get_matches(self) -> List[Dict[str, Any]]:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            try:
                await page.goto(self.matches_url, wait_until="networkidle", timeout=30000)
                
                matches = await page.evaluate(r"""() => {
                    const results = [];
                    const matchBlocks = document.querySelectorAll('.match-item');
                    
                    matchBlocks.forEach(block => {
                        const date = block.querySelector('.match-time-title')?.innerText.trim() || "";
                        const rows = block.querySelectorAll('.match-item-row');
                        
                        rows.forEach(row => {
                            const time = row.querySelector('.match-time-star div')?.innerText.trim() || "";
                            const format = row.querySelector('.match-rule')?.innerText.trim() || "BO3";
                            
                            const teamDivs = row.querySelectorAll('.match-team .cp');
                            const team1 = {
                                name: teamDivs[0]?.querySelector('p')?.innerText.trim() || "TBD",
                                logo: teamDivs[0]?.querySelector('img')?.src || ""
                            };
                            const team2 = {
                                name: teamDivs[1]?.querySelector('p')?.innerText.trim() || "TBD",
                                logo: teamDivs[1]?.querySelector('img')?.src || ""
                            };
                            
                            const scoreDivs = row.querySelectorAll('.all-score-box .all-score div');
                            const score1 = scoreDivs[0]?.innerText.trim() || "--";
                            const score2 = scoreDivs[1]?.innerText.trim() || "--";
                            
                            const status = row.querySelector('.match-btn')?.innerText.trim() || "未开始";
                            const tournament = row.querySelector('.match-system .tleft .ellip')?.innerText.trim() || "";
                            const tournament_icon = row.querySelector('.match-system .tleft .ellip img')?.src || "";
                            
                            results.push({
                                date, time, format,
                                team1, team2,
                                score1, score2,
                                status, tournament, tournament_icon
                            });
                        });
                    });
                    return results;
                }""")
                return matches
            except Exception as e:
                logger.error(f"Error crawling 5E matches: {e}")
                return []
            finally:
                await browser.close()

    async def get_events(self) -> List[Dict[str, Any]]:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            try:
                await page.goto(self.events_url, wait_until="networkidle", timeout=30000)
                
                # Extract events using a more precise script
                events = await page.evaluate(r"""() => {
                    const results = [];
                    
                    // 5EPlay event items are usually in .tournament-item or similar
                    // Let's find all blocks that contain '级赛事'
                    const allDivs = Array.from(document.querySelectorAll('div, section, article'));
                    const eventBlocks = allDivs.filter(el => {
                        const text = el.innerText || "";
                        return (text.includes('S级赛事') || text.includes('A级赛事')) && 
                               el.children.length > 5 && el.children.length < 20;
                    });
                    
                    eventBlocks.forEach(block => {
                        const text = block.innerText || "";
                        const lines = text.split('\n').map(l => l.trim()).filter(l => l.length > 0);
                        
                        const levelIdx = lines.findIndex(l => l.includes('级赛事'));
                        if (levelIdx !== -1) {
                            const level = lines[levelIdx];
                            const title = lines[levelIdx - 1] || "未知赛事";
                            
                            // Status is often '进行中', '未开始', '已结束'
                            // In some cases, it might be shifted. Let's look for known statuses.
                            let status = "未知";
                            const knownStatuses = ['进行中', '未开始', '已结束', '报名中'];
                            const foundStatus = lines.find(l => knownStatuses.includes(l));
                            if (foundStatus) {
                                status = foundStatus;
                            } else if (lines[levelIdx + 1] && !lines[levelIdx + 1].includes('-')) {
                                // If not a date range, assume it's status
                                status = lines[levelIdx + 1];
                            }
                            
                            // Time usually matches \d{2}-\d{2} - \d{2}-\d{2}
                            const time = lines.find(l => /\d{2}-\d{2}/.test(l)) || "";
                            
                            let location = "";
                            const locationIdx = lines.indexOf('地点');
                            if (locationIdx !== -1 && lines[locationIdx - 1]) {
                                location = lines[locationIdx - 1];
                            } else {
                                // Fallback: look for lines that look like locations (often contain countries or cities)
                                const possibleLocation = lines.find(l => l.includes('，') || l.includes(',') || l.includes('线上'));
                                if (possibleLocation && possibleLocation !== title && possibleLocation !== status && !possibleLocation.includes('级赛事')) {
                                    location = possibleLocation;
                                }
                            }
                            
                            let prize = "";
                            const prizeIdx = lines.indexOf('奖金');
                            if (prizeIdx !== -1 && lines[prizeIdx - 1]) {
                                prize = lines[prizeIdx - 1];
                            } else {
                                // Fallback: look for $ or '晋级'
                                const possiblePrize = lines.find(l => l.includes('$') || l.includes('晋级') || l.includes('¥'));
                                if (possiblePrize && possiblePrize !== title && possiblePrize !== status && !possiblePrize.includes('级赛事')) {
                                    prize = possiblePrize;
                                }
                            }

                            results.push({ title, level, status, time, location, prize });
                        }
                    });

                    return results;
                }""")
                
                # Filter for unique events and S/A level
                unique_events = []
                seen_titles = set()
                for e in events:
                    if e['title'] not in seen_titles:
                        unique_events.append(e)
                        seen_titles.add(e['title'])
                
                return unique_events
                
            except Exception as e:
                logger.error(f"Error crawling 5E events: {e}")
                return []
            finally:
                await browser.close()

class FiveECrawler:
    def __init__(self):
        self.base_url = "https://arena-next.5eplaycdn.com/home/personalInfo?domain={domain}&uuid=null"
        self.search_url = "https://arena.5eplay.com/search?keywords={keywords}"
        
    async def search_player(self, keywords: str):
        """
        Search for players by keywords and return a list of potential domains.
        """
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            url = self.search_url.format(keywords=urllib.parse.quote(keywords))
            await page.goto(url, wait_until="networkidle")
            await asyncio.sleep(2)
            
            users = await page.evaluate("""() => {
                const results = [];
                const items = document.querySelectorAll('div[class*="userItem"], a[href*="/data/player/"]');
                for (const item of items) {
                    let link, name, avatar;
                    if (item.tagName === 'A') {
                        link = item;
                        name = item.innerText.trim();
                    } else {
                        link = item.querySelector('a[href*="/data/player/"]');
                        const text = item.innerText || "";
                        name = text.trim().split('\\n')[0];
                        const img = item.querySelector('img');
                        if (img) avatar = img.src;
                    }
                    
                    if (link) {
                        const href = link.getAttribute('href');
                        if (!href) continue;
                        const parts = href.split('/');
                        const domain = parts[parts.length - 1];
                        if (domain && name && !results.find(r => r.domain === domain)) {
                            results.push({name, domain, avatar});
                        }
                    }
                }
                return results;
            }""")
            
            await browser.close()
            return users

    async def get_player_data(self, domain: str):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            player_data = {
                "nickname": "Unknown",
                "avatar": "",
                "stats": {}
            }
            
            # Listen for API responses
            async def handle_response(response):
                # logger.debug(f"Response: {response.url}")
                if "player_career" in response.url:
                    try:
                        data = await response.json()
                        career_data = data.get("data", {}).get("career_data", {})
                        player_data["stats"]["career"] = career_data
                        
                        # Fallback for role data if present in career
                        if not player_data["stats"].get("role") and data.get("data", {}).get("role"):
                            role_data = data.get("data", {}).get("role")
                            if role_data.get("role_name") or role_data.get("name"):
                                player_data["stats"]["role"] = {
                                    "role_name": role_data.get("role_name") or role_data.get("name"),
                                    "role_icon": role_data.get("role_icon") or role_data.get("icon"),
                                    "role_desc": role_data.get("role_desc") or role_data.get("description"),
                                    "role_tags": role_data.get("role_tags") or role_data.get("tags") or [],
                                    "player_template_name": role_data.get("player_template_name") or role_data.get("tpl_name"),
                                    "score_level": role_data.get("score_level") or role_data.get("level_name"),
                                    "score": role_data.get("score"),
                                    "rarity": role_data.get("rarity")
                                }
                    except:
                        pass
                elif "player/best_season" in response.url:
                    try:
                        data = await response.json()
                        player_data["stats"]["best_season"] = data.get("data", {})
                    except:
                        pass
                elif "player/home" in response.url:
                    try:
                        data = await response.json()
                        home_data = data.get("data", {})
                        player_data["stats"]["home"] = home_data
                        
                        # Fallback for role data if present in home info
                        if not player_data["stats"].get("role") and home_data.get("role"):
                            role_data = home_data.get("role")
                            if role_data.get("role_name") or role_data.get("name"):
                                player_data["stats"]["role"] = {
                                    "role_name": role_data.get("role_name") or role_data.get("name"),
                                    "role_icon": role_data.get("role_icon") or role_data.get("icon"),
                                    "role_desc": role_data.get("role_desc") or role_data.get("description"),
                                    "role_tags": role_data.get("role_tags") or role_data.get("tags") or [],
                                    "player_template_name": role_data.get("player_template_name") or role_data.get("tpl_name"),
                                    "score_level": role_data.get("score_level") or role_data.get("level_name"),
                                    "score": role_data.get("score"),
                                    "rarity": role_data.get("rarity")
                                }
                    except:
                        pass
                elif "role_position" in response.url:
                    try:
                        resp_data = await response.json()
                        role_data = resp_data.get("data")
                        if role_data and isinstance(role_data, dict):
                            # Map API keys to template keys if necessary
                            mapped_role = {
                                "role_name": role_data.get("role_name") or role_data.get("name"),
                                "role_icon": role_data.get("role_icon") or role_data.get("icon"),
                                "role_desc": role_data.get("role_desc") or role_data.get("description"),
                                "role_tags": role_data.get("role_tags") or role_data.get("tags") or [],
                                "player_template_name": role_data.get("player_template_name") or role_data.get("tpl_name") or role_data.get("template_name"),
                                "score_level": role_data.get("score_level") or role_data.get("level_name") or role_data.get("level"),
                                "score": role_data.get("score"),
                                "rarity": role_data.get("rarity")
                            }
                            # Only set if we have a role name
                            if mapped_role["role_name"]:
                                player_data["stats"]["role"] = mapped_role
                                logger.info(f"Captured role data: {mapped_role['role_name']}")
                    except Exception as e:
                        logger.error(f"Error parsing role_position: {e}")
                elif "player_match" in response.url:
                    try:
                        data = await response.json()
                        matches = data.get("data", {}).get("match_data", [])
                        player_data["stats"]["recent_matches"] = matches[:5]
                        
                        # Extract nickname and avatar from inferred_info if available
                        inferred = data.get("data", {}).get("inferred_info", {})
                        if inferred:
                            if inferred.get("nickname"):
                                player_data["nickname"] = inferred["nickname"]
                            if inferred.get("avatar"):
                                avatar = inferred["avatar"]
                                if avatar.startswith('//'):
                                    avatar = 'https:' + avatar
                                player_data["avatar"] = avatar
                    except:
                        pass

            page.on("response", handle_response)
            
            url = self.base_url.format(domain=domain)
            await page.goto(url, wait_until="networkidle")
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(3) # Increased wait for dynamic data
            
            # Try to get nickname and avatar from the page with more flexible selectors
            try:
                # Look for elements that might contain the nickname
                nickname_el = await page.query_selector('[class*="name_box"]') or \
                              await page.query_selector('[class*="nickname"]') or \
                              await page.query_selector('[class*="player_name"]') or \
                              await page.query_selector('h1')
                
                if nickname_el:
                    text = await nickname_el.inner_text()
                    # If there's multiple lines, the first one is usually the nickname
                    player_data["nickname"] = text.split('\n')[0].strip()
                
                # Look for elements that might be the avatar
                avatar_el = await page.query_selector('[class*="avatar"] img') or \
                            await page.query_selector('img[src*="avatar"]') or \
                            await page.query_selector('img[src*="disguise"]')
                
                if avatar_el:
                    src = await avatar_el.get_attribute("src")
                    if src:
                        # Ensure it's an absolute URL
                        if src.startswith('//'):
                            src = 'https:' + src
                        elif src.startswith('/'):
                            src = 'https://arena-next.5eplay.com' + src
                        player_data["avatar"] = src
            except:
                pass
                
            await browser.close()
            return player_data

class PWCrawler:
    def __init__(self):
        self.base_url = "https://api.wmpvp.com/api"
        self.passport_url = "https://passport.pwesports.cn"
        self.app_engine_url = "https://appengine.wmpvp.com"
        self.appversion = "3.5.4.172"
        self.token = "3dbb2485ah77nn99950bf805bbr20ff13f5d355" # Default/Fallback token
        self.my_steam_id = 76561198929215155 # Default/Fallback steamId
        self._load_session()
        
    def _load_session(self):
        """从文件加载 Session"""
        if PW_SESSION_FILE.exists():
            try:
                with open(PW_SESSION_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.token = data.get("token", self.token)
                    self.my_steam_id = data.get("steam_id", self.my_steam_id)
                logger.info("Loaded PW session from file.")
            except Exception as e:
                logger.error(f"Error loading PW session: {e}")

    def _save_session(self):
        """保存 Session 到文件"""
        try:
            with open(PW_SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "token": self.token,
                    "steam_id": self.my_steam_id
                }, f, ensure_ascii=False, indent=4)
            logger.info("Saved PW session to file.")
        except Exception as e:
            logger.error(f"Error saving PW session: {e}")

    def set_session(self, token: str, steam_id: int):
        self.token = token
        self.my_steam_id = steam_id
        self._save_session()

    async def login(self, mobile: str, code: str) -> Dict[str, Any]:
        """登录完美平台获取 token"""
        url = f"{self.passport_url}/account/login"
        payload = {
            "appId": 2,
            "mobilePhone": mobile,
            "securityCode": code
        }
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if data.get("code") == 0:
                    acc_info = data["result"]["loginResult"]["accountInfo"]
                    self.set_session(acc_info["token"], acc_info["steamId"])
                    return acc_info
                return {"error": data.get("description", "登录失败")}
            except Exception as e:
                logger.error(f"PW Login Error: {e}")
                return {"error": str(e)}

    async def search_player(self, keyword: str) -> List[Dict[str, Any]]:
        """搜索完美世界平台玩家"""
        url = f"{self.app_engine_url}/steamcn/app/search/user"
        headers = {
            "appversion": self.appversion,
            "token": self.token
        }
        payload = {
            "keyword": keyword,
            "page": 1
        }
        async with httpx.AsyncClient(headers=headers) as client:
            try:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if data.get("code") == 1:
                    return data.get("result", [])
                return []
            except Exception as e:
                logger.error(f"Error searching PW player: {e}")
                return []

    async def get_player_data(self, target_steam_id: str) -> Dict[str, Any]:
        """获取玩家详细战绩"""
        url = f"{self.base_url}/csgo/home/pvp/detailStats"
        headers = {
            "appversion": self.appversion,
            "token": self.token,
            "platform": "android",
            "Content-Type": "application/json"
        }
        payload = {
            "mySteamId": int(self.my_steam_id),
            "toSteamId": int(target_steam_id),
            "accessToken": "",
            "csgoSeasonId": ""
        }
        async with httpx.AsyncClient(headers=headers, timeout=10.0) as client:
            try:
                # Fetch detailed stats
                resp = await client.post(url, json=payload)
                data = resp.json()
                
                if data.get("statusCode") == 0:
                    pw_data = data.get("data", {})
                    if not pw_data:
                        logger.warning(f"PW API returned success but empty data for {target_steam_id}")
                        return {"error": "API 返回数据为空，请检查玩家是否在该赛季有战绩"}
                        
                    # Fetch recent matches
                    recent_matches = await self.get_recent_matches(target_steam_id)
                    
                    return {
                        "summary": {
                            "nickname": pw_data.get("name"),
                            "avatarUrl": pw_data.get("avatar"),
                            "steamId": pw_data.get("steamId"),
                            "description": pw_data.get("summary")
                        },
                        "stats": pw_data,
                        "recent_matches": recent_matches
                    }
                else:
                    error_msg = data.get("errorMessage") or f"Status Code: {data.get('statusCode')}"
                    logger.error(f"PW API Error for {target_steam_id}: {error_msg}")
                    return {"error": error_msg}
            except Exception as e:
                logger.error(f"Error getting PW player data: {e}")
                return {"error": f"网络请求失败: {str(e)}"}

    async def get_recent_matches(self, target_steam_id: str, count: int = 5) -> List[Dict[str, Any]]:
        """获取玩家最近比赛记录"""
        url = f"{self.base_url}/csgo/home/match/list"
        headers = {
            "appversion": self.appversion,
            "token": self.token,
            "platform": "android"
        }
        payload = {
            "csgoSeasonId": "recent",
            "dataSource": 3,
            "mySteamId": int(self.my_steam_id),
            "page": 1,
            "pageSize": count,
            "pvpType": -1,
            "toSteamId": int(target_steam_id)
        }
        async with httpx.AsyncClient(headers=headers, timeout=10.0) as client:
            try:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if data.get("statusCode") == 0:
                    match_list = data.get("data", {}).get("matchList", [])
                    return match_list
                else:
                    logger.error(f"PW Match List API Error: {data.get('errorMessage')}")
                    return []
            except Exception as e:
                logger.error(f"Error getting PW recent matches: {e}")
                return []
