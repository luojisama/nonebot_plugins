from pydantic import BaseModel


class Config(BaseModel):
    github_token: str
    github_repo: str  # owner/repo
    github_branch: str = "main"
    blog_path: str = "src/content/blog"
    thought_path: str = "src/content/thoughts"
    image_path: str = "public/images/blog"
