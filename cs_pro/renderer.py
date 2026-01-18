import os
from pathlib import Path
from datetime import datetime
from jinja2 import Environment, FileSystemLoader
from nonebot import require
from typing import List, Dict, Any

require("nonebot_plugin_htmlrender")
from nonebot_plugin_htmlrender import html_to_pic

TEMPLATE_PATH = Path(__file__).parent / "templates"
env = Environment(loader=FileSystemLoader(TEMPLATE_PATH))

async def render_events_card(events: List[Dict[str, Any]]) -> bytes:
    template = env.get_template("events.html")
    
    # Pre-process events for template
    processed_events = []
    for event in events:
        # Basic cleanup
        title = event.get('title', '未知赛事')
        if title == '进行中' and event.get('status'):
            title = event.get('status')
            status = '进行中'
        else:
            status = event.get('status', '未开始')
            
        processed_events.append({
            'title': title,
            'level': event.get('level', 'A级'),
            'status': status,
            'time': event.get('time', ''),
            'location': event.get('location', ''),
            'prize': event.get('prize', ''),
            'teams': event.get('teams', '')
        })

    html_content = template.render(
        events=processed_events,
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    
    # Calculate height based on number of events
    height = 200 + len(processed_events) * 110
    if height > 2000: height = 2000
    
    return await html_to_pic(
        html=html_content,
        viewport={"width": 800, "height": height}
    )

async def render_matches_card(matches: List[Dict[str, Any]]) -> bytes:
    template = env.get_template("matches.html")
    
    html_content = template.render(
        matches=matches[:15], # Limit to 15 matches to avoid too long image
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    
    # Calculate height: header(150) + groups + matches(80 each) + footer(100)
    # Estimate based on number of matches and dates
    unique_dates = len(set(m['date'] for m in matches[:15]))
    height = 150 + unique_dates * 40 + len(matches[:15]) * 85 + 100
    if height > 2500: height = 2500
    
    return await html_to_pic(
        html=html_content,
        viewport={"width": 800, "height": height}
    )

async def render_stats_card(data: dict) -> bytes:
    """
    Render player stats data to an image.
    """
    template = env.get_template("stats.html")
    
    html_content = template.render(
        nickname=data.get("nickname", "Unknown"),
        avatar=data.get("avatar", ""),
        stats=data.get("stats", {}),
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    
    # Calculate height based on whether recent matches are present
    base_height = 650
    if data.get("stats", {}).get("recent_matches"):
        base_height += len(data["stats"]["recent_matches"]) * 85 + 50
    
    if base_height > 1500: base_height = 1500
    
    return await html_to_pic(
        html=html_content,
        viewport={"width": 640, "height": base_height}
    )

async def render_player_detail(player_data: dict) -> bytes:
    """
    Render detailed CS professional player info to an image.
    """
    template = env.get_template("player_detail.html")
    
    # Pre-calculate HLTV link
    hltv_id = player_data.get("hltv_id")
    name = player_data.get("name", "player")
    hltv_link = f"https://www.hltv.org/player/{hltv_id}/{name}" if hltv_id else None
    
    html_content = template.render(
        player=player_data,
        hltv_link=hltv_link,
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    
    # Player detail height can vary based on honors, but 1200 is a good baseline
    return await html_to_pic(
        html=html_content,
        viewport={"width": 800, "height": 1300}
    )

async def render_pw_stats_card(player_data: dict) -> bytes:
    """
    Render Perfect World player stats to an image.
    """
    template = env.get_template("pw_stats.html")
    
    html_content = template.render(
        player=player_data,
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    
    # Calculate height based on content
    # Base height for fixed sections: ~1050px
    # Recent matches (up to 5): ~85px each
    base_height = 1050
    if player_data.get("recent_matches"):
        base_height += len(player_data["recent_matches"]) * 85 + 50
    
    if base_height > 1800: base_height = 1800
    
    return await html_to_pic(
        html=html_content,
        viewport={"width": 640, "height": base_height}
    )
