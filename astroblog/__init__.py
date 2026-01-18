import base64
import uuid
import mimetypes
import re
from datetime import datetime
from typing import List, Optional

import httpx
from nonebot import get_plugin_config, on_command, require, get_driver
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.matcher import Matcher
from nonebot.params import CommandArg, Arg, ArgPlainText
from nonebot.plugin import PluginMetadata

require("nonebot_plugin_htmlrender")
from nonebot_plugin_htmlrender import md_to_pic

from .config import Config
from .github_client import GitHubClient

__plugin_meta__ = PluginMetadata(
    name="astro-blog",
    description="通过 GitHub 管理 Astro 博客，支持文章管理、小事记录及图片自动 Base64 渲染",
    usage="""
【文章管理】   
- blog list : 列出所有文章    
- blog view <slug> : 查看文章渲染图    
- blog new <标题> : 创建新文章(交互式)    
- blog update <slug> : 更新文章内容(交互式)    
- blog del <slug> : 删除指定文章    

【小事记录】   
- blog thought <内容> : 记录一件小事(支持图片)    
- blog thoughts : 查看最近10条小事列表    
- blog view_thought <文件名> : 查看小事详情图    

【其他】    
- blog help : 显示此帮助信息    
    """.strip(),
    type="application",
    homepage="https://github.com/user/nonebot-plugin-astro-blog",
    config=Config,
    supported_adapters={"~onebot.v11"},
)

config = get_plugin_config(Config)
gh = GitHubClient(config)

# 获取超级用户配置
superusers = get_driver().config.superusers

blog = on_command("blog", priority=5, block=True)

async def download_image(url: str) -> tuple[bytes, str]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "image/png")
        ext = mimetypes.guess_extension(content_type) or ".png"
        return resp.content, ext

async def handle_images(message: Message) -> tuple[str, List[str]]:
    """处理消息中的图片，上传到 GitHub 并返回替换后的文本和图片路径列表"""
    content = ""
    image_paths = []
    
    for seg in message:
        if seg.type == "text":
            content += str(seg)
        elif seg.type == "image":
            url = seg.data.get("url")
            if url:
                try:
                    image_data, ext = await download_image(url)
                    filename = f"{uuid.uuid4()}{ext}"
                    path = f"{config.image_path}/{filename}"
                    success = await gh.upload_file(path, image_data, f"Upload image {filename}")
                    if success:
                        # Astro public path usually starts from /
                        image_url = f"/{config.image_path.replace('public/', '')}/{filename}"
                        content += f"\n![image]({image_url})\n"
                        image_paths.append(path)
                    else:
                        content += "\n[图片上传失败]\n"
                except Exception as e:
                    content += f"\n[图片处理失败: {e}]\n"
    return content, image_paths

async def fix_image_urls(content: str) -> str:
    """将 Markdown 中的相对图片路径转换为 Base64，以便 htmlrender 渲染"""
    async def replace_url(match):
        alt = match.group(1)
        url = match.group(2)
        
        # 识别 Astro 的相对路径或绝对路径
        if url.startswith("/"):
            # 假设 /images/xxx 对应 public/images/xxx
            rel_path = url.lstrip("/")
            full_path = f"public/{rel_path}"
            
            # 从 GitHub 获取原始文件
            file_info = await gh.get_file(full_path)
            if file_info and "content" in file_info:
                # GitHub content is already base64, but we need the data URI format
                mime_type, _ = mimetypes.guess_type(full_path)
                mime_type = mime_type or "image/png"
                # Remove newlines from GitHub's base64
                b64_data = file_info["content"].replace("\n", "").replace("\r", "")
                return f"![{alt}](data:{mime_type};base64,{b64_data})"
        
        return match.group(0)
    
    # 因为需要 await，所以我们手动处理正则替换
    pattern = r"!\[(.*?)\]\((.*?)\)"
    matches = list(re.finditer(pattern, content))
    
    offset = 0
    new_content = content
    for match in matches:
        replacement = await replace_url(match)
        start = match.start() + offset
        end = match.end() + offset
        new_content = new_content[:start] + replacement + new_content[end:]
        offset += len(replacement) - (match.end() - match.start())
        
    return new_content

async def send_rendered_content(matcher: Matcher, content: str, width: int = 800):
    """渲染 Markdown 并发送"""
    # 1. 预处理图片：将相对路径转换为 Base64
    processed_content = await fix_image_urls(content)
    
    # 2. 尝试渲染全图
    try:
        img_bytes = await md_to_pic(md=processed_content, width=width)
    except Exception as e:
        await matcher.finish(f"【渲染失败】: {e}\n原文：\n{content}")
    
    await matcher.finish(MessageSegment.image(img_bytes))

