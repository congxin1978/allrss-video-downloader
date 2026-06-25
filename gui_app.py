"""
allrss 视频下载器 — 桌面版
直接运行: python gui_app.py
"""
import queue
import threading
import time
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
import feedparser
import requests
import yt_dlp

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

MAIN_RSS = "https://allrss.se/dramas/"
HEADERS  = {"User-Agent": "Mozilla/5.0 (compatible; RSSBot/1.0)"}


# ── RSS 工具 ────────────────────────────────────────────

def fetch_rss(url: str):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return feedparser.parse(r.text)
    except Exception:
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
        for kw in ["file=", "url=", "src="]:
            idx = content.find(kw)
            if idx == -1:
                continue
            start = idx + len(kw)
            for q in ['"', "'"]:
                q1 = content.find(q, start)
                q2 = content.find(q, q1 + 1) if q1 != -1 else -1
                if 0 < q1 < q2:
                    u = content[q1 + 1:q2]
                    if u.startswith("http"):
                        videos.append((title, u))
                        found = True
                        break
            if found:
                break
    return videos


def download_video(url: str, save_path: str, log_fn):
    """用 yt-dlp Python API 下载（适合打包进 APP）"""
    class Logger:
        def debug(self, msg):
            if msg.startswith("[download]"):
                log_fn("    " + msg.strip())
        def warning(self, msg): pass
        def error(self, msg): log_fn("  ✗ " + msg.strip())

    opts = {
        "outtmpl":          save_path,
        "retries":          3,
        "fragment_retries": 3,
        "noplaylist":       True,
        "logger":           Logger(),
        "quiet":            True,
        "no_warnings":      True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        return True
    except Exception as e:
        log_fn(f"  ✗ 失败：{e}")
        return False


# ── 主界面 ──────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("allrss 视频下载器")
        self.geometry("980x700")
        self.minsize(820, 560)

        self.channels:  list[dict]    = []
        self.ch_vars:   list[ctk.BooleanVar] = []
        self.ch_boxes:  list          = []
        self.log_q:     queue.Queue   = queue.Queue()
        self.downloading              = False

        self._build_ui()
        self.after(200, self._load_channels_async)
        self._poll_log()

    # ── 构建 UI ─────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=5)
        self.grid_columnconfigure(1, weight=7)
        self.grid_rowconfigure(0, weight=1)

        # ── 左栏：频道 ──────────────────────
        lf = ctk.CTkFrame(self)
        lf.grid(row=0, column=0, padx=(14, 7), pady=14, sticky="nsew")
        lf.grid_columnconfigure(0, weight=1)
        lf.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(lf, text="选择频道",
                     font=ctk.CTkFont(size=15, weight="bold")
                     ).grid(row=0, column=0, padx=14, pady=(14, 6), sticky="w")

        # 全选 / 清空
        btn_row = ctk.CTkFrame(lf, fg_color="transparent")
        btn_row.grid(row=1, column=0, padx=14, pady=(0, 8), sticky="ew")
        ctk.CTkButton(btn_row, text="全选", width=64, height=28,
                      command=self._select_all).pack(side="left", padx=(0, 6))
        ctk.CTkButton(btn_row, text="清空", width=64, height=28,
                      fg_color="#374151", hover_color="#4b5563",
                      command=self._clear_all).pack(side="left")

        self.scroll = ctk.CTkScrollableFrame(lf)
        self.scroll.grid(row=2, column=0, padx=10, pady=(0, 10), sticky="nsew")
        self.scroll.grid_columnconfigure(0, weight=1)

        self.loading_lbl = ctk.CTkLabel(
            self.scroll, text="⏳  正在加载频道列表…", text_color="gray")
        self.loading_lbl.pack(pady=40)

        # ── 右栏：选项 + 日志 ───────────────
        rf = ctk.CTkFrame(self)
        rf.grid(row=0, column=1, padx=(7, 14), pady=14, sticky="nsew")
        rf.grid_columnconfigure(0, weight=1)
        rf.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(rf, text="下载选项",
                     font=ctk.CTkFont(size=15, weight="bold")
                     ).grid(row=0, column=0, padx=14, pady=(14, 6), sticky="w")

        # 选项区
        of = ctk.CTkFrame(rf, fg_color="transparent")
        of.grid(row=1, column=0, padx=14, pady=(0, 10), sticky="ew")
        of.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(of, text="保存目录", font=ctk.CTkFont(size=12)
                     ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))

        self.folder_var = ctk.StringVar(
            value=str(Path.home() / "Downloads" / "dramas"))
        ctk.CTkEntry(of, textvariable=self.folder_var).grid(
            row=1, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkButton(of, text="浏览", width=60,
                      command=self._choose_folder).grid(row=1, column=1)

        ctk.CTkLabel(of, text="每频道集数（0 = 不限）",
                     font=ctk.CTkFont(size=12)
                     ).grid(row=2, column=0, sticky="w", pady=(12, 4))
        self.max_var = ctk.StringVar(value="0")
        ctk.CTkEntry(of, textvariable=self.max_var, width=80).grid(
            row=3, column=0, sticky="w")

        # 下载按钮
        self.dl_btn = ctk.CTkButton(
            rf, text="▶   开始下载", height=46,
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self._start_download)
        self.dl_btn.grid(row=2, column=0, padx=14, pady=(4, 10), sticky="ew")

        # 日志
        ctk.CTkLabel(rf, text="下载日志",
                     font=ctk.CTkFont(size=13, weight="bold")
                     ).grid(row=3, column=0, padx=14, pady=(0, 4), sticky="w")

        self.log_box = ctk.CTkTextbox(
            rf, font=ctk.CTkFont(family="Courier New", size=12),
            wrap="word", state="disabled")
        self.log_box.grid(row=4, column=0, padx=14, pady=(0, 14), sticky="nsew")

    # ── 频道加载 ─────────────────────────────────────────

    def _load_channels_async(self):
        def run():
            chs = get_channels()
            self.after(0, lambda: self._populate_channels(chs))
        threading.Thread(target=run, daemon=True).start()

    def _populate_channels(self, channels: list[dict]):
        self.channels = channels
        self.ch_vars  = []
        self.ch_boxes = []
        if self.loading_lbl:
            self.loading_lbl.destroy()
        for ch in channels:
            var = ctk.BooleanVar(value=False)
            cb  = ctk.CTkCheckBox(self.scroll, text=ch["title"],
                                  variable=var, font=ctk.CTkFont(size=13))
            cb.pack(anchor="w", padx=10, pady=4)
            self.ch_vars.append(var)
            self.ch_boxes.append(cb)
        self._log(f"✓ 加载完成，共 {len(channels)} 个频道，请勾选后点开始下载\n")

    def _select_all(self):
        for v in self.ch_vars: v.set(True)

    def _clear_all(self):
        for v in self.ch_vars: v.set(False)

    def _choose_folder(self):
        d = filedialog.askdirectory(title="选择保存目录")
        if d:
            self.folder_var.set(d)

    # ── 下载任务 ─────────────────────────────────────────

    def _start_download(self):
        if self.downloading:
            return
        selected = [self.channels[i]
                    for i, v in enumerate(self.ch_vars) if v.get()]
        if not selected:
            messagebox.showwarning("提示", "请先勾选至少一个频道")
            return

        try:
            max_eps = int(self.max_var.get() or 0)
        except ValueError:
            max_eps = 0
        out_dir = Path(self.folder_var.get())

        self.downloading = True
        self.dl_btn.configure(text="⏳  下载中…", state="disabled")
        self.log_q.put("━" * 42 + "\n开始下载任务…\n")

        def run():
            for ch in selected:
                self.log_q.put(f"\n── {ch['title']}\n")
                time.sleep(0.8)
                feed = fetch_rss(ch["url"])
                if not feed:
                    self.log_q.put("  ✗ 无法获取 RSS\n")
                    continue
                videos = extract_video_urls(feed)
                self.log_q.put(f"  找到 {len(videos)} 个视频\n")
                if not videos:
                    self.log_q.put("  ⚠ 暂无可直接解析的视频链接\n")
                    continue
                limit   = max_eps if max_eps > 0 else len(videos)
                save_dir = out_dir / ch["title"]
                save_dir.mkdir(parents=True, exist_ok=True)
                for title, url in videos[:limit]:
                    safe = "".join(
                        c for c in title if c not in r'\/:*?"<>|').strip()[:80]
                    out_tpl = str(save_dir / f"{safe}.%(ext)s")
                    self.log_q.put(f"  ▶ {title[:52]}\n")
                    ok = download_video(url, out_tpl, self.log_q.put)
                    if ok:
                        self.log_q.put("  ✓ 完成\n")
                    time.sleep(1.5)
            self.log_q.put(
                f"\n{'━'*42}\n✅ 全部完成！\n文件保存在：{out_dir.resolve()}\n")
            self.after(0, self._on_done)

        threading.Thread(target=run, daemon=True).start()

    def _on_done(self):
        self.downloading = False
        self.dl_btn.configure(text="▶   开始下载", state="normal")

    # ── 日志轮询 ─────────────────────────────────────────

    def _log(self, msg: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _poll_log(self):
        try:
            while True:
                self._log(self.log_q.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll_log)


# ── 入口 ─────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
