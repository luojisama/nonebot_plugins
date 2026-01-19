import json
import random
import httpx
import re
import asyncio
import time
from pathlib import Path
from typing import List, Dict, Optional, Any

from nonebot import on_command, get_plugin_config, require
from nonebot.adapters.onebot.v11 import Message, MessageSegment, GroupMessageEvent, MessageEvent, Bot
from nonebot.exception import ActionFailed
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.typing import T_State
from nonebot.matcher import Matcher
from nonebot.log import logger

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="猜歌游戏",
    description="从网易云获取歌词进行猜歌",
    usage="猜歌: 开始游戏\n点歌 <歌名>: 搜索并点歌\n下载歌曲 <歌名>: 下载歌曲文件\n添加歌曲 <歌名> <歌手>: 添加歌曲到库\n导入歌曲 <网易云链接/ID>: 从网易云导入歌曲或歌单\n删除歌曲 <歌名>: 从库中删除歌曲\n歌曲列表: 查看库中所有歌曲",
    config=Config,
    type="application",
    homepage="{项目主页}",
    supported_adapters={"~onebot.v11"},
)

config = get_plugin_config(Config)
DATA_PATH = config.guess_song_data_path
CACHE_DIR = config.guess_song_cache_dir

def clean_cache(all_files: bool = False):
    """清理缓存文件
    :param all_files: 是否清理所有文件，否则仅清理超过 1 小时的文件
    """
    try:
        if not CACHE_DIR.exists():
            return
        now = time.time()
        count = 0
        for f in CACHE_DIR.glob("*.mp3"):
            if all_files or (now - f.stat().st_mtime > 3600):
                f.unlink()
                count += 1
        if count > 0:
            logger.info(f"猜歌插件：已清理 {count} 个缓存音频文件")
    except Exception as e:
        logger.error(f"清理缓存出错: {e}")

# 每日凌晨 3 点自动清理所有缓存
@scheduler.scheduled_job("cron", hour=3, minute=0, id="guess_song_daily_clean")
async def _():
    clean_cache(all_files=True)

def get_headers():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://music.163.com/",
    }
    return headers

def load_songs() -> List[Dict[str, str]]:
    if not DATA_PATH.exists():
        DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        save_songs([])
        return []
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_songs(songs: List[Dict[str, str]]):
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(songs, f, ensure_ascii=False, indent=2)

async def ncm_search(keyword: str, limit: int = 5) -> List[Dict[str, Any]]:
    """使用网易云标准接口搜索歌曲"""
    url = f"https://music.163.com/api/search/get/web?s={keyword}&type=1&offset=0&total=true&limit={limit}"
    async with httpx.AsyncClient(timeout=10, headers=get_headers()) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("result") and data["result"].get("songs"):
                    results = []
                    for song in data["result"]["songs"]:
                        results.append({
                            "id": song["id"],
                            "title": song["name"],
                            "artist": song["artists"][0]["name"] if song.get("artists") else "未知歌手",
                            "album": song["album"]["name"] if song.get("album") else ""
                        })
                    return results
        except Exception as e:
            logger.error(f"网易云搜索失败: {e}")
    return []

