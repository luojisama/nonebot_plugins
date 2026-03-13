import httpx
import asyncio
from typing import List, Dict, Optional

BASE_URL = "https://schoolido.lu/api/cards/"

async def get_cards(rarity: str, count: int = 1, group: Optional[str] = None) -> List[Dict]:
    """
    Fetch random cards of a specific rarity.
    """
    params = {
        "rarity": rarity,
        "ordering": "random",
        "page_size": count,
        "expand_idol": "true" # Ensure idol details are included
    }
    
    if group:
        params["idol_main_unit"] = group
        
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        try:
            resp = await client.get(BASE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            
            cards = []
            for item in results:
                # Process image URL
                image_url = item.get("card_image") or item.get("card_idolized_image")
                if image_url and image_url.startswith("//"):
                    image_url = "https:" + image_url
                
                cards.append({
                    "id": item.get("id"),
                    "name": item.get("idol", {}).get("name", "Unknown"),
                    "rarity": item.get("rarity"),
                    "image": image_url,
                    "attribute": item.get("attribute")
                })
            return cards
        except Exception as e:
            print(f"Error fetching cards: {e}")
            return []