async def send_text_as_pic(matcher: Matcher, text: str, title: str = "", width: int = 600):
    """将普通文本包装成 Markdown 并渲染为图片发送"""
    # 确保 Markdown 列表和换行能被正确解析
    md_lines = []
    if title:
        md_lines.append(f"# {title}")
        md_lines.append("---")
    
    md_lines.append(text)
    md = "\n\n".join(md_lines)
    
    try:
        img_bytes = await md_to_pic(md=md, width=width)
    except Exception as e:
        await matcher.finish(f"【渲染失败】: {e}\n{text}")
    
    await matcher.finish(MessageSegment.image(img_bytes))

@blog.handle()
async def _(bot: Bot, matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    arg_str = args.extract_plain_text().strip()
    if not arg_str:
        await send_text_as_pic(matcher, __plugin_meta__.usage, title="Astro Blog 管理")
    
    parts = arg_str.split(maxsplit=1)
    subcommand = parts[0]
    subargs = parts[1] if len(parts) > 1 else ""

    # 权限检查：记录小事、创建、更新、删除博客仅超级用户可用
    if subcommand in ["new", "update", "del", "thought"]:
        if event.get_user_id() not in superusers:
            await matcher.finish("只有超级用户可以使用该功能。")

    # 帮助命令
    if subcommand == "help":
        await send_text_as_pic(matcher, __plugin_meta__.usage, title="Astro Blog 帮助")

    # --- 文章管理 ---
    
    # 列出所有文章 (递归搜索子目录)
    if subcommand == "list":
        files = await gh.list_files_recursive(config.blog_path)
        if not files:
            await matcher.finish("没有找到文章或获取失败，请检查 BLOG_PATH 是否正确")
        
        items = []
        for f in files:
            if f["name"].endswith((".md", ".mdx")):
                slug = f["name"].rsplit(".", 1)[0]
                items.append(f"- {slug}")
        
        if not items:
            await matcher.finish(f"在 {config.blog_path} 下未发现 .md 或 .mdx 文件")
        
        msg = "\n".join(items)
        await send_text_as_pic(matcher, msg, title="文章列表")

    # 查看文章源码 (渲染为图片)
    elif subcommand == "view":
        if not subargs:
            await matcher.finish("请输入文章 slug：blog view <slug>")
        
        # 尝试匹配 slug 对应的文件
        file_path = f"{config.blog_path}/{subargs}"
        file_info = None
        # 如果 subargs 没带后缀，尝试补全
        if not (file_path.endswith(".md") or file_path.endswith(".mdx")):
            file_info = await gh.get_file(f"{file_path}.md") or await gh.get_file(f"{file_path}.mdx")
        else:
            file_info = await gh.get_file(file_path)
             
        if not file_info:
            await matcher.finish(f"文章 {subargs} 不存在")
        
        content = base64.b64decode(file_info["content"]).decode("utf-8")
        await send_rendered_content(matcher, content, width=800)

    # 创建新文章 (进入交互模式)
    elif subcommand == "new":
        if not subargs:
            await matcher.finish("请输入文章标题：blog new <标题>")
        matcher.set_arg("title", Message(subargs))
    
    # 更新文章 (进入交互模式)
    elif subcommand == "update":
        if not subargs:
            await matcher.finish("请输入文章 slug：blog update <slug>")
        matcher.set_arg("slug", Message(subargs))

    # 删除文章
    elif subcommand == "del":
        if not subargs:
            await matcher.finish("请输入文章 slug：blog del <slug>")
        
        # 尝试查找文件获取 SHA
        file_path = f"{config.blog_path}/{subargs}.md"
        file_info = await gh.get_file(file_path) or await gh.get_file(f"{config.blog_path}/{subargs}.mdx")
        
        if not file_info:
            await matcher.finish(f"文章 {subargs} 不存在")
        
        success = await gh.delete_file(file_info["path"], f"Delete article {subargs}", file_info["sha"])
        if success:
            await matcher.finish(f"文章 {subargs} 已删除")
        else:
            await matcher.finish(f"文章 {subargs} 删除失败")

    # --- 小事记录 ---

    # 记录一件小事 (支持图片)
    elif subcommand == "thought":
        if not subargs and len(event.message["image"]) == 0:
            await matcher.finish("请输入小事内容：blog thought <内容>")
        
        # 提取除去 "thought" 指令后的消息内容
        thought_msg = args.copy()
        for seg in thought_msg:
            if seg.type == "text":
                seg.data["text"] = seg.data["text"].replace(subcommand, "", 1).lstrip()
                break
        
        content, _ = await handle_images(thought_msg)
        content = content.strip()
        
        if not content:
            await matcher.finish("内容不能为空")
            
        now = datetime.now()
        # 优化文件名命名：使用日期和时间戳，确保唯一性
        filename = now.strftime("%Y-%m-%d-%H%M%S.md")
        path = f"{config.thought_path}/{filename}"
        
        # 构造 Markdown 内容 (包含 Frontmatter)
        # 修复 build 报错：使用更标准的日期格式，并确保 published 字段存在
        date_str = now.strftime("%Y-%m-%d %H:%M:%S")
        frontmatter = f"---\ndate: {date_str}\npublished: {date_str}\n---\n\n"
        full_content = frontmatter + content
        
        success = await gh.upload_file(path, full_content.encode("utf-8"), f"Record thought {filename}")
        if success:
            await matcher.finish(f"已记录小事：{filename}")
        else:
            await matcher.finish("记录失败")

    # 查看最近的小事列表
    elif subcommand == "thoughts":
        files = await gh.list_files_recursive(config.thought_path)
        if not files:
            await matcher.finish("没有找到记录的小事")
        
        # 按文件名倒序排列 (最新的在前)
        files.sort(key=lambda x: x["name"], reverse=True)
        
        items = []
        for f in files[:10]: # 只显示最近10条
            if f["name"].endswith(".md"):
                items.append(f"- {f['name']}")
        
        if not items:
            await matcher.finish("没有找到记录的小事")
            
        msg = "\n".join(items)
        msg += "\n\n> 提示：使用 `blog view_thought <文件名>` 查看详情"
        await send_text_as_pic(matcher, msg, title="最近小事")

    # 查看单条小事内容 (渲染为图片)
    elif subcommand == "view_thought":
        if not subargs:
            await matcher.finish("请输入小事文件名：blog view_thought <文件名>")
        
        path = f"{config.thought_path}/{subargs}"
        if not path.endswith(".md"):
            path += ".md"
            
        file_info = await gh.get_file(path)
        if not file_info:
            await matcher.finish("该记录不存在")
            
        content = base64.b64decode(file_info["content"]).decode("utf-8")
        await send_rendered_content(matcher, content, width=500)

@blog.got("title", prompt="请输入文章标题：")
async def handle_new_title(matcher: Matcher):
    # 标题已自动存入 state["title"]
    pass

@blog.got("content", prompt="请输入文章内容（支持图片）：")
async def handle_new_content(
    bot: Bot, 
    event: MessageEvent, 
    matcher: Matcher,
    title: Message = Arg("title"),
    content_msg: Message = Arg("content")
):
    title_str = title.extract_plain_text().strip()
    content, _ = await handle_images(content_msg)
    
    slug = title_str.lower().replace(" ", "-") # Simple slugify
    path = f"{config.blog_path}/{slug}.md"
    
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    frontmatter = f"""---
title: "{title_str}"
pubDate: {date_str}
published: {date_str}
description: "{title_str}"
---

"""
    full_content = frontmatter + content
    
    success = await gh.upload_file(path, full_content.encode("utf-8"), f"Create article {title_str}")
    if success:
        await matcher.finish(f"文章《{title_str}》创建成功！\n路径：{path}")
    else:
        await matcher.finish("文章创建失败，请检查配置或日志")

@blog.got("update_content", prompt="请输入更新后的内容（支持图片）：")
async def handle_update_content(
    bot: Bot, 
    event: MessageEvent, 
    matcher: Matcher,
    slug: str = ArgPlainText("slug"),
    update_content: Message = Arg("update_content")
):
    content, _ = await handle_images(update_content)
    
    path = f"{config.blog_path}/{slug}.md"
    file_info = await gh.get_file(path)
    if not file_info:
        await matcher.finish(f"文章 {slug} 不存在")
    
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    frontmatter = f"""---
title: "{slug}"
pubDate: {date_str}
updatedDate: {date_str}
published: {date_str}
---

"""
    full_content = frontmatter + content
    
    success = await gh.upload_file(path, full_content.encode("utf-8"), f"Update article {slug}", file_info["sha"])
    if success:
        await matcher.finish(f"文章 {slug} 更新成功！")
    else:
        await matcher.finish("文章更新失败")

