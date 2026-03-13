from typing import List, Optional
from pydantic import BaseModel

class IllustImageUrls(BaseModel):
    square_medium: str
    medium: str
    large: str
    original: Optional[str] = None

class IllustTag(BaseModel):
    name: str
    translated_name: Optional[str] = None

class Illust(BaseModel):
    id: int
    title: str
    type: str
    image_urls: IllustImageUrls
    caption: str
    restrict: int
    x_restrict: int = 0
    user: "User"
    tags: List[IllustTag]
    total_bookmarks: int
    total_view: int
    illust_ai_type: int = 0
    create_date: str
    page_count: int
    meta_single_page: dict = {}
    meta_pages: list = []
    
    @property
    def is_r18(self) -> bool:
        # 1. Check API restrictions (Disabled by user request: only filter by tags)
        # if self.restrict > 0 or self.x_restrict > 0:
        #     return True
            
        # 2. Check Tags (Only source of truth now)
        # Sometimes API fields might be delayed or inconsistent, tags are usually reliable
        # Checked against nonebot_plugin_pixivbot logic: it checks both name and translated_name
        for tag in self.tags:
            if tag.name.upper() in ["R-18", "R-18G"]:
                return True
            if tag.translated_name and tag.translated_name.upper() in ["R-18", "R-18G"]:
                return True
        
        return False

    @property
    def link(self) -> str:
        return f"https://www.pixiv.net/artworks/{self.id}"
        
    @property
    def all_image_urls(self) -> List[str]:
        """Get all original image URLs"""
        if self.page_count == 1:
            if self.meta_single_page.get("original_image_url"):
                return [self.meta_single_page["original_image_url"]]
            return [self.image_urls.large] # Fallback
            
        urls = []
        for page in self.meta_pages:
            if "image_urls" in page and "original" in page["image_urls"]:
                urls.append(page["image_urls"]["original"])
            elif "image_urls" in page and "large" in page["image_urls"]:
                urls.append(page["image_urls"]["large"])
        return urls

    @property
    def all_large_image_urls(self) -> List[str]:
        """Get all large image URLs (better for sending to QQ)"""
        if self.page_count == 1:
            return [self.image_urls.large]
            
        urls = []
        for page in self.meta_pages:
            if "image_urls" in page and "large" in page["image_urls"]:
                urls.append(page["image_urls"]["large"])
            elif "image_urls" in page and "medium" in page["image_urls"]:
                urls.append(page["image_urls"]["medium"])
            elif "image_urls" in page and "original" in page["image_urls"]:
                urls.append(page["image_urls"]["original"])
        return urls

class UserImageUrls(BaseModel):
    medium: str

class User(BaseModel):
    id: int
    name: str
    account: str
    profile_image_urls: UserImageUrls

Illust.update_forward_refs()
