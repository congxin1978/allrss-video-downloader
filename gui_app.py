"""
allrss 视频下载器 v3
三层导航：频道 → 剧名 → 选集下载
修复：正确区分 RSS 链接与视频链接
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

# ── RSS 解析 ────────────────────────────────────────────

def fetch_rss(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return feedparser.parse(r.text)
    except:
        return None

def get_channels():
    feed = fetch_rss(MAIN_RSS)
    if not feed: return []
    result = []
    for e in feed.entries:
        url = next((l["href"] for l in e.get("links", [])
                    if l.get("type") == "application/rss+xml"), None)
        if url:
            result.append({"title": e.get("title",""), "url": url})
    return result

def _extract_video_url(entry):
    """从一条 RSS entry 里提取真正的视频 URL，跳过 RSS 链接"""
    VIDEO_EXTS = (".m3u8", ".mp4", ".mkv", ".ts", ".avi", ".flv", ".wmv")

    # 1. links / enclosures
    for link in entry.get("links", []) + entry.get("enclosures", []):
        href = link.get("href") or link.get("url", "")
        mime = link.get("type", "")
        if "rss" in mime or "xml" in mime:   # ← 关键：跳过 RSS 链接
            continue
        if any(href.lower().endswith(ext) for ext in VIDEO_EXTS):
            return href
        if "video" in mime or "mpegurl" in mime:
            return href

    # 2. HTML 内容
    content = entry.get("summary", "") + "".join(
        c.get("value","") for c in entry.get("content",[]))
    for kw in ['file="', "file='", 'src="', "src='",
               'url="', "url='", 'source src="', "source src='"]:
        idx = content.find(kw)
        if idx == -1: continue
        start = idx + len(kw)
        q   = kw[-1]
        end = content.find(q, start)
        if end > start:
            u = content[start:end]
            if u.startswith("http") and not u.endswith((".xml",".rss")):
                return u
    return None

def _extract_sub_rss(entry):
    """从 entry 里找子 RSS 链接"""
    for link in entry.get("links", []) + entry.get("enclosures", []):
        href = link.get("href") or link.get("url", "")
        mime = link.get("type", "")
        if "rss" in mime or "xml" in mime:
            return href
        if href.endswith(".xml") or "rss" in href:
            return href
    return None

def get_shows(channel_url):
    """
    频道 RSS → 剧名列表
    每条返回：{title, sub_rss, direct_url}
    sub_rss:    有子 RSS 时填写（需要再一层获取剧集）
    direct_url: 有直接视频时填写（本身就是单集）
    """
    feed = fetch_rss(channel_url)
    if not feed: return []
    shows = []
    for e in feed.entries:
        title      = e.get("title","unknown")
        sub_rss    = _extract_sub_rss(e)
        direct_url = _extract_video_url(e)
        shows.append({"title": title, "sub_rss": sub_rss, "direct_url": direct_url})
    return shows

def get_episodes(show):
    """
    给定一个 show dict，返回可下载的剧集列表
    [{title, url}]
    """
    # 有子 RSS → 拉取子 RSS 里的每一集
    if show.get("sub_rss"):
        feed = fetch_rss(show["sub_rss"])
        if feed and feed.entries:
            eps = []
            for e in feed.entries:
                url = _extract_video_url(e)
                eps.append({"title": e.get("title","unknown"), "url": url})
            if any(ep["url"] for ep in eps):
                return eps

    # 有直接视频 → 本身就是一集
    if show.get("direct_url"):
        return [{"title": show["title"], "url": show["direct_url"]}]

    return []

def download_video(url, save_path, log_fn):
    class Logger:
        def debug(self, m):
            if "[download]" in m: log_fn("    " + m.strip())
        def warning(self, m): pass
        def error(self, m): log_fn("  ✗ " + m.strip())
    try:
        with yt_dlp.YoutubeDL({
            "outtmpl": save_path, "retries": 3, "fragment_retries": 3,
            "noplaylist": True, "logger": Logger(),
            "quiet": True, "no_warnings": True
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

        self.channels    = []
        self.ch_btns     = []
        self.cur_channel = None   # {title, url}
        self.cur_show    = None   # {title, sub_rss, direct_url}
        self.shows       = []
        self.show_btns   = []
        self.episodes    = []
        self.ep_vars     = []
        self.log_q       = queue.Queue()
        self.downloading = False

        self._build_ui()
        self.after(200, lambda: threading.Thread(
            target=self._bg_load_channels, daemon=True).start())
        self._poll_log()

    # ── 构建 UI ──────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=2)
        self.grid_columnconfigure(1, weight=3)
        self.grid_columnconfigure(2, weight=3)
        self.grid_rowconfigure(0, weight=1)

        # ── 左栏：频道 ───────────────────────────
        lf = ctk.CTkFrame(self)
        lf.grid(row=0, column=0, padx=(14,6), pady=14, sticky="nsew")
        lf.grid_columnconfigure(0, weight=1)
        lf.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(lf, text="选择频道",
                     font=ctk.CTkFont(size=14, weight="bold")
                     ).grid(row=0, column=0, padx=12, pady=(12,8), sticky="w")
        self.ch_scroll = ctk.CTkScrollableFrame(lf)
        self.ch_scroll.grid(row=1, column=0, padx=8, pady=(0,10), sticky="nsew")
        self.ch_loading = ctk.CTkLabel(self.ch_scroll,
                                       text="⏳ 加载中…", text_color="gray")
        self.ch_loading.pack(pady=30)

        # ── 中栏：剧名 / 剧集（共用，三种状态） ──
        mf = ctk.CTkFrame(self)
        mf.grid(row=0, column=1, padx=6, pady=14, sticky="nsew")
        mf.grid_columnconfigure(0, weight=1)
        mf.grid_rowconfigure(2, weight=1)

        # 面包屑导航行
        nav_row = ctk.CTkFrame(mf, fg_color="transparent")
        nav_row.grid(row=0, column=0, padx=12, pady=(12,0), sticky="ew")
        nav_row.grid_columnconfigure(1, weight=1)

        self.back_btn = ctk.CTkButton(
            nav_row, text="◀ 返回", width=72, height=28,
            fg_color="#374151", hover_color="#4b5563",
            command=self._go_back)
        self.back_btn.grid(row=0, column=0, padx=(0,8))
        self.back_btn.grid_remove()   # 初始隐藏

        self.mid_title = ctk.CTkLabel(
            nav_row, text="← 先点左边选频道",
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w")
        self.mid_title.grid(row=0, column=1, sticky="w")

        # 全选 / 清空（仅剧集层显示）
        self.ep_btn_row = ctk.CTkFrame(mf, fg_color="transparent")
        self.ep_btn_row.grid(row=1, column=0, padx=12, pady=(6,4), sticky="ew")
        ctk.CTkButton(self.ep_btn_row, text="全选", width=60, height=26,
                      command=self._ep_select_all).pack(side="left", padx=(0,6))
        ctk.CTkButton(self.ep_btn_row, text="清空", width=60, height=26,
                      fg_color="#374151", hover_color="#4b5563",
                      command=self._ep_clear_all).pack(side="left")
        self.ep_btn_row.grid_remove()

        self.mid_scroll = ctk.CTkScrollableFrame(mf)
        self.mid_scroll.grid(row=2, column=0, padx=8, pady=(0,10), sticky="nsew")

        self.mid_placeholder = ctk.CTkLabel(
            self.mid_scroll, text="← 先点左边选频道", text_color="gray")
        self.mid_placeholder.pack(pady=40)

        # ── 右栏：下载选项 + 日志 ─────────────────
        rf = ctk.CTkFrame(self)
        rf.grid(row=0, column=2, padx=(6,14), pady=14, sticky="nsew")
        rf.grid_columnconfigure(0, weight=1)
        rf.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(rf, text="下载选项",
                     font=ctk.CTkFont(size=14, weight="bold")
                     ).grid(row=0, column=0, padx=12, pady=(12,6), sticky="w")

        of = ctk.CTkFrame(rf, fg_color="transparent")
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
            rf, text="▶   开始下载", height=44,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._start_download)
        self.dl_btn.grid(row=2, column=0, padx=12, pady=(4,8), sticky="ew")

        ctk.CTkLabel(rf, text="下载日志",
                     font=ctk.CTkFont(size=13, weight="bold")
                     ).grid(row=3, column=0, padx=12, pady=(0,4), sticky="w")
        self.log_box = ctk.CTkTextbox(
            rf, font=ctk.CTkFont(family="Courier New", size=11),
            wrap="word", state="disabled")
        self.log_box.grid(row=4, column=0, padx=12, pady=(0,12), sticky="nsew")

    # ── 频道加载 ─────────────────────────────────────────

    def _bg_load_channels(self):
        chs = get_channels()
        self.after(0, lambda: self._populate_channels(chs))

    def _populate_channels(self, channels):
        self.channels = channels
        if self.ch_loading: self.ch_loading.destroy()
        for ch in channels:
            btn = ctk.CTkButton(
                self.ch_scroll, text=ch["title"], anchor="w",
                fg_color="transparent", hover_color="#2d3447",
                text_color=("gray10","gray90"),
                font=ctk.CTkFont(size=13),
                command=lambda c=ch: self._click_channel(c))
            btn.pack(fill="x", padx=4, pady=2)
            self.ch_btns.append(btn)
        self._log(f"✓ {len(channels)} 个频道，点左边选一个\n")

    # ── 导航：频道 → 剧名 ────────────────────────────────

    def _click_channel(self, channel):
        if self.downloading: return
        self.cur_channel = channel
        self.cur_show    = None
        # 高亮频道按钮
        for b in self.ch_btns: b.configure(fg_color="transparent")
        for b in self.ch_btns:
            if b.cget("text") == channel["title"]:
                b.configure(fg_color="#1e3a5f"); break

        self.mid_title.configure(text=f"📺  {channel['title']}")
        self.back_btn.grid_remove()
        self.ep_btn_row.grid_remove()
        self._clear_mid("⏳  加载剧名列表…")
        self._log(f"\n加载「{channel['title']}」…\n")

        def run():
            shows = get_shows(channel["url"])
            self.after(0, lambda: self._show_shows(shows))
        threading.Thread(target=run, daemon=True).start()

    def _show_shows(self, shows):
        self.shows = shows
        self.show_btns = []
        self._clear_mid(None)
        if not shows:
            ctk.CTkLabel(self.mid_scroll, text="暂无内容", text_color="gray").pack(pady=30)
            return
        for show in shows:
            btn = ctk.CTkButton(
                self.mid_scroll, text=show["title"], anchor="w",
                fg_color="transparent", hover_color="#2d3447",
                text_color=("gray10","gray90"),
                font=ctk.CTkFont(size=13),
                command=lambda s=show: self._click_show(s))
            btn.pack(fill="x", padx=4, pady=2)
            self.show_btns.append(btn)
        self._log(f"  ✓ 找到 {len(shows)} 部剧，点剧名查看剧集\n")

    # ── 导航：剧名 → 选集 ────────────────────────────────

    def _click_show(self, show):
        if self.downloading: return
        self.cur_show = show
        # 高亮剧名按钮
        for b in self.show_btns: b.configure(fg_color="transparent")
        for b in self.show_btns:
            if b.cget("text") == show["title"]:
                b.configure(fg_color="#1e3a5f"); break

        ch_name = self.cur_channel["title"] if self.cur_channel else ""
        self.mid_title.configure(
            text=f"{ch_name}  ›  {show['title'][:30]}")
        self.back_btn.grid()
        self.ep_btn_row.grid()
        self._clear_mid("⏳  加载剧集…")
        self._log(f"\n加载「{show['title']}」剧集…\n")

        def run():
            eps = get_episodes(show)
            self.after(0, lambda: self._show_episodes(eps))
        threading.Thread(target=run, daemon=True).start()

    def _show_episodes(self, episodes):
        self.episodes = episodes
        self.ep_vars  = []
        self._clear_mid(None)
        if not episodes:
            ctk.CTkLabel(self.mid_scroll,
                         text="⚠ 暂未找到可下载的剧集链接",
                         text_color="gray").pack(pady=30)
            self._log("  ⚠ 暂无可解析的视频链接\n")
            return
        for ep in episodes:
            var     = ctk.BooleanVar(value=False)
            has_url = bool(ep.get("url"))
            cb = ctk.CTkCheckBox(
                self.mid_scroll,
                text=ep["title"],
                variable=var,
                font=ctk.CTkFont(size=12),
                text_color=("gray10","gray90") if has_url else ("gray40","gray50"),
                state="normal" if has_url else "disabled")
            cb.pack(anchor="w", padx=10, pady=3, fill="x")
            self.ep_vars.append(var)
        avail = sum(1 for ep in episodes if ep.get("url"))
        self._log(f"  ✓ 共 {len(episodes)} 集，{avail} 集可下载，勾选后点开始下载\n")

    def _go_back(self):
        """返回剧名列表"""
        if self.cur_channel:
            self._show_shows(self.shows)
            self.mid_title.configure(
                text=f"📺  {self.cur_channel['title']}")
            self.back_btn.grid_remove()
            self.ep_btn_row.grid_remove()

    # ── 中栏工具 ─────────────────────────────────────────

    def _clear_mid(self, placeholder_text):
        for w in self.mid_scroll.winfo_children(): w.destroy()
        self.ep_vars = []; self.episodes = []
        if placeholder_text:
            ctk.CTkLabel(self.mid_scroll,
                         text=placeholder_text, text_color="gray").pack(pady=40)

    def _ep_select_all(self):
        for v in self.ep_vars: v.set(True)

    def _ep_clear_all(self):
        for v in self.ep_vars: v.set(False)

    # ── 下载 ─────────────────────────────────────────────

    def _choose_folder(self):
        d = filedialog.askdirectory(title="选择保存目录")
        if d: self.folder_var.set(d)

    def _start_download(self):
        if self.downloading: return
        if not self.cur_show:
            messagebox.showwarning("提示", "请先点左边频道 → 点剧名 → 勾选剧集")
            return
        selected = [(self.episodes[i], v)
                    for i, v in enumerate(self.ep_vars) if v.get()]
        if not selected:
            messagebox.showwarning("提示", "请勾选要下载的集数")
            return

        show_name = self.cur_show["title"]
        out_dir   = Path(self.folder_var.get())
        save_dir  = out_dir / (self.cur_channel["title"] if self.cur_channel else "") / show_name
        save_dir.mkdir(parents=True, exist_ok=True)

        self.downloading = True
        self.dl_btn.configure(text="⏳  下载中…", state="disabled")
        self.log_q.put(f"\n{'━'*38}\n下载「{show_name}」，共 {len(selected)} 集\n")

        def run():
            for ep, _ in selected:
                if not ep.get("url"):
                    self.log_q.put(f"  跳过（无链接）：{ep['title'][:40]}\n"); continue
                safe = "".join(c for c in ep["title"]
                               if c not in r'\/:*?"<>|').strip()[:80]
                self.log_q.put(f"  ▶ {ep['title'][:55]}\n")
                ok = download_video(ep["url"],
                                    str(save_dir / f"{safe}.%(ext)s"),
                                    self.log_q.put)
                if ok: self.log_q.put("  ✓ 完成\n")
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
        except queue.Empty: pass
        self.after(100, self._poll_log)


if __name__ == "__main__":
    App().mainloop()
