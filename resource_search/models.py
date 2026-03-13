from dataclasses import dataclass
from typing import Optional

@dataclass
class Resource:
    title: str
    link: str
    magnet: Optional[str] = None
    size: str = ""
    date: str = ""
    source: str = ""
    
    def __str__(self) -> str:
        msg = f"[{self.source}] {self.title}"
        if self.size:
            msg += f"\n大小: {self.size}"
        if self.date:
            msg += f"\n日期: {self.date}"
        msg += f"\n链接: {self.link}"
        return msg
