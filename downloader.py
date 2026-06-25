"""
allrss.se 视频爬虫
用法: python downloader.py [--channel "Korean Drama"] [--max 5]
依赖: pip install -r requirements.txt
"""

import os
import time
import argparse
import requests
import feedparser
import subprocess
from pathlib import Path

# ========== 默认配置 ==========
MAIN_RSS    = "https://allrss.se/dramas/"
DOWNLOAD_DIR = Path("./downloads")
DELAY       = 2      # 请求间隔（秒）
MAX_EPISODES = 0     # 0 = 不限制
# ==============================

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RSSBot/1.0)"}


def fetch_rss(url: str):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return feedparser.parse(r.text)
    except Exception as e:
        print(f"  [错误] 无法获取 RSS: {url}\n  {e}")
        return None


def get_channels(feed) -> list[dict]:
    channels = []
    for entry in feed.entries:
        title = entry.get("title", "")
        sub_url = None
        for link in entry.get("links", []):
            if link.get("type") == "application/rss+xml":
                sub_url = link.get("href")
                break
        if sub_url:
            channels.append({"title": title, "url": sub_url})
    return channels


def extract_video_urls(sub_feed) -> list[tuple]:
    videos = []
    for entry in sub_feed.entries:
        title = entry.get("title", "unknown")
        found = False

        # 方法1: enclosure / links
        for link in entry.get("links", []):
            href = link.get("href", "")
            mime = link.get("type", "")
            if any(ext in href.lower() for ext in [".m3u8", ".mp4", ".mkv"]):
                videos.append((title, href))
                found = True
                break
            if "video" in mime:
                videos.append((title, href))
                found = True
                break

        if found:
            continue

        # 方法2: 从 HTML 内容提取
        content = (
            entry.get("summary", "") +
            "".join(c.get("value", "") for c in entry.get("content", []))
        )
        for kw in ["file=", "url=", "src=", "source src="]:
            idx = content.find(kw)
            if idx == -1:
                continue
            start = idx + len(kw)
            for q in ['"', "'"]:
                q1 = content.find(q, start)
                q2 = content.find(q, q1 + 1) if q1 != -1 else -1
                if 0 < q1 < q2:
                    url = content[q1 + 1:q2]
                    if url.startswith("http"):
                        videos.append((title, url))
                        found = True
                        break
            if found:
                break

    return videos


def download(title: str, url: str, save_dir: Path):
    save_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in title if c not in r'\/:*?"<>|').strip()[:80]
    out  = str(save_dir / f"{safe}.%(ext)s")
    cmd  = ["yt-dlp", "--no-playlist", "--retries", "3",
            "--fragment-retries", "3", "-o", out, url]

    print(f"  ▶ {title[:55]}")
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if res.returncode == 0:
            print("  ✓ 完成")
        else:
            print(f"  ✗ 失败\n{res.stderr[-300:]}")
    except subprocess.TimeoutExpired:
        print("  ✗ 超时（10分钟）")
    except FileNotFoundError:
        print("  ✗ 未找到 yt-dlp，请运行: pip install yt-dlp")


def main():
    parser = argparse.ArgumentParser(description="allrss.se 视频下载器")
    parser.add_argument("--channel", type=str, default="",
                        help='频道名，例如 "Korean Drama"，留空下载全部')
    parser.add_argument("--max", type=int, default=MAX_EPISODES,
                        help="每频道最多下载集数，0=不限制")
    parser.add_argument("--list", action="store_true",
                        help="只列出所有频道，不下载")
    parser.add_argument("--out", type=str, default=str(DOWNLOAD_DIR),
                        help="下载保存目录")
    args = parser.parse_args()

    save_root = Path(args.out)

    print("=" * 55)
    print("  allrss.se 视频爬虫")
    print("=" * 55)

    print(f"\n获取主 RSS: {MAIN_RSS}")
    feed = fetch_rss(MAIN_RSS)
    if not feed:
        return

    channels = get_channels(feed)
    print(f"找到 {len(channels)} 个频道\n")

    if args.list:
        for i, ch in enumerate(channels, 1):
            print(f"  {i:>2}. {ch['title']}")
        return

    if args.channel:
        channels = [c for c in channels if args.channel.lower() in c["title"].lower()]
        if not channels:
            print(f'未找到频道: "{args.channel}"')
            print("可用频道（运行 --list 查看）")
            return

    for ch in channels:
        print(f"\n{'─'*45}")
        print(f"频道: {ch['title']}")

        time.sleep(DELAY)
        sub = fetch_rss(ch["url"])
        if not sub:
            continue

        videos = extract_video_urls(sub)
        print(f"找到 {len(videos)} 个视频")

        if not videos:
            print("  [提示] 该频道视频链接可能需要二次解析，请提交 Issue")
            continue

        limit = args.max if args.max > 0 else len(videos)
        for title, url in videos[:limit]:
            download(title, url, save_root / ch["title"])
            time.sleep(DELAY)

    print(f"\n{'='*55}")
    print(f"完成！文件保存在: {save_root.resolve()}")


if __name__ == "__main__":
    main()