async def ncm_get_lyrics(song_id: int, full: bool = False) -> Optional[str]:
    # url = f"https://api.viki.moe/ncm/song/{song_id}/lyric"
    url = f"https://music.163.com/api/song/lyric?os=pc&id={song_id}&lv=-1"
    async with httpx.AsyncClient(timeout=10, headers=get_headers()) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                lrc = data.get("lrc", {}).get("lyric", "")
                if lrc:
                    # 过滤时间标签
                    lines = re.sub(r"\[.*?\]", "", lrc).split("\n")
                    lines = [line.strip() for line in lines if line.strip()]
                    
                    if full:
                        return "\n".join(lines)
                        
                    # 过滤掉一些无意义的行
                    filters = ["作词", "作曲", "编曲", "制作", "Producer", "Arrangement", "Lyricist", "Composer", "混音", "吉他", "鼓", "钢琴", "后期"]
                    lines = [line for line in lines if not any(x.lower() in line.lower() for x in filters)]
                    if len(lines) > 5:
                        # 随机选一段，但尽量避开最后几行（通常是重复的副歌或鸣谢）
                        max_start = max(0, len(lines) - 4)
                        start = random.randint(0, min(max_start, len(lines) // 2))
                        return "\n".join(lines[start:start+3])
                    return "\n".join(lines)
        except Exception as e:
            logger.error(f"获取歌词失败 (ID: {song_id}): {e}")
    return None

async def ncm_get_audio(song_id: int, br: int = 320000) -> Optional[tuple[Path, str]]:
    """获取歌曲音频并保存为本地 mp3 文件
    :param song_id: 歌曲 ID
    :param br: 期望音质 (bitrate)，默认 320000 (HQ)
    """
    if not CACHE_DIR.exists():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # 优先检查请求的音质缓存
    local_path = CACHE_DIR / f"{song_id}_{br}.mp3"
    if local_path.exists() and local_path.stat().st_size > 0:
        return local_path, ""

    # 如果请求的是 HQ 但没缓存，或者缓存失效，尝试获取
    bitrates = [br]
    if br > 128000:
        bitrates.append(128000) # 备选音质

    async with httpx.AsyncClient(timeout=15, headers=get_headers(), follow_redirects=True) as client:
        for current_br in bitrates:
            url = f"https://api.viki.moe/ncm/song/{song_id}/url?br={current_br}"
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.warning(f"音频接口返回异常 (ID: {song_id}, br: {current_br}): {resp.status_code}")
                    continue
                
                data = resp.json()
                audio_url = data.get("url")
                
                if not audio_url:
                    logger.warning(f"音频接口未返回 URL (ID: {song_id}, br: {current_br}), 响应: {data}")
                    continue
                
                # 再次检查该音质的本地缓存（防止循环中其他音质已缓存）
                current_local_path = CACHE_DIR / f"{song_id}_{current_br}.mp3"
                if current_local_path.exists() and current_local_path.stat().st_size > 0:
                    return current_local_path, audio_url

                # 下载音频
                audio_resp = await client.get(audio_url)
                if audio_resp.status_code == 200:
                    current_local_path.write_bytes(audio_resp.content)
                    return current_local_path, audio_url
                else:
                    logger.error(f"下载音频文件失败 (ID: {song_id}, URL: {audio_url}): {audio_resp.status_code}")
                    
            except Exception as e:
                logger.error(f"获取音频尝试失败 (ID: {song_id}, br: {current_br}): {e}")
                
    return None

async def ncm_get_song_info(song_id: int) -> Optional[Dict[str, Any]]:
    url = f"https://api.viki.moe/ncm/song/{song_id}"
    async with httpx.AsyncClient(timeout=10, headers=get_headers()) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if "name" in data:
                    return {
                        "title": data["name"],
                        "artist": data["artists"][0]["name"] if data.get("artists") else "未知歌手",
                        "id": song_id
                    }
        except Exception as e:
            logger.error(f"获取歌曲详情失败 (ID: {song_id}): {e}")
    return None

async def ncm_get_playlist_songs(playlist_id: int) -> List[Dict[str, Any]]:
    detail_url = f"https://music.163.com/api/v1/playlist/detail?id={playlist_id}"
    songs_list = []
    async with httpx.AsyncClient(timeout=10, headers=get_headers()) as client:
        try:
            resp = await client.get(detail_url)
            if resp.status_code == 200:
                data = resp.json()
                playlist = data.get("playlist", {})
                
                # 获取所有歌曲 ID
                track_ids = [t["id"] for t in playlist.get("trackIds", [])]
                if not track_ids and playlist.get("tracks"):
                    # 备选方案：如果 trackIds 为空，尝试直接用 tracks
                    track_ids = [t["id"] for t in playlist["tracks"]]
                
                if not track_ids:
                    return []
                
                # 限制获取数量，防止请求过大
                track_ids = track_ids[:300]
                
                # 分批获取歌曲详情 (每批 50 个 ID)
                for i in range(0, len(track_ids), 50):
                    batch_ids = track_ids[i:i+50]
                    ids_str = ",".join(map(str, batch_ids))
                    ids_param = "[" + ",".join(map(str, batch_ids)) + "]"
                    song_url = f"https://music.163.com/api/song/detail?ids={ids_param}"
                    
                    song_resp = await client.get(song_url)
                    if song_resp.status_code == 200:
                        song_data = song_resp.json()
                        for track in song_data.get("songs", []):
                            songs_list.append({
                                "title": track["name"],
                                "artist": track["artists"][0]["name"],
                                "id": track["id"]
                            })
        except Exception as e:
            print(f"导入歌单出错: {e}")
    return songs_list

# 命令注册
guess_song = on_command("猜歌", priority=5, block=True)
add_song = on_command("添加歌曲", priority=5, block=True)
del_song = on_command("删除歌曲", priority=5, block=True)
import_song = on_command("导入歌曲", priority=5, block=True)
list_songs = on_command("歌曲列表", priority=5, block=True)
query_lyrics = on_command("查询歌词", aliases={"查歌词", "歌词"}, priority=5, block=True)
order_song = on_command("点歌", priority=5, block=True)
download_song = on_command("下载歌曲", aliases={"下载"}, priority=5, block=True)
guess_help = on_command("猜歌帮助", aliases={"猜歌指令", "猜歌菜单"}, priority=5, block=True)

@guess_help.handle()
async def _():
    help_msg = (
        "🎵 【猜歌游戏】 指令帮助 🎵\n"
        "━━━━━━━━━━━━━━━\n"
        "🎮 游戏指令：\n"
        "• 猜歌 - 开始猜歌游戏 (随机歌词/语音)\n"
        "• 猜歌 歌词 - 强制歌词模式\n"
        "• 猜歌 语音 - 强制语音模式\n\n"
        "🔍 查询/点歌指令：\n"
        "• 点歌 <歌名> - 搜索并点播歌曲语音\n"
        "• 下载歌曲 <歌名> - 获取歌曲下载链接\n"
        "• 歌曲列表 [范围] - 查看曲库 (例: 1-100, all)\n"
        "• 查询歌词 <歌名/ID> - 查询指定歌曲的歌词\n\n"
        "📥 歌曲管理：\n"
        "• 添加歌曲 <歌名> <歌手> - 手动录入歌曲\n"
        "• 导入歌曲 <链接/ID> - 从网易云导入歌曲/歌单\n"
        "• 删除歌曲 <歌名> - 从库中移除歌曲\n\n"
        "📖 游戏说明：\n"
        "系统会随机给出一段歌词或语音，你可以回复【选项序号】或【歌名关键字】来回答。回答错误或格式不对将结束本轮游戏。"
    )
    await guess_help.finish(help_msg)

async def handle_import(matcher: Matcher, text: str):
    song_id = None
    playlist_id = None
    
    # 解析 ID
    if text.isdigit():
        song_id = int(text)
    else:
        # 解析链接
        song_match = re.search(r"song\?id=(\d+)", text)
        playlist_match = re.search(r"playlist\?id=(\d+)", text)
        if song_match:
            song_id = int(song_match.group(1))
        elif playlist_match:
            playlist_id = int(playlist_match.group(1))
        else:
            await matcher.finish("无法解析链接，请确保是网易云音乐的歌曲或歌单链接")

    songs = load_songs()
    added_count = 0
    
    if song_id:
        info = await ncm_get_song_info(song_id)
        if not info:
            await matcher.finish(f"未找到 ID 为 {song_id} 的歌曲信息")
        
        if any(s.get("id") == song_id or s["title"] == info["title"] for s in songs):
            await matcher.finish(f"歌曲《{info['title']}》已在库中")
        
        songs.append(info)
        added_count = 1
        msg = f"成功导入歌曲：《{info['title']}》- {info['artist']} (ID: {song_id})"
    else:
        # 导入歌单
        new_songs = await ncm_get_playlist_songs(playlist_id)
        if not new_songs:
            await matcher.finish(f"未找到 ID 为 {playlist_id} 的歌单或歌单为空")
        
        # 限制单次导入上限为 200 首
        if len(new_songs) > 200:
            new_songs = new_songs[:200]
            await matcher.send("⚠️ 歌单歌曲较多，为保证稳定性，本次仅尝试导入前 200 首。")
        
        current_ids = {s.get("id") for s in songs if s.get("id")}
        current_titles = {s["title"] for s in songs}
        
        for s in new_songs:
            if s.get("id") not in current_ids and s["title"] not in current_titles:
                songs.append(s)
                added_count += 1
        msg = f"成功从歌单导入 {added_count} 首新歌曲！"

    if added_count > 0:
        save_songs(songs)
        await matcher.finish(msg)
    else:
        await matcher.finish("未发现新歌曲（可能已全部在库中）")

@import_song.handle()
async def _(matcher: Matcher, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    if not text:
        await import_song.finish("使用方法: 导入歌曲 <网易云链接/ID>")
    await handle_import(matcher, text)

@list_songs.handle()
async def _(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    songs = load_songs()
    if not songs:
        await list_songs.finish("歌曲库为空")
    
    total = len(songs)
    arg_text = args.extract_plain_text().strip()
    
    start_idx = 0
    end_idx = total
    
    if not arg_text:
        # 默认显示最后 100 首
        start_idx = max(0, total - 100)
        display_range_msg = f"最近 100 首歌曲 (总计 {total})"
    elif arg_text.lower() == "all":
        start_idx = 0
        end_idx = total
        display_range_msg = f"全部 {total} 首歌曲"
    elif "-" in arg_text:
        try:
            parts = arg_text.split("-")
            start_idx = max(1, int(parts[0])) - 1
            end_idx = min(total, int(parts[1]))
            if start_idx >= end_idx:
                await list_songs.finish("范围无效，起始位置必须小于结束位置")
            display_range_msg = f"第 {start_idx + 1} 到 {end_idx} 首歌曲 (总计 {total})"
        except ValueError:
            await list_songs.finish("范围格式错误，请使用 '歌曲列表 1-100' 或 '歌曲列表 100'")
    elif arg_text.isdigit():
        count = int(arg_text)
        start_idx = max(0, total - count)
        display_range_msg = f"最近 {min(count, total)} 首歌曲 (总计 {total})"
    else:
        await list_songs.finish("参数错误。用法：'歌曲列表' (显示最后100首), '歌曲列表 50' (最后50首), '歌曲列表 1-100' (指定范围), '歌曲列表 all' (全部)")

    display_songs = songs[start_idx:end_idx]
    
    # 分段发送，每 100 首一条聊天记录
    chunk_size = 100
    for i in range(0, len(display_songs), chunk_size):
        chunk = display_songs[i:i + chunk_size]
        messages = []
        
        # 添加标题节点
        header_content = f"【猜歌曲库】{display_range_msg}"
        if len(display_songs) > chunk_size:
            header_content += f"\n(分段 {i // chunk_size + 1}: 第 {start_idx + i + 1} - {start_idx + min(i + chunk_size, len(display_songs))} 首)"
            
        messages.append({
            "type": "node",
            "data": {
                "name": "猜歌曲库",
                "uin": bot.self_id,
                "content": header_content
            }
        })
        
        # 逐条添加歌曲信息
        for idx, s in enumerate(chunk):
            real_idx = start_idx + i + idx + 1
            messages.append({
                "type": "node",
                "data": {
                    "name": "猜歌曲库",
                    "uin": bot.self_id,
                    "content": f"{real_idx}. 🎵 {s['title']} - {s['artist']} {'🔗' if s.get('id') else ''}"
                }
            })
        
        try:
            if isinstance(event, GroupMessageEvent):
                await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=messages)
            else:
                await bot.call_api("send_private_forward_msg", user_id=event.user_id, messages=messages)
        except Exception as e:
            if i == 0:
                # 如果第一段就失败了，回退到简洁文本
                msg = f"转发列表失败: {e}\n最近 10 首歌曲：\n"
                msg += "\n".join([f"- {s['title']} ({s['artist']})" for s in songs[-10:]])
                await list_songs.finish(msg)
            else:
                await list_songs.send(f"发送分段 {i // chunk_size + 1} 失败: {e}")

@query_lyrics.handle()
async def _(bot: Bot, matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    keyword = args.extract_plain_text().strip()
    if not keyword:
        await query_lyrics.finish("使用方法: 查询歌词 <歌名/歌手/ID>")
    
    song_id = None
    song_title = "未知歌曲"
    
    # 1. 尝试解析 ID
    if keyword.isdigit():
        song_id = int(keyword)
    else:
        # 2. 尝试从本地库匹配
        songs = load_songs()
        for s in songs:
            if keyword.lower() in s["title"].lower() or keyword.lower() in s["artist"].lower():
                song_id = s.get("id")
                song_title = f"{s['title']} - {s['artist']}"
                break
        
        # 3. 如果没匹配到或本地没 ID，去网易云搜
        if not song_id:
            await matcher.send(f"正在网易云搜索歌曲: {keyword}...")
            search_results = await ncm_search(keyword, limit=1)
            song_id = search_results[0]["id"] if search_results else None
    
    if not song_id:
        await query_lyrics.finish(f"未找到与 '{keyword}' 相关的歌曲 ID，无法查询歌词")

    # 获取歌曲详情（如果还不知道歌名）
    if song_title == "未知歌曲":
        info = await ncm_get_song_info(song_id)
        if info:
            song_title = f"{info['title']} - {info['artist']}"

    # 获取完整歌词
    lyrics = await ncm_get_lyrics(song_id, full=True)
    if not lyrics:
        await query_lyrics.finish(f"歌曲《{song_title}》暂时没有歌词")

    # 构造转发消息
    messages = [
        {
            "type": "node",
            "data": {
                "name": "歌词查询",
                "uin": bot.self_id,
                "content": f"📖 歌曲《{song_title}》的完整歌词如下："
            }
        },
        {
            "type": "node",
            "data": {
                "name": "歌词查询",
                "uin": bot.self_id,
                "content": lyrics
            }
        }
    ]

    try:
        if isinstance(event, GroupMessageEvent):
            await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=messages)
        else:
            await bot.call_api("send_private_forward_msg", user_id=event.user_id, messages=messages)
    except Exception as e:
        await query_lyrics.finish(f"歌词发送失败: {e}")

@order_song.handle()
async def _(bot: Bot, event: MessageEvent, matcher: Matcher, state: T_State, args: Message = CommandArg()):
    keyword = args.extract_plain_text().strip()
    if not keyword:
        await order_song.finish("使用方法: 点歌 <歌名/歌手>")
    
    results = await ncm_search(keyword, limit=5)
    if not results:
        await order_song.finish(f"未找到与 '{keyword}' 相关的歌曲")
    
    if len(results) == 1:
        state["selected_song"] = results[0]
    else:
        state["results"] = results
        msg = "找到以下歌曲，请回复序号进行点播（回复其他内容取消）：\n"
        for i, song in enumerate(results, 1):
            msg += f"{i}. {song['title']} - {song['artist']}"
            if song.get("album"):
                msg += f" ({song['album']})"
            msg += "\n"
        await order_song.pause(msg.strip())

@order_song.handle()
async def _(bot: Bot, event: MessageEvent, state: T_State):
    if "selected_song" in state:
        return

    text = event.get_plaintext().strip()
    results = state.get("results", [])
    if text.isdigit():
        idx = int(text)
        if 1 <= idx <= len(results):
            state["selected_song"] = results[idx - 1]
        else:
            await order_song.finish("序号超出范围，已取消点歌")
    else:
        await order_song.finish("已取消点歌")

@order_song.handle()
async def _(bot: Bot, event: MessageEvent, state: T_State):
    song = state["selected_song"]
    song_id = song["id"]
    
    await order_song.send(f"正在为您获取音频: {song['title']} - {song['artist']}...")
    
    # 获取音频 (点歌使用 HQ 音质，失败会自动回退)
    result = await ncm_get_audio(song_id, br=320000)
    if not result:
        await order_song.finish("无法获取音频文件（已尝试高音质和标准音质），该歌曲可能受版权限制或仅限会员收听。")
        
    local_path, audio_url = result
    
    # 发送歌曲卡片/信息和音频
    msg = MessageSegment.text(f"🎧 为您点播：{song['title']} - {song['artist']}\n")
    msg += MessageSegment.record(local_path.absolute().as_uri())
    
    await order_song.finish(msg)

@download_song.handle()
async def _(bot: Bot, event: MessageEvent, matcher: Matcher, state: T_State, args: Message = CommandArg()):
    keyword = args.extract_plain_text().strip()
    if not keyword:
        await download_song.finish("使用方法: 下载歌曲 <歌名/歌手>")
    
    results = await ncm_search(keyword, limit=5)
    if not results:
        await download_song.finish(f"未找到与 '{keyword}' 相关的歌曲")
    
    if len(results) == 1:
        state["selected_song"] = results[0]
    else:
        state["results"] = results
        msg = "找到以下歌曲，请回复序号进行下载（回复其他内容取消）：\n"
        for i, song in enumerate(results, 1):
            msg += f"{i}. {song['title']} - {song['artist']}"
            if song.get("album"):
                msg += f" ({song['album']})"
            msg += "\n"
        await download_song.pause(msg.strip())

@download_song.handle()
async def _(bot: Bot, event: MessageEvent, state: T_State):
    if "selected_song" in state:
        return

    text = event.get_plaintext().strip()
    results = state.get("results", [])
    if text.isdigit():
        idx = int(text)
        if 1 <= idx <= len(results):
            state["selected_song"] = results[idx - 1]
        else:
            await download_song.finish("序号超出范围，已取消下载")
    else:
        await download_song.finish("已取消下载")

@download_song.handle()
async def _(bot: Bot, event: MessageEvent, state: T_State):
    song = state["selected_song"]
    song_id = song["id"]
    
    await download_song.send(f"正在为您准备下载链接: {song['title']} - {song['artist']}...")
    
    # 获取音频 (下载使用 HQ 音质，失败会自动回退)
    result = await ncm_get_audio(song_id, br=320000)
    if not result:
        await download_song.finish("无法获取音频链接（已尝试高音质和标准音质），该歌曲可能受版权限制或仅限会员收听。")
        
    local_path, audio_url = result
    
    # 发送下载信息
    msg = (
        f"✅ 歌曲《{song['title']}》- {song['artist']} 已准备就绪！\n"
        f"🔗 下载链接: {audio_url}\n"
        f"💡 提示: 链接有效期较短，请尽快下载。如果是语音，可以直接右键另存为。"
    )
    
    # 尝试发送文件
    try:
        if isinstance(event, GroupMessageEvent):
            await download_song.send(MessageSegment.record(local_path.absolute().as_uri()))
    except Exception:
        pass
        
    await download_song.finish(msg)

@add_song.handle()
async def _(matcher: Matcher, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    if not text:
        await add_song.finish("使用方法: 添加歌曲 <歌名> <歌手> 或 <网易云链接>")
    
    # 如果看起来像链接，跳转到导入逻辑
    if "music.163.com" in text or "163cn.tv" in text:
        await matcher.send("检测到网易云链接，正在尝试导入...")
        # 这里可以直接逻辑复用，或者简单提示用户使用“导入歌曲”
        # 为了体验好，我们直接在这里处理
        await handle_import(matcher, text)
        return

    # 正常添加逻辑...
    parts = text.rsplit(maxsplit=1)
    if len(parts) < 2:
        await add_song.finish("使用方法: 添加歌曲 <歌名> <歌手> 或 <网易云链接>")
    
    title, artist = parts[0].strip(), parts[1].strip()
    songs = load_songs()
    if any(s["title"] == title for s in songs):
        await add_song.finish(f"歌曲《{title}》已在库中")
    
    # 尝试搜索并保存 ID
    search_results = await ncm_search(f"{title} {artist}", limit=1)
    song_id = search_results[0]["id"] if search_results else None
    
    song_entry = {"title": title, "artist": artist}
    if song_id:
        song_entry["id"] = song_id
        
    songs.append(song_entry)
    save_songs(songs)
    
    msg = f"成功添加歌曲《{title}》- {artist}"
    if song_id:
        msg += f" (已匹配 ID: {song_id})"
    else:
        msg += " (未找到匹配 ID，游戏时将尝试实时搜索)"
    
    await add_song.finish(msg)

@del_song.handle()
async def _(args: Message = CommandArg()):
    title = args.extract_plain_text().strip()
    if not title:
        await del_song.finish("使用方法: 删除歌曲 <歌名>")
    
    songs = load_songs()
    new_songs = [s for s in songs if s["title"] != title]
    if len(songs) == len(new_songs):
        await del_song.finish(f"未找到歌曲《{title}》")
    
    save_songs(new_songs)
    await del_song.finish(f"成功删除歌曲《{title}》")

@guess_song.handle()
async def _(matcher: Matcher, state: T_State, args: Message = CommandArg()):
    mode_arg = args.extract_plain_text().strip()
    
    songs = load_songs()
    if len(songs) < 4:
        await guess_song.finish("歌曲库数量不足（至少需要4首），请先添加歌曲")
    
    # 随机选一首歌
    target_song = random.choice(songs)
    state["target"] = target_song
    
    # 优先使用 JSON 中记录性 ID
    song_id = target_song.get("id")
    if not song_id:
        # 获取网易云 ID
        keyword = f"{target_song['title']} {target_song['artist']}"
        search_results = await ncm_search(keyword, limit=1)
        song_id = search_results[0]["id"] if search_results else None
    
    if not song_id:
        await guess_song.finish(f"网易云中未搜索到歌曲《{target_song['title']}》")
    
    state["song_id"] = song_id
    
    # 选择游戏模式: 歌词或语音
    if mode_arg in ["歌词", "lyric"]:
        mode = "lyric"
    elif mode_arg in ["语音", "voice", "音频"]:
        mode = "voice"
    else:
        mode = random.choice(["lyric", "voice"])
    state["mode"] = mode
    
    msg = Message()
    if mode == "lyric":
        lyric = await ncm_get_lyrics(song_id)
        if not lyric:
            lyric = "（无法获取歌词，请尝试猜歌名）"
        msg.append(f"【猜歌名 - 歌词模式】\n歌词片段：\n{lyric}")
    else:
        # 语音模式 (猜歌游戏使用 128000 节约带宽)
        clean_cache()
        audio_info = await ncm_get_audio(song_id, br=128000)
        if audio_info:
            audio_path, audio_url = audio_info
            state["audio_path"] = str(audio_path)
            state["audio_url"] = audio_url
            msg.append(MessageSegment.record(audio_path.absolute().as_uri()))
            msg.append("\n【猜歌名 - 语音模式】")
        else:
            mode = "lyric"
            state["mode"] = mode
            lyric = await ncm_get_lyrics(song_id)
            if not lyric:
                lyric = "（无法获取音频和歌词，请直接根据选项猜歌）"
            msg.append(f"【猜歌名 - 模式回退】\n(原音频不可用，已切换至歌词模式)\n歌词片段：\n{lyric}")
    
    # 生成选项
    options = [target_song["title"]]
    other_songs = [s["title"] for s in songs if s["title"] != target_song["title"]]
    options.extend(random.sample(other_songs, min(3, len(other_songs))))
    random.shuffle(options)
    
    state["options"] = options
    option_str = "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(options)])
    
    if mode == "voice":
        try:
            await matcher.send(msg)
            await matcher.send(f"【猜歌名 - 选项提示】\n{option_str}\n\n请输入歌名或选项序号进行回答！")
        except ActionFailed:
            # 语音发送失败回退
            audio_url = state.get("audio_url")
            try:
                # 尝试使用 URL 直接发送
                await matcher.send(MessageSegment.record(audio_url) + "\n【猜歌名 - 语音模式(URL回退)】")
                await matcher.send(f"【猜歌名 - 选项提示】\n{option_str}\n\n请输入歌名或选项序号进行回答！")
            except Exception:
                # 最终回退到歌词
                lyric = await ncm_get_lyrics(song_id)
                fallback_msg = "⚠️ 语音发送失败"
                if lyric:
                    fallback_msg += f"，已切换至歌词模式：\n\n{lyric}"
                else:
                    fallback_msg += "，请直接根据选项猜歌名。"
                
                await matcher.send(fallback_msg)
                await matcher.send(f"【猜歌名 - 选项提示】\n{option_str}\n\n请输入歌名或选项序号进行回答！")
    else:
        # 歌词模式直接发送
        await matcher.send(msg)
        await matcher.send(f"【猜歌名 - 选项提示】\n{option_str}\n\n请输入歌名或选项序号进行回答！")

@guess_song.receive()
async def _(matcher: Matcher, event: MessageEvent, state: T_State):
    answer = event.get_plaintext().strip()
    target = state["target"]
    options = state["options"]
    
    # 检查序号
    is_correct = False
    if answer.isdigit():
        idx = int(answer) - 1
        if 0 <= idx < len(options):
            if options[idx] == target["title"]:
                is_correct = True
    elif answer == target["title"] or target["title"] in answer:
        is_correct = True
        
    if is_correct:
        await matcher.finish(f"恭喜你答对了！这首歌正是《{target['title']}》- {target['artist']}")
    else:
        await matcher.finish(f"很遗憾，答错了。正确答案是《{target['title']}》- {target['artist']}")
