"""
allrss 视频下载器 v4
修复：下载无反应 / 增加详细日志 / 错误不再静默
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
    except Exception as e:
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
    """提取真正的视频 URL，明确跳过 RSS/XML 链接"""
    VIDEO_EXTS = (".m3u8", ".mp4", ".mkv", ".ts", ".avi", ".flv", ".wmv", ".mov")
    SKIP_TYPES = ("application/rss+xml", "text/xml", "application/xml",
                  "application/atom+xml")
    SKIP_EXTS  = (".xml", ".rss", ".atom")

    all_links = list(entry.get("links", [])) + list(entry.get("enclosures", []))
    for link in all_links:
        href = (link.get("href") or link.get("url") or "").strip()
        mime = link.get("type", "").lower()
        if not href: continue
        if mime in SKIP_TYPES: continue
        if any(href.lower().endswith(x) for x in SKIP_EXTS): continue
        if any(href.lower().endswith(x) for x in VIDEO_EXTS): return href
        if "video" in mime or "mpegurl" in mime or "octet" in mime: return href

    # HTML 内容里找
    raw = entry.get("summary","") + "".join(
        c.get("value","") for c in entry.get("content",[]))
    for kw in ['file="',"file='", 'src="',"src='",
               'url="',"url='", 'source src="',"source src='"]:
        idx = content_find(raw, kw)
        if idx == -1: continue
        start = idx + len(kw)
        q = kw[-1]
        end = raw.find(q, start)
        if end > start:
            u = raw[start:end].strip()
            if (u.startswith("http") and
                    not any(u.lower().endswith(x) for x in SKIP_EXTS)):
                return u
    return None

def content_find(text, kw):
    return text.find(kw)

def _extract_sub_rss(entry):
    all_links = list(entry.get("links",[])) + list(entry.get("enclosures",[]))
    for link in all_links:
        href = (link.get("href") or link.get("url") or "").strip()
        mime = link.get("type","").lower()
        if "rss" in mime or "xml" in mime: return href
        if href.lower().endswith((".xml",".rss")): return href
    return None

def get_shows(channel_url):
    feed = fetch_rss(channel_url)
    if not feed: return []
    shows = []
    for e in feed.entries:
        shows.append({
            "title":       e.get("title","unknown"),
            "sub_rss":     _extract_sub_rss(e),
            "direct_url":  _extract_video_url(e),
        })
    return shows

def get_episodes(show):
    if show.get("sub_rss"):
        feed = fetch_rss(show["sub_rss"])
        if feed and feed.entries:
            eps = []
            for e in feed.entries:
                url = _extract_video_url(e)
                eps.append({"title": e.get("title","unknown"), "url": url})
            if any(ep["url"] for ep in eps):
                return eps
    if show.get("direct_url"):
        return [{"title": show["title"], "url": show["direct_url"]}]
    return []

def download_video(url, save_path, log_fn):
    """下载单个视频，输出详细日志"""
    log_fn(f"    链接：{url[:80]}\n")
    log_fn(f"    正在解析…\n")

    class YLogger:
        def debug(self, m):
            m = m.strip()
            if not m or m.startswith("[debug]"): return
            log_fn(f"    {m}\n")
        def warning(self, m):
            if m.strip(): log_fn(f"    ⚠ {m.strip()}\n")
        def error(self, m):
            if m.strip(): log_fn(f"  ✗ {m.strip()}\n")

    opts = {
        "outtmpl":            save_path,
        "retries":            3,
        "fragment_retries":   3,
        "noplaylist":         True,
        "logger":             YLogger(),
        "quiet":              False,
        "no_warnings":        False,
        "socket_timeout":     30,
        "http_headers":       HEADERS,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        return True
    except yt_dlp.utils.DownloadError as e:
        log_fn(f"  ✗ 下载失败：{e}\n")
    except Exception as e:
        log_fn(f"  ✗ 错误：{type(e).__name__}: {e}\n")
    return False

# ── 主界面 ──────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("allrss 视频下载器")
        self.geometry("1200x720")
        self.minsize(900, 560)

        self.channels    = []
        self.ch_btns     = []
        self.cur_channel = None
        self.cur_show    = None
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

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=2)
        self.grid_columnconfigure(1, weight=3)
        self.grid_columnconfigure(2, weight=3)
        self.grid_rowconfigure(0, weight=1)

        # ── 左栏 ────────────────────────────────
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

        # ── 中栏 ────────────────────────────────
        mf = ctk.CTkFrame(self)
        mf.grid(row=0, column=1, padx=6, pady=14, sticky="nsew")
        mf.grid_columnconfigure(0, weight=1)
        mf.grid_rowconfigure(2, weight=1)

        nav_row = ctk.CTkFrame(mf, fg_color="transparent")
        nav_row.grid(row=0, column=0, padx=12, pady=(12,0), sticky="ew")
        nav_row.grid_columnconfigure(1, weight=1)

        self.back_btn = ctk.CTkButton(
            nav_row, text="◀ 返回", width=72, height=28,
            fg_color="#374151", hover_color="#4b5563",
            command=self._go_back)
        self.back_btn.grid(row=0, column=0, padx=(0,8))
        self.back_btn.grid_remove()

        self.mid_title = ctk.CTkLabel(
            nav_row, text="← 先点左边选频道",
            font=ctk.CTkFont(size=14, weight="bold"), anchor="w")
        self.mid_title.grid(row=0, column=1, sticky="w")

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
        ctk.CTkLabel(self.mid_scroll, text="← 先点左边选频道",
                     text_color="gray").pack(pady=40)

        # ── 右栏 ────────────────────────────────
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
                     font=ctk.CTkFont(size=12)
                     ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0,4))
        self.folder_var = ctk.StringVar(
            value=str(Path.home() / "Downloads" / "dramas"))
        ctk.CTkEntry(of, textvariable=self.folder_var
                     ).grid(row=1, column=0, sticky="ew", padx=(0,8))
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

    # ── 频道 ─────────────────────────────────────────────

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
                text_color=("gray10","gray90"), font=ctk.CTkFont(size=13),
                command=lambda c=ch: self._click_channel(c))
            btn.pack(fill="x", padx=4, pady=2)
            self.ch_btns.append(btn)
        self._log(f"✓ {len(channels)} 个频道，点左边选一个\n")

    def _click_channel(self, channel):
        if self.downloading: return
        self.cur_channel = channel
        self.cur_show = None
        for b in self.ch_btns: b.configure(fg_color="transparent")
        for b in self.ch_btns:
            if b.cget("text") == channel["title"]:
                b.configure(fg_color="#1e3a5f"); break
        self.mid_title.configure(text=f"📺  {channel['title']}")
        self.back_btn.grid_remove()
        self.ep_btn_row.grid_remove()
        self._clear_mid("⏳  加载剧名…")
        self._log(f"\n加载「{channel['title']}」剧名…\n")
        threading.Thread(target=lambda: self.after(
            0, lambda: None) or self._fetch_shows(channel), daemon=True).start()

    def _fetch_shows(self, channel):
        shows = get_shows(channel["url"])
        self.after(0, lambda: self._show_shows(shows))

    def _show_shows(self, shows):
        self.shows = shows
        self.show_btns = []
        self._clear_mid(None)
        if not shows:
            ctk.CTkLabel(self.mid_scroll, text="暂无内容",
                         text_color="gray").pack(pady=30)
            return
        for show in shows:
            btn = ctk.CTkButton(
                self.mid_scroll, text=show["title"], anchor="w",
                fg_color="transparent", hover_color="#2d3447",
                text_color=("gray10","gray90"), font=ctk.CTkFont(size=13),
                command=lambda s=show: self._click_show(s))
            btn.pack(fill="x", padx=4, pady=2)
            self.show_btns.append(btn)
        self._log(f"  ✓ {len(shows)} 部剧，点剧名查看集数\n")

    # ── 剧集 ─────────────────────────────────────────────

    def _click_show(self, show):
        if self.downloading: return
        self.cur_show = show
        for b in self.show_btns: b.configure(fg_color="transparent")
        for b in self.show_btns:
            if b.cget("text") == show["title"]:
                b.configure(fg_color="#1e3a5f"); break
        ch = self.cur_channel["title"] if self.cur_channel else ""
        short_title = show["title"][:28] + ("…" if len(show["title"]) > 28 else "")
        self.mid_title.configure(text=f"{ch}  ›  {short_title}")
        self.back_btn.grid()
        self.ep_btn_row.grid()
        self._clear_mid("⏳  加载集数…")
        self._log(f"\n加载「{show['title']}」集数…\n")
        threading.Thread(
            target=lambda: self.after(0, lambda: None) or self._fetch_eps(show),
            daemon=True).start()

    def _fetch_eps(self, show):
        eps = get_episodes(show)
        self.after(0, lambda: self._show_episodes(eps))

    def _show_episodes(self, episodes):
        self.episodes = episodes
        self.ep_vars  = []
        self._clear_mid(None)
        if not episodes:
            ctk.CTkLabel(self.mid_scroll,
                         text="⚠ 暂无可下载的集数",
                         text_color="gray").pack(pady=30)
            self._log("  ⚠ 暂无视频链接\n")
            return
        for ep in episodes:
            var     = ctk.BooleanVar(value=False)
            has_url = bool(ep.get("url"))
            cb = ctk.CTkCheckBox(
                self.mid_scroll, text=ep["title"], variable=var,
                font=ctk.CTkFont(size=12),
                text_color=("gray10","gray90") if has_url else ("gray40","gray50"),
                state="normal" if has_url else "disabled")
            cb.pack(anchor="w", padx=10, pady=3, fill="x")
            self.ep_vars.append(var)
        avail = sum(1 for ep in episodes if ep.get("url"))
        self._log(f"  ✓ 共 {len(episodes)} 集，{avail} 集可下载\n")

    def _go_back(self):
        if self.cur_channel:
            self._show_shows(self.shows)
            self.mid_title.configure(text=f"📺  {self.cur_channel['title']}")
            self.back_btn.grid_remove()
            self.ep_btn_row.grid_remove()

    def _clear_mid(self, placeholder):
        for w in self.mid_scroll.winfo_children(): w.destroy()
        self.ep_vars = []; self.episodes = []
        if placeholder:
            ctk.CTkLabel(self.mid_scroll, text=placeholder,
                         text_color="gray").pack(pady=40)

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
            messagebox.showinfo("提示", "下载中，请等待当前任务完成")
            return
        if not self.cur_show:
            messagebox.showwarning("提示", "请先选频道 → 点剧名 → 勾选集数")
            return

        # 收集勾选的集数
        selected = []
        for i, var in enumerate(self.ep_vars):
            try:
                if var.get():
                    selected.append(self.episodes[i])
            except Exception:
                pass

        if not selected:
            messagebox.showwarning("提示", "请勾选要下载的集数（打勾）")
            return

        # 创建保存目录
        ch_name   = self.cur_channel["title"] if self.cur_channel else ""
        show_name = self.cur_show["title"]
        # 清理目录名中的非法字符
        safe_ch   = "".join(c for c in ch_name   if c not in r'\/:*?"<>|').strip()
        safe_show = "".join(c for c in show_name if c not in r'\/:*?"<>|').strip()
        out_dir   = Path(self.folder_var.get())
        save_dir  = out_dir / safe_ch / safe_show

        try:
            save_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("无法创建目录", str(e))
            return

        self.downloading = True
        self.dl_btn.configure(text="⏳  下载中…", state="disabled")

        self._log(f"\n{'━'*36}\n开始下载「{show_name}」\n"
                  f"共 {len(selected)} 集 → {save_dir}\n")

        ep_list = list(selected)   # 快照，防止 UI 刷新影响

        def run():
            for ep in ep_list:
                url   = ep.get("url")
                title = ep.get("title", "unknown")
                if not url:
                    self.log_q.put(f"  跳过（无链接）：{title[:40]}\n")
                    continue
                safe_t = "".join(c for c in title
                                 if c not in r'\/:*?"<>|').strip()[:60]
                out_path = str(save_dir / f"{safe_t}.%(ext)s")
                self.log_q.put(f"\n  ▶ {title[:50]}\n")
                ok = download_video(url, out_path, self.log_q.put)
                self.log_q.put("  ✓ 完成\n" if ok else "  ✗ 此集下载失败\n")
                time.sleep(0.5)

            self.log_q.put(f"\n✅ 完成！文件在：{save_dir.resolve()}\n")
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
        self.after(80, self._poll_log)


if __name__ == "__main__":
    App().mainloop()
