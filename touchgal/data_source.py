from __future__ import annotations

from io import BytesIO
from typing import Any

import aiohttp
from PIL import Image, UnidentifiedImageError

# Enable AVIF decoding if available.
try:
    import pillow_avif  # noqa: F401
except Exception:
    pillow_avif = None


class NoGameFound(Exception):
    pass


class DownloadNotFound(Exception):
    pass


class APIError(Exception):
    pass


class TouchGalAPI:
    def __init__(self, timeout: int = 20) -> None:
        self.base_url = "https://www.touchgal.top/api"
        self.search_url = f"{self.base_url}/search"
        self.download_url = f"{self.base_url}/patch/resource"
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    async def search_game(self, keyword: str, limit: int, nsfw: bool) -> list[dict[str, Any]]:
        query_string = '[{"type":"keyword","name":"%s"}]' % keyword.replace('"', "")
        payload = {
            "queryString": query_string,
            "limit": limit,
            "searchOption": {
                "searchInIntroduction": True,
                "searchInAlias": True,
                "searchInTag": True,
            },
            "page": 1,
            "selectedType": "all",
            "selectedLanguage": "all",
            "selectedPlatform": "all",
            "sortField": "resource_update_time",
            "sortOrder": "desc",
            "selectedYears": ["all"],
            "selectedMonths": ["all"],
        }
        cookies = {
            "kun-patch-setting-store|state|data|kunNsfwEnable": "all" if nsfw else "sfw"
        }
        headers = {"Content-Type": "application/json"}

        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(
                    self.search_url, json=payload, headers=headers, cookies=cookies
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise APIError(f"search status {resp.status}: {text[:120]}")
                    data = await resp.json()
        except aiohttp.ClientError as e:
            raise APIError(f"network error: {e}") from e
        except Exception as e:
            raise APIError(f"search failed: {e}") from e

        games = data.get("galgames", []) if isinstance(data, dict) else []
        if not games:
            raise NoGameFound(f"未找到游戏: {keyword}")
        return games

    async def get_downloads(self, patch_id: int) -> list[dict[str, Any]]:
        params = {"patchId": patch_id}
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(self.download_url, params=params) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise APIError(f"download status {resp.status}: {text[:120]}")
                    data = await resp.json()
        except aiohttp.ClientError as e:
            raise APIError(f"network error: {e}") from e
        except Exception as e:
            raise APIError(f"download query failed: {e}") from e

        if not isinstance(data, list) or not data:
            raise DownloadNotFound(f"未找到 ID={patch_id} 的下载资源")
        return data

    async def fetch_cover(self, url: str) -> tuple[bytes | None, str | None]:
        if not url:
            return None, None
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return None, url
                    raw = await resp.read()
        except Exception:
            return None, url

        try:
            with Image.open(BytesIO(raw)) as img:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img.thumbnail((900, 900), Image.Resampling.BILINEAR)
                out = BytesIO()
                img.save(out, format="JPEG", quality=85)
                return out.getvalue(), None
        except (UnidentifiedImageError, OSError):
            return None, url
        except Exception:
            return None, url
