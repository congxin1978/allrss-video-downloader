"""
allrss.se 视频下载器 - Web 界面
启动: python app.py
然后浏览器打开 http://localhost:5000
"""

import json
import queue
import subprocess
import threading
import time
from pathlib import Path

import feedparser
import requests
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

app = Flask(__name__)

MAIN_RSS = "https://allrss.se/dramas/"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RSSBot/1.0)"}
DEFAULT_OUT = str(Path("./downloads").resolve())

_log_queue: queue.Queue = queue.Queue()
_is_running = False


# ──────────────────────────────────────────
# RSS 工具函数
# ──────────────────────────────────────────

def fetch_rss(url: str):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return feedparser.parse(r.text)
    except Exception as e:
        return None


def get_channels() -> list[dict]:
    feed = fetch_rss(MAIN_RSS)
    if not feed:
        return []
    result = []
    for entry in feed.entries:
        title = entry.get("title", "")
        sub_url = None
        for link in entry.get("links", []):
            if link.get("type") == "application/rss+xml":
                sub_url = link.get("href")
                break
        if sub_url:
            result.append({"title": title, "url": sub_url})
    return result


def extract_video_urls(sub_feed) -> list[tuple]:
    videos = []
    for entry in sub_feed.entries:
        title = entry.get("title", "unknown")
        found = False
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
        content = entry.get("summary", "") + "".join(
            c.get("value", "") for c in entry.get("content", [])
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
                    u = content[q1 + 1 : q2]
                    if u.startswith("http"):
                        videos.append((title, u))
                        found = True
                        break
            if found:
                break
    return videos


# ──────────────────────────────────────────
# 下载任务（后台线程）
# ──────────────────────────────────────────

def _log(msg: str, kind: str = "info"):
    _log_queue.put({"type": kind, "msg": msg})


def run_download(channels: list[dict], max_eps: int, out_dir: str):
    global _is_running
    save_root = Path(out_dir)

    for ch in channels:
        _log(f"── 频道：{ch['title']}", "channel")
        time.sleep(1)
        feed = fetch_rss(ch["url"])
        if not feed:
            _log("  ✗ 无法获取 RSS", "error")
            continue

        videos = extract_video_urls(feed)
        _log(f"  找到 {len(videos)} 个视频链接")

        if not videos:
            _log("  ⚠ 该频道暂无可直接解析的视频链接", "warn")
            continue

        limit = max_eps if max_eps > 0 else len(videos)
        save_dir = save_root / ch["title"]
        save_dir.mkdir(parents=True, exist_ok=True)

        for title, url in videos[:limit]:
            safe = "".join(c for c in title if c not in r'\/:*?"<>|').strip()[:80]
            out_tpl = str(save_dir / f"{safe}.%(ext)s")
            cmd = ["yt-dlp", "--no-playlist", "--retries", "3",
                   "--fragment-retries", "3", "-o", out_tpl, url]

            _log(f"  ▶ {title[:55]}", "dl")
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                if res.returncode == 0:
                    _log(f"  ✓ 完成", "ok")
                else:
                    _log(f"  ✗ 失败：{res.stderr[-200:]}", "error")
            except subprocess.TimeoutExpired:
                _log("  ✗ 超时（10 分钟）", "error")
            except FileNotFoundError:
                _log("  ✗ 未找到 yt-dlp，请运行：pip install yt-dlp", "error")
            time.sleep(2)

    _log(f"\n全部完成！文件保存在：{save_root.resolve()}", "done")
    _log("__DONE__", "done")
    _is_running = False


# ──────────────────────────────────────────
# 路由
# ──────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", default_out=DEFAULT_OUT)


@app.route("/api/channels")
def api_channels():
    channels = get_channels()
    return jsonify(channels)


@app.route("/api/download", methods=["POST"])
def api_download():
    global _is_running, _log_queue
    if _is_running:
        return jsonify({"error": "已有下载任务进行中，请等待完成"}), 400

    data = request.get_json()
    selected = data.get("channels", [])   # [{"title":..., "url":...}]
    max_eps  = int(data.get("max", 0))
    out_dir  = data.get("out", DEFAULT_OUT).strip() or DEFAULT_OUT

    if not selected:
        return jsonify({"error": "请至少选择一个频道"}), 400

    _log_queue = queue.Queue()
    _is_running = True
    _log("开始下载任务…", "info")

    t = threading.Thread(target=run_download,
                         args=(selected, max_eps, out_dir), daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    return jsonify({"running": _is_running})


@app.route("/api/logs")
def api_logs():
    """SSE 实时日志流"""
    def generate():
        while True:
            try:
                item = _log_queue.get(timeout=30)
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                if item.get("msg") == "__DONE__":
                    break
            except queue.Empty:
                yield "data: {\"type\":\"ping\"}\n\n"
    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    print("=" * 45)
    print("  allrss 下载器已启动")
    print("  浏览器打开: http://localhost:5000")
    print("=" * 45)
    app.run(host="0.0.0.0", port=5000, debug=False)
