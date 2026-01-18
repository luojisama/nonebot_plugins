from pydantic import BaseModel
from pathlib import Path

class Config(BaseModel):
    guess_song_data_path: Path = Path(__file__).parent / "data" / "songs.json"
    guess_song_session_path: Path = Path(__file__).parent / "data" / "session.json"
    guess_song_cache_dir: Path = Path(__file__).parent / "cache"
    guess_song_timeout: int = 60
    guess_song_cookie: str = ""  # 备用 MUSIC_U Cookie
