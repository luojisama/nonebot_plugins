import httpx
import xml.etree.ElementTree as ET
from typing import List, Type, Optional
from nonebot.log import logger
from .models import Resource

def format_size(size_bytes: Optional[str]) -> str:
    if not size_bytes:
        return ""
    try:
        s = int(size_bytes)
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if s < 1024:
                return f"{s:.2f} {unit}"
            s /= 1024
        return f"{s:.2f} PB"
    except ValueError:
        return size_bytes

class BaseSource:
    name: str = "base"
    
    async def search(self, keyword: str) -> List[Resource]:
        raise NotImplementedError

class MikanSource(BaseSource):
    name: str = "Mikan"
    
    async def search(self, keyword: str) -> List[Resource]:
        url = "https://mikanani.me/RSS/Search"
        params = {"searchstr": keyword}
        logger.info(f"Searching Mikan for: {keyword}")
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=20.0)
            resp.raise_for_status()
            
            # Remove namespace prefixes to make parsing easier (hacky but effective for simple XML)
            xml_content = resp.text.replace('xmlns:torrent="http://xmlns.ezrss.it/0.1/"', '')
            # Or just ignore namespaces in find/findall or use namespace dict
            
            root = ET.fromstring(resp.content)
            
            # Mikan uses namespace for magnetURI. 
            # If we parse raw content, we need to handle it.
            # Let's use the namespace map.
            ns = {'torrent': 'http://xmlns.ezrss.it/0.1/'}
            
            resources = []
            for item in root.findall("./channel/item"):
                title = item.find("title").text if item.find("title") is not None else "Unknown"
                link = item.find("link").text if item.find("link") is not None else ""
                date = item.find("pubDate").text if item.find("pubDate") is not None else ""
                
                # Try to get magnet
                magnet_node = item.find("torrent:magnetURI", ns)
                magnet = magnet_node.text if magnet_node is not None else None
                
                # Try to get size
                enclosure = item.find("enclosure")
                size = ""
                if enclosure is not None:
                    size = format_size(enclosure.get("length"))
                
                # Sometimes magnet is not in torrent:magnetURI for some RSS feeds, 
                # but Mikan usually has it.
                
                resources.append(Resource(
                    title=title,
                    link=link,
                    magnet=magnet,
                    size=size,
                    date=date,
                    source=self.name
                ))
            return resources

class AcgRipSource(BaseSource):
    name: str = "ACG.RIP"
    
    async def search(self, keyword: str) -> List[Resource]:
        url = "https://acg.rip/.xml"
        params = {"term": keyword}
        logger.info(f"Searching ACG.RIP for: {keyword}")
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=20.0)
            resp.raise_for_status()
            
            root = ET.fromstring(resp.content)
            resources = []
            for item in root.findall("./channel/item"):
                title = item.find("title").text if item.find("title") is not None else "Unknown"
                link = item.find("link").text if item.find("link") is not None else ""
                date = item.find("pubDate").text if item.find("pubDate") is not None else ""
                
                # Acg.rip RSS description usually contains details, but not structured magnet/size
                # We can just return title and link
                
                resources.append(Resource(
                    title=title,
                    link=link,
                    date=date,
                    source=self.name
                ))
            return resources

class DmhySource(BaseSource):
    name: str = "DMHY"
    
    async def search(self, keyword: str) -> List[Resource]:
        # Use dongmanhuayuan.com mirror as seen in nonebot_plugin_animeres
        base_url = "https://www.dongmanhuayuan.com"
        url = f"{base_url}/search/{keyword}/"
        
        logger.info(f"Searching DMHY via {base_url} for: {keyword}")
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        try:
            from lxml import etree
        except ImportError:
            logger.error("lxml is not installed, cannot use DMHY source")
            raise

        async with httpx.AsyncClient(headers=headers, timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            
            html = etree.HTML(resp.text, None)
            resources = []
            
            # Using xpath from nonebot_plugin_animeres implementation
            titles = html.xpath("//a[@class='uk-text-break']/@title")
            sizes = html.xpath("//b/text()")
            links = html.xpath("//span[contains(@class, 'down_txt')]/a/@href")
            
            # Since fetching magnet for ALL results is slow, we will only fetch for the top 5
            # But first let's just collect basic info
            
            for i, (title, size, link) in enumerate(zip(titles, sizes, links)):
                full_link = link if link.startswith("http") else f"{base_url}{link}"
                
                # Fetch magnet for top 5 results to ensure text fallback works nicely
                magnet = None
                if i < 5:
                    try:
                        detail_resp = await client.get(full_link)
                        detail_html = etree.HTML(detail_resp.text, None)
                        # XPath for magnet link on detail page
                        magnets = detail_html.xpath("//input[@id='magnet_one']/@value")
                        if magnets:
                            magnet = magnets[0]
                    except Exception as e:
                        logger.warning(f"Failed to fetch magnet for {title}: {e}")
                
                resources.append(Resource(
                    title=title,
                    link=full_link,
                    magnet=magnet,
                    size=size,
                    source=self.name
                ))
                
            return resources

SOURCES: List[Type[BaseSource]] = [DmhySource, MikanSource, AcgRipSource]

async def search_all(keyword: str) -> List[Resource]:
    for source_cls in SOURCES:
        source = source_cls()
        try:
            results = await source.search(keyword)
            if results:
                logger.success(f"Found {len(results)} results from {source.name}")
                return results
        except Exception as e:
            logger.error(f"Error fetching from {source.name}: {e}")
            continue
    return []
