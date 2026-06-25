"""
allrss 视频下载器 — 桌面版 v2
三栏设计：频道 → 剧集 → 下载
"""
import queue, threading, time
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
import feedparser, requests, yt_dlp

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

MAIN_RSS = "https://allrss.se/dramas/"
HEADERS  = {"User-Agent": "Mozilla/5.0 (compatible; RSSBot/1.0)"}

# ── RSS 工具 ────────────────────────────────────────────

def fetch_rss(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return feedparser.parse(r.text)
    except:
        return None

def get_channels():
    feed = fetch_rss(MAIN_RSS)
    if not feed:
        return []
    result = []
    for e in feed.entries:
        title = e.get("title", "")
        url = next((l["href"] for l in e.get("links", [])
                    if l.get("type") == "application/rss+xml"), None)
        if url:
            result.append({"title": title, "url": url})
    return result

def get_episodes(channel_url):
    """获取频道里的剧集列表"""
    feed = fetch_rss(channel_url)
    if not feed:
        return []
    episodes = []
    for e in feed.entries:
        title = e.get("title", "unknown")
        video_url = None
        # 方法1: enclosure/links
        for link in e.get("links", []):
            href = link.get("href", "")
            mime = link.get("type", "")
            if any(x in href.lower() for x in [".m3u8", ".mp4", ".mkv"]):
                video_url = href; break
            if "video" in mime:
                video_url = href; break
        # 方法2: HTML 内容
        if not video_url:
            content = e.get("summary", "") + "".join(
                c.get("value", "") for c in e.get("content", []))
            for kw in ["file=", "url=", "src="]:
                idx = content.find(kw)
                if idx == -1: continue
                start = idx + len(kw)
                for q in ['"', "'"]:
                    q1 = content.find(q, start)
                    q2 = content.find(q, q1+1) if q1!=-1 else -1
                    if 0 < q1 < q2:
                        u = content[q1+1:q2]
                        if u.startswith("http"):
                            video_url = u; break
                if video_url: break
        episodes.append({"title": title, "url": video_url})
    return episodes

def download_video(url, save_path, log_fn):
    class Logger:
        def debug(self, m):
            if "[download]" in m: log_fn("    " + m.strip())
        def warning(self, m): pass
        def error(self, m): log_fn("  ✗ " + m.strip())
    try:
        with yt_dlp.YoutubeDL({
            "outtmpl": save_path, "retries": 3,
            "fragment_retries": 3, "noplaylist": True,
            "logger": Logger(), "quiet": True, "no_warnings": True
        }) as ydl:
            ydl.download([url])
        return True
    except Exception as ex:
        log_fn(f"  ✗ {ex}"); return False

# ── 主界面 ──────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("allrss 视频下载器")
        self.geometry("1200x720")
        self.minsize(900, 560)

        self.channels      = []
        self.ch_btns       = []
        self.episodes      = []        # 当前频道的剧集
        self.ep_vars       = []        # 剧集复选框变量
        self.cur_channel   = None
        self.log_q         = queue.Queue()
        self.downloading   = False

        self._build_ui()
        self.after(200, self._load_channels_async)
        self._poll_log()

    # ── UI 构建 ──────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=2)   # 频道栏
        self.grid_columnconfigure(1, weight=3)   # 剧集栏
        self.grid_columnconfigure(2, weight=3)   # 选项栏
        self.grid_rowconfigure(0, weight=1)

        # ── 栏1：频道 ───────────────────────────
        c1 = ctk.CTkFrame(self)
        c1.grid(row=0, column=0, padx=(14,6), pady=14, sticky="nsew")
        c1.grid_columnconfigure(0, weight=1)
        c1.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(c1, text="选择频道",
                     font=ctk.CTkFont(size=14, weight="bold")
                     ).grid(row=0, column=0, padx=12, pady=(12,8), sticky="w")

        self.ch_scroll = ctk.CTkScrollableFrame(c1)
        self.ch_scroll.grid(row=1, column=0, padx=8, pady=(0,10), sticky="nsew")
        self.ch_scroll.grid_columnconfigure(0, weight=1)

        self.ch_loading = ctk.CTkLabel(self.ch_scroll,
                                       text="⏳ 加载中…", text_color="gray")
        self.ch_loading.pack(pady=30)

        # ── 栏2：剧集 ───────────────────────────
        c2 = ctk.CTkFrame(self)
        c2.grid(row=0, column=1, padx=6, pady=14, sticky="nsew")
        c2.grid_columnconfigure(0, weight=1)
        c2.grid_rowconfigure(2, weight=1)

        self.ep_title_lbl = ctk.CTkLabel(c2, text="剧集列表",
                                          font=ctk.CTkFont(size=14, weight="bold"))
        self.ep_title_lbl.grid(row=0, column=0, padx=12, pady=(12,6), sticky="w")

        ep_btn_row = ctk.CTkFrame(c2, fg_color="transparent")
        ep_btn_row.grid(row=1, column=0, padx=12, pady=(0,8), sticky="ew")
        ctk.CTkButton(ep_btn_row, text="全选", width=60, height=26,
                      command=self._ep_select_all).pack(side="left", padx=(0,6))
        ctk.CTkButton(ep_btn_row, text="清空", width=60, height=26,
                      fg_color="#374151", hover_color="#4b5563",
                      command=self._ep_clear_all).pack(side="left")

        self.ep_scroll = ctk.CTkScrollableFrame(c2)
        self.ep_scroll.grid(row=2, column=0, padx=8, pady=(0,10), sticky="nsew")
        self.ep_scroll.grid_columnconfigure(0, weight=1)

        self.ep_placeholder = ctk.CTkLabel(self.ep_scroll,
                                           text="← 先点左边选一个频道",
                                           text_color="gray")
        self.ep_placeholder.pack(pady=40)

        # ── 栏3：选项 + 日志 ─────────────────────
        c3 = ctk.CTkFrame(self)
        c3.grid(row=0, column=2, padx=(6,14), pady=14, sticky="nsew")
        c3.grid_columnconfigure(0, weight=1)
        c3.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(c3, text="下载选项",
                     font=ctk.CTkFont(size=14, weight="bold")
                     ).grid(row=0, column=0, padx=12, pady=(12,6), sticky="w")

        of = ctk.CTkFrame(c3, fg_color="transparent")
        of.grid(row=1, column=0, padx=12, pady=(0,8), sticky="ew")
        of.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(of, text="保存目录",
                     font=ctk.CTkFont(size=12)).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0,4))
        self.folder_var = ctk.StringVar(
            value=str(Path.home() / "Downloads" / "dramas"))
        ctk.CTkEntry(of, textvariable=self.folder_var).grid(
            row=1, column=0, sticky="ew", padx=(0,8))
        ctk.CTkButton(of, text="浏览", width=58,
                      command=self._choose_folder).grid(row=1, column=1)

        self.dl_btn = ctk.CTkButton(
            c3, text="▶   开始下载", height=44,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._start_download)
        self.dl_btn.grid(row=2, column=0, padx=12, pady=(4,8), sticky="ew")

        ctk.CTkLabel(c3, text="下载日志",
                     font=ctk.CTkFont(size=13, weight="bold")
                     ).grid(row=3, column=0, padx=12, pady=(0,4), sticky="w")

        self.log_box = ctk.CTkTextbox(
            c3, font=ctk.CTkFont(family="Courier New", size=11),
            wrap="word", state="disabled")
        self.log_box.grid(row=4, column=0, padx=12, pady=(0,12), sticky="nsew")

    # ── 频道加载 ─────────────────────────────────────────

    def _load_channels_async(self):
        def run():
            chs = get_channels()
            self.after(0, lambda: self._populate_channels(chs))
        threading.Thread(target=run, daemon=True).start()

    def _populate_channels(self, channels):
        self.channels = channels
        self.ch_btns  = []
        if self.ch_loading:
            self.ch_loading.destroy()
        for i, ch in enumerate(channels):
            btn = ctk.CTkButton(
                self.ch_scroll, text=ch["title"], anchor="w",
                fg_color="transparent", hover_color="#2d3447",
                text_color=("gray10","gray90"),
                font=ctk.CTkFont(size=13),
                command=lambda c=ch: self._load_episodes(c))
            btn.pack(fill="x", padx=4, pady=2)
            self.ch_btns.append(btn)
        self._log(f"✓ {len(channels)} 个频道，点左边选一个\n")

    def _load_episodes(self, channel):
        """点击频道 → 加载剧集"""
        # 高亮选中的频道按钮
        for b in self.ch_btns:
            b.configure(fg_color="transparent")
        for b in self.ch_btns:
            if b.cget("text") == channel["title"]:
                b.configure(fg_color="#1e3a5f")
                break

        self.cur_channel = channel
        self.ep_title_lbl.configure(text=f"📺  {channel['title']}")

        # 清空剧集列表
        for w in self.ep_scroll.winfo_children():
            w.destroy()
        self.ep_vars = []
        self.episodes = []

        loading = ctk.CTkLabel(self.ep_scroll,
                               text="⏳ 正在加载剧集…", text_color="gray")
        loading.pack(pady=30)
        self._log(f"\n加载「{channel['title']}」剧集列表…\n")

        def run():
            eps = get_episodes(channel["url"])
            self.after(0, lambda: self._populate_episodes(eps, loading))

        threading.Thread(target=run, daemon=True).start()

    def _populate_episodes(self, episodes, loading_lbl):
        loading_lbl.destroy()
        self.episodes = episodes
        self.ep_vars  = []

        if not episodes:
            ctk.CTkLabel(self.ep_scroll,
                         text="⚠ 暂无可解析的剧集", text_color="gray").pack(pady=30)
            self._log("  ⚠ 暂无可直接解析的视频链接\n")
            return

        for ep in episodes:
            var = ctk.BooleanVar(value=False)
            has_url = ep["url"] is not None
            text = ep["title"] if has_url else f"[无链接] {ep['title']}"
            color = ("gray10","gray90") if has_url else ("gray50","gray50")
            cb = ctk.CTkCheckBox(
                self.ep_scroll, text=text, variable=var,
                font=ctk.CTkFont(size=12), text_color=color,
                state="normal" if has_url else "disabled")
            cb.pack(anchor="w", padx=8, pady=3, fill="x")
            self.ep_vars.append(var)

        self._log(f"  ✓ 找到 {len(episodes)} 集，勾选后点开始下载\n")

    def _ep_select_all(self):
        for v in self.ep_vars: v.set(True)

    def _ep_clear_all(self):
        for v in self.ep_vars: v.set(False)

    # ── 下载 ─────────────────────────────────────────────

    def _choose_folder(self):
        d = filedialog.askdirectory(title="选择保存目录")
        if d: self.folder_var.set(d)

    def _start_download(self):
        if self.downloading:
            return
        if not self.cur_channel:
            messagebox.showwarning("提示", "请先在左边选择一个频道"); return

        selected = [(self.episodes[i], v)
                    for i, v in enumerate(self.ep_vars) if v.get()]
        if not selected:
            messagebox.showwarning("提示", "请先勾选要下载的剧集"); return

        out_dir = Path(self.folder_var.get())
        save_dir = out_dir / self.cur_channel["title"]
        save_dir.mkdir(parents=True, exist_ok=True)

        self.downloading = True
        self.dl_btn.configure(text="⏳  下载中…", state="disabled")
        self.log_q.put(f"\n{'━'*38}\n开始下载「{self.cur_channel['title']}」\n")

        def run():
            for ep, _ in selected:
                if not ep["url"]:
                    self.log_q.put(f"  跳过（无链接）：{ep['title'][:40]}\n")
                    continue
                safe = "".join(c for c in ep["title"]
                               if c not in r'\/:*?"<>|').strip()[:80]
                self.log_q.put(f"  ▶ {ep['title'][:52]}\n")
                ok = download_video(ep["url"],
                                    str(save_dir / f"{safe}.%(ext)s"),
                                    self.log_q.put)
                if ok:
                    self.log_q.put("  ✓ 完成\n")
                time.sleep(1)
            self.log_q.put(f"\n✅ 完成！保存在：{save_dir.resolve()}\n")
            self.after(0, self._on_done)

        threading.Thread(target=run, daemon=True).start()

    def _on_done(self):
        self.downloading = False
        self.dl_btn.configure(text="▶   开始下载", state="normal")

    # ── 日志 ─────────────────────────────────────────────

    def _log(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _poll_log(self):
        try:
            while True: self._log(self.log_q.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll_log)


if __name__ == "__main__":
    App().mainloop()
