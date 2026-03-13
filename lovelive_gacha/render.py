from nonebot_plugin_htmlrender import html_to_pic
from typing import List, Dict
import os

async def render_gacha_result(cards: List[Dict]) -> bytes:
    """
    Render gacha result to image.
    """
    # Since single pull is handled directly now, this is mainly for 10-pull
    # But we keep logic generic just in case
    is_ten_pull = len(cards) > 1
    
    html = """
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Ma+Shan+Zheng&family=Noto+Sans+SC:wght@400;700&display=swap');

            body {
                margin: 0;
                padding: 10px;
                background-color: #fff0f5; /* LavenderBlush - 浅粉色背景 */
                font-family: 'Ma Shan Zheng', 'Noto Sans SC', sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                width: fit-content;
                height: fit-content;
            }

            .container {
                background: #ffffff;
                border: 3px solid #ffb6c1; /* Light Pink Border */
                border-radius: 20px;
                padding: 15px;
                box-shadow: 0 10px 20px rgba(255, 182, 193, 0.3);
                width: 800px; /* Adjusted for 10-pull grid */
                position: relative;
                overflow: hidden;
            }

            /* Decorations */
            .container::before {
                content: '🌸';
                position: absolute;
                top: -5px;
                right: -5px;
                font-size: 40px;
                opacity: 0.3;
                z-index: 0;
            }

            .container::after {
                content: '✨';
                position: absolute;
                bottom: -5px;
                left: -5px;
                font-size: 40px;
                opacity: 0.3;
                z-index: 0;
            }

            .header {
                text-align: center;
                margin-bottom: 15px;
                position: relative;
                z-index: 1;
            }

            .title {
                color: #ff69b4; /* Hot Pink */
                font-size: 32px;
                font-weight: bold;
                margin: 0;
                text-shadow: 1px 1px 2px rgba(0, 0, 0, 0.1);
                /* 保持 Ma Shan Zheng 以增加辨识度，用户可能只是觉得之前排版太乱 */
                /* 如果用户明确说字体太花哨，我们可以改用标准字体 */
                font-family: 'Ma Shan Zheng', cursive; 
            }

            /* Grid Layout for Cards */
            .card-grid {
                display: grid;
                grid-template-columns: repeat(5, 1fr);
                gap: 8px;
                position: relative;
                z-index: 1;
            }

            /* Individual Card Style */
            .gacha-card {
                position: relative;
                border-radius: 6px;
                overflow: hidden;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                background: #fff;
                aspect-ratio: 2 / 3;
                transition: transform 0.2s;
            }

            .gacha-card img {
                display: block;
                width: 100%;
                height: 100%;
                object-fit: cover;
            }
            
            .card-info {
                position: absolute;
                bottom: 0;
                left: 0;
                right: 0;
                background: rgba(255, 255, 255, 0.92);
                color: #d147a3;
                padding: 3px;
                text-align: center;
                font-size: 11px;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
                font-weight: bold;
                border-top: 1px solid #ffb6c1;
                font-family: 'Noto Sans SC', sans-serif; /* 卡片信息使用清晰字体 */
            }

            /* Rarity Borders */
            .rarity-UR { border: 2px solid #ff00de; }
            .rarity-SSR { border: 2px solid #ffaa00; }
            .rarity-SR { border: 2px solid #00aaff; }
            .rarity-R { border: 2px solid #ffb6c1; } /* Pinkish for R to match theme */
            .rarity-N { border: 2px solid #cd7f32; }

        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1 class="title">LoveLive! 招募结果</h1>
            </div>
            <div class="card-grid">
                {% for card in cards %}
                <div class="gacha-card rarity-{{ card.rarity }}">
                    <img src="{{ card.image }}" alt="{{ card.name }}">
                    <div class="card-info">
                        <span style="color: {{ '#ff00de' if card.rarity == 'UR' else ('#ffaa00' if card.rarity == 'SSR' else '#d147a3') }}">{{ card.rarity }}</span>
                        {{ card.name }}
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
    </body>
    </html>
    """
    
    from jinja2 import Template
    template = Template(html)
    rendered_html = template.render(cards=cards)
    
    # Adjust viewport width to fit the container + padding
    # Container is 800px width + 15px padding * 2 = 830px.
    # Body padding 10px * 2 = 20px. Total ~850px.
    return await html_to_pic(html=rendered_html, viewport={"width": 860, "height": 100}) # Height is auto/ignored usually if content is smaller but safer to set small to allow expand
