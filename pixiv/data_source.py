import httpx
import asyncio
import hashlib
from datetime import datetime
from typing import List, Optional, Dict, Any
from nonebot import get_driver, logger, get_plugin_config
from .config import Config
from .model import Illust, User

global_config = get_plugin_config(Config)

class PixivClient:
    def __init__(self):
        self.refresh_token = global_config.pixiv_refresh_token
        self.proxy = global_config.pixiv_proxy
        if not self.proxy:
            self.proxy = None
        self.access_token: Optional[str] = None
        
        # Updated to match nonebot_plugin_pixivbot credentials
        self.client_id = "MOBrBDS8blbauoSck0ZfDbtuzpyT"
        self.client_secret = "lsACyCD94FhDUtGTXi3QzcFE2uU1hqtDaKeqrdwj"
        
        self.headers = {
            "User-Agent": "PixivAndroidApp/5.0.234 (Android 11; Pixel 5)",
            "Referer": "https://app-api.pixiv.net/",
            "Accept-Language": "zh-CN"
        }

    def _get_client_hash_headers(self) -> Dict[str, str]:
        # ISO 8601 format: YYYY-MM-DDTHH:mm:ss+00:00
        time_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")
        hash_secret = "28c1fdd170a5204386cb1313c7077b34f83e4aaf4aa829ce78c231e05b0bae2c"
        m = hashlib.md5()
        m.update((time_str + hash_secret).encode('utf-8'))
        client_hash = m.hexdigest()
        
        return {
            "X-Client-Time": time_str,
            "X-Client-Hash": client_hash
        }
        
    async def _get_auth_headers(self) -> Dict[str, str]:
        if not self.access_token:
            await self.auth()
        headers = self.headers.copy()
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        headers.update(self._get_client_hash_headers())
        return headers

    async def auth(self):
        if not self.refresh_token:
            logger.warning("Pixiv refresh_token 未配置，无法进行认证")
            return

        url = "https://oauth.secure.pixiv.net/auth/token"
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "include_policy": "true"
        }
        
        headers = {
            "User-Agent": "PixivAndroidApp/5.0.234 (Android 11; Pixel 5)",
        }

        try:
            # trust_env=False ensures we only use the explicitly provided proxy
            async with httpx.AsyncClient(proxy=self.proxy, timeout=20, verify=False) as client:
                resp = await client.post(url, data=data, headers=headers)
                if resp.status_code == 200:
                    json_data = resp.json()
                    self.access_token = json_data["response"]["access_token"]
                    self.refresh_token = json_data["response"]["refresh_token"]
                    self._update_env_file(self.refresh_token)
                    logger.info("Pixiv 认证成功")
                else:
                    logger.error(f"Pixiv 认证失败: {resp.text}")
                    # If invalid_grant, token is dead.
                    if "invalid_grant" in resp.text:
                         logger.error("Refresh Token 已失效，请重新使用 pixiv login 登录")
        except Exception as e:
            logger.error(f"Pixiv 认证异常: {repr(e)}")

    async def _request(self, method: str, url: str, params: dict = None, retry: int = 1) -> Optional[dict]:
        headers = await self._get_auth_headers()
        try:
            async with httpx.AsyncClient(proxy=self.proxy, timeout=20, verify=False) as client:
                resp = await client.request(method, url, params=params, headers=headers)
                
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code in [400, 401, 403]:
                    # 400 is often invalid_grant for token endpoints, but for API it might be parameter error
                    # However, Pixiv API sometimes returns 400 for token errors too.
                    # Check body if possible, but safely assume we can try refresh if retry > 0
                    if retry > 0:
                        logger.warning(f"Pixiv Token 可能已过期 (Status: {resp.status_code})，尝试刷新...")
                        self.access_token = None # Force refresh
                        await self.auth()
                        return await self._request(method, url, params, retry - 1)
                    else:
                        logger.error(f"Pixiv 请求失败 (Token 失效?): {resp.text}")
                else:
                    logger.error(f"Pixiv 请求失败: {resp.text}")
        except Exception as e:
            logger.error(f"Pixiv 请求异常: {repr(e)}")
        return None

    async def login_with_code(self, code: str, code_verifier: str) -> bool:
        url = "https://oauth.secure.pixiv.net/auth/token"
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": "https://app-api.pixiv.net/web/v1/users/auth/pixiv/callback",
            "include_policy": "true"
        }
        headers = {
            "User-Agent": "PixivAndroidApp/5.0.234 (Android 11; Pixel 5)",
        }
        
        try:
            async with httpx.AsyncClient(proxy=self.proxy, timeout=20, verify=False) as client:
                resp = await client.post(url, data=data, headers=headers)
                if resp.status_code == 200:
                    json_data = resp.json()
                    new_refresh_token = json_data["response"]["refresh_token"]
                    self.refresh_token = new_refresh_token
                    self.access_token = json_data["response"]["access_token"]
                    
                    # Save to .env.prod
                    self._update_env_file(new_refresh_token)
                    
                    logger.info("Pixiv 登录成功，Token 已更新")
                    return True
                else:
                    logger.error(f"Pixiv 登录失败: {resp.text}")
        except Exception as e:
            logger.error(f"Pixiv 登录异常: {repr(e)}")
        return False

    async def login_with_token(self, refresh_token: str) -> bool:
        """Verify and save a manually provided refresh token"""
        old_token = self.refresh_token
        self.refresh_token = refresh_token
        self.access_token = None # Force refresh
        
        # Try to auth with new token
        await self.auth()
        
        if self.access_token:
            # Success
            self._update_env_file(self.refresh_token)
            logger.info("Pixiv Token 手动更新成功")
            return True
        else:
            # Failed, revert
            self.refresh_token = old_token
            logger.error("Pixiv Token 手动更新失败: 无效的 Refresh Token")
            return False

    def _update_env_file(self, refresh_token: str):
        import os
        # Try to find the env file
        env_path = ".env.prod"
        if not os.path.exists(env_path):
            if os.path.exists(".env"):
                env_path = ".env"
            elif os.path.exists(".env.dev"):
                env_path = ".env.dev"
        
        try:
            lines = []
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            
            new_lines = []
            found = False
            for line in lines:
                if line.startswith("PIXIV_REFRESH_TOKEN="):
                    new_lines.append(f"PIXIV_REFRESH_TOKEN={refresh_token}\n")
                    found = True
                else:
                    new_lines.append(line)
            
            if not found:
                new_lines.append(f"PIXIV_REFRESH_TOKEN={refresh_token}\n")
                
            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
                
        except Exception as e:
            logger.error(f"更新 .env 文件失败: {e}")

    async def get_illust(self, illust_id: int) -> Optional[Illust]:
        url = f"https://app-api.pixiv.net/v1/illust/detail?illust_id={illust_id}"
        data = await self._request("GET", url)
        if data:
            return Illust.parse_obj(data["illust"])
        return None

    async def recommended_illusts(self, count: int = 30) -> List[Illust]:
        url = "https://app-api.pixiv.net/v1/illust/recommended"
        params = {
            "include_ranking_label": "true",
            "filter": "for_ios",
            "include_privacy_policy": "true"
        }
        data = await self._request("GET", url, params=params)
        if data:
            illusts = [Illust.parse_obj(i) for i in data.get("illusts", [])]
            return illusts[:count]
        return []

    async def search_illust(self, word: str, sort: str = "date_desc", search_target: str = "partial_match_for_tags", offset: int = 0) -> List[Illust]:
        """
        search_target: partial_match_for_tags, exact_match_for_tags, title_and_caption
        sort: date_desc, date_asc, popular_desc (premium only)
        """
        url = "https://app-api.pixiv.net/v1/search/illust"
        params = {
            "word": word,
            "search_target": search_target,
            "sort": sort,
            "filter": "for_ios",
            "offset": offset
        }
        data = await self._request("GET", url, params=params)
        if data:
            return [Illust.parse_obj(i) for i in data.get("illusts", [])]
        return []

    async def search_user(self, word: str) -> List[User]:
        url = "https://app-api.pixiv.net/v1/search/user"
        params = {
            "word": word,
            "filter": "for_ios"
        }
        data = await self._request("GET", url, params=params)
        if data:
            # data['user_previews'] contains 'user' object
            return [User.parse_obj(u["user"]) for u in data.get("user_previews", [])]
        return []

    async def user_illusts(self, user_id: int) -> List[Illust]:
        url = f"https://app-api.pixiv.net/v1/user/illusts?user_id={user_id}&type=illust"
        data = await self._request("GET", url)
        if data:
             return [Illust.parse_obj(i) for i in data.get("illusts", [])]
        return []

    async def illust_ranking(self, mode: str = "day", offset: int = 0) -> List[Illust]:
        """
        mode: day, week, month, day_male, day_female, week_original, week_rookie, day_r18, day_male_r18, day_female_r18, week_r18, week_r18g
        """
        url = "https://app-api.pixiv.net/v1/illust/ranking"
        params = {
            "mode": mode,
            "filter": "for_ios",
            "offset": offset
        }
        data = await self._request("GET", url, params=params)
        if data:
            return [Illust.parse_obj(i) for i in data.get("illusts", [])]
        return []

    async def download_image(self, url: str) -> Optional[bytes]:
        headers = {
            "Referer": "https://app-api.pixiv.net/"
        }
        try:
            async with httpx.AsyncClient(proxy=self.proxy, timeout=30, verify=False) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    return resp.content
        except Exception as e:
            logger.error(f"下载图片失败: {e}")
        return None

pixiv_client = PixivClient()
