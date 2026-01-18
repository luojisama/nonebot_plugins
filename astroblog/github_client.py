import base64
import httpx
from typing import Optional, List, Dict, Any
from .config import Config

class GitHubClient:
    def __init__(self, config: Config):
        self.token = config.github_token
        self.repo = config.github_repo
        self.branch = config.github_branch
        self.base_url = f"https://api.github.com/repos/{self.repo}/contents"
        self.headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json"
        }

    async def get_file(self, path: str) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/{path}"
        params = {"ref": self.branch}
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self.headers, params=params)
            if resp.status_code == 200:
                return resp.json()
            return None

    async def list_files(self, path: str) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/{path}"
        params = {"ref": self.branch}
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self.headers, params=params)
            if resp.status_code == 200:
                return resp.json()
            return []

    async def list_files_recursive(self, path: str) -> List[Dict[str, Any]]:
        # 1. 获取分支的最后一次提交的 tree sha
        url = f"https://api.github.com/repos/{self.repo}/branches/{self.branch}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code != 200:
                return []
            tree_sha = resp.json()["commit"]["commit"]["tree"]["sha"]

        # 2. 递归获取整个树
        url = f"https://api.github.com/repos/{self.repo}/git/trees/{tree_sha}?recursive=1"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code != 200:
                return []
            
            tree = resp.json().get("tree", [])
            # 过滤出在指定路径下且是文件的项
            files = []
            for item in tree:
                if item["path"].startswith(path) and item["type"] == "blob":
                    # 模拟 contents API 的返回格式
                    files.append({
                        "name": item["path"].replace(f"{path}/", ""),
                        "path": item["path"],
                        "type": "file"
                    })
            return files

    async def upload_file(self, path: str, content: bytes, message: str, sha: Optional[str] = None) -> bool:
        url = f"{self.base_url}/{path}"
        data = {
            "message": message,
            "content": base64.b64encode(content).decode("utf-8"),
            "branch": self.branch
        }
        if sha:
            data["sha"] = sha

        async with httpx.AsyncClient() as client:
            resp = await client.put(url, headers=self.headers, json=data)
            return resp.status_code in (200, 201)

    async def delete_file(self, path: str, message: str, sha: str) -> bool:
        url = f"{self.base_url}/{path}"
        data = {
            "message": message,
            "sha": sha,
            "branch": self.branch
        }
        async with httpx.AsyncClient() as client:
            # httpx.delete doesn't support json body by default in some versions or requires explicit content
            # But GitHub API DELETE /contents/{path} requires sha in the body
            resp = await client.request("DELETE", url, headers=self.headers, json=data)
            return resp.status_code == 200

    def get_raw_url(self, path: str) -> str:
        """获取文件的 GitHub Raw URL"""
        return f"https://raw.githubusercontent.com/{self.repo}/{self.branch}/{path}"
