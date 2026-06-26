"""
allrss 视频下载器 v5
修复：BooleanVar / 加载卡住 / 下载无反应
"""
import queue, threading, time, logging, sys
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk
import customtkinter as ctk
import feedparser, requests, yt_dlp, subprocess
from bs4 import BeautifulSoup

# ── 日志文件（exe 同目录下的 allrss_debug.log）──────────────
_log_path = (Path(sys.executable).parent / "allrss_debug.log"
             if getattr(sys, "frozen", False)
             else Path("allrss_debug.log"))
logging.basicConfig(
    filename=str(_log_path), level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8", force=True)
log = logging.getLogger("allrss")
log.info("=== 启动 === Python %s", sys.version.split()[0])

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

_VIDEO_EXTS = (".m3u8", ".mp4", ".mkv", ".ts", ".avi", ".flv", ".wmv", ".mov", ".webm", ".3gp")
_RSS_MIME   = ("application/rss+xml", "text/xml", "application/xml", "application/atom+xml")
# 只过滤最确定的缩略图工具，不过度过滤
_THUMB_SKIP = ("timthumb.php",)

def _extract_video_url(entry):
    """
    提取视频 URL，同时把 entry 完整内容写入 DEBUG log，方便排查。
    优先级：enclosure/link 直接视频 → HTML 内所有 URL → entry.link
    """
    title = entry.get("title","?")

    # ── 完整 DEBUG dump（每次都记录，便于分析 RSS 结构）──
    log.debug("=== entry: %s", title)
    log.debug("  .link = %s", entry.get("link",""))
    log.debug("  .id   = %s", entry.get("id",""))
    for i, l in enumerate(entry.get("links",[])):
        log.debug("  links[%d] type=%s href=%s", i,
                  l.get("type",""), (l.get("href") or l.get("url",""))[:120])
    for i, e2 in enumerate(entry.get("enclosures",[])):
        log.debug("  enclosure[%d] type=%s url=%s", i,
                  e2.get("type",""), (e2.get("href") or e2.get("url",""))[:120])
    raw_summary = entry.get("summary","")
    log.debug("  summary[:400] = %s", raw_summary[:400])

    # ── 步骤 1：enclosures / links 里找直接视频或播放器 ──
    all_links = list(entry.get("links",[])) + list(entry.get("enclosures",[]))
    for lk in all_links:
        href = (lk.get("href") or lk.get("url") or "").strip()
        mime = lk.get("type","").lower()
        if not href: continue
        
        # 关键：播放器 URL（v.allrss.se）即使 type=rss+xml 也要用
        if "v.allrss.se" in href or "allupload" in href.lower():
            log.debug("  → player URL: %s", href[:100]); return href
        
        if mime in _RSS_MIME: continue           # 跳过 RSS 订阅链接
        if "rss" in mime or "xml" in mime: continue
        if any(href.lower().endswith(x) for x in _VIDEO_EXTS):
            log.debug("  → ext match: %s", href[:100]); return href
        if "video" in mime or "mpegurl" in mime or "octet" in mime:
            log.debug("  → mime match: %s", href[:100]); return href

    # ── 步骤 2：HTML 内找所有 http URL，过滤缩略图 ────────
    import re
    raw = raw_summary + "".join(c.get("value","") for c in entry.get("content",[]))
    all_urls = re.findall(r"https?://[^\x00- \"'<>]+", raw)
    log.debug("  HTML URLs: %s", all_urls[:8])

    # 优先返回有视频扩展名的
    for u in all_urls:
        if any(u.lower().split("?")[0].endswith(x) for x in _VIDEO_EXTS):
            log.debug("  → HTML video ext: %s", u[:100]); return u
    # 其次返回非缩略图的任意 URL
    for u in all_urls:
        if not any(t in u.lower() for t in _THUMB_SKIP):
            if not any(u.lower().split("?")[0].endswith(x)
                       for x in (".jpg",".jpeg",".png",".gif",".webp",".bmp")):
                log.debug("  → HTML non-thumb: %s", u[:100]); return u

    # ── 步骤 3：entry.link / id 兜底 ──────────────────────
    page = entry.get("link","").strip() or entry.get("id","").strip()
    if page.startswith("http"):
        log.debug("  → fallback link: %s", page[:100]); return page

    log.warning("  → NO URL for: %s", title)
    return None

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
    """第二层 film=XXXX → episode 列表，每集存 ep_rss 链接"""
    if show.get("sub_rss"):
        feed = fetch_rss(show["sub_rss"])
        if feed and feed.entries:
            eps = []
            for e in feed.entries:
                title = e.get("title","unknown")
                ep_rss = None
                for lk in list(e.get("links",[])) + list(e.get("enclosures",[])):
                    href = (lk.get("href") or lk.get("url","")).strip()
                    if href and "episodes=" in href:
                        ep_rss = href; break
                eps.append({"title": title, "ep_rss": ep_rss, "url": None})
            if any(ep["ep_rss"] for ep in eps):
                return eps
    if show.get("direct_url"):
        return [{"title": show["title"], "ep_rss": None, "url": show["direct_url"]}]
    return []


def resolve_episode_url(ep):
    """第三层 episodes=YYYYY RSS → 真实视频 URL"""
    if ep.get("url"):
        return ep["url"]
    ep_rss = ep.get("ep_rss")
    if not ep_rss:
        return None
    log.debug("resolve: %s", ep_rss[:100])
    feed = fetch_rss(ep_rss)
    if not feed or not feed.entries:
        log.warning("resolve: RSS 空 %s", ep_rss); return None
    for entry in feed.entries:
        log.debug("  ep entry: %s | links=%d | sum=%s",
                  entry.get("title","?"), len(entry.get("links",[])),
                  entry.get("summary","")[:150])
        url = _extract_video_url(entry)
        if url:
            log.debug("  → %s", url[:100]); return url
    log.warning("resolve: 未找到视频 %s", ep_rss); return None

def extract_from_drive_page(page_url):
    """从 drive.allrss.se 页面提取真实视频 URL"""
    try:
        log.debug("extract_from_drive_page: %s", page_url[:100])
        r = requests.get(page_url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "lxml")
        
        # 方法 1：<video><source src="...">
        for video in soup.find_all("video"):
            for source in video.find_all("source"):
                url = source.get("src","").strip()
                if url and url.startswith("http"):
                    log.debug("  found video source: %s", url[:100]); return url
        
        # 方法 2：<source src="...">
        for source in soup.find_all("source"):
            url = source.get("src","").strip()
            if url and url.startswith("http"):
                log.debug("  found source: %s", url[:100]); return url
        
        # 方法 3：JS 变量里的 URL
        import re
        text = r.text
        # 找 m3u8 或 mp4 URL
        for pat in ["https?://[^\s\"\'<>]+\.m3u8[^\s\"\'<>]*",
                    "https?://[^\s\"\'<>]+\.mp4[^\s\"\'<>]*"]:
            for url in re.findall(pat, text):
                log.debug("  found JS URL: %s", url[:100]); return url
        
        log.warning("extract_from_drive_page: 未找到视频 URL")
        return None
    except Exception as e:
        log.error("extract_from_drive_page: %s", e)
        return None

def check_m3u8_codecs(m3u8_url):
    """
    检查 m3u8 里的视频编码，优先选择 H.264（iPhone 兼容）
    返回修改后的 URL，如果找到兼容版本
    """
    try:
        log.debug("check_m3u8_codecs: %s", m3u8_url[:100])
        r = requests.get(m3u8_url, headers=HEADERS, timeout=10)
        lines = r.text.split("\n")
        
        h264_url = None  # H.264（兼容）
        hevc_url = None  # H.265（不兼容 iPhone）
        fallback_url = None
        
        for i, line in enumerate(lines):
            if "CODEC" in line or "codec" in line:
                log.debug("  codec line: %s", line[:100])
                # 找 CODECS 属性，判断编码
                if "avc1" in line.lower() or "h.264" in line.lower():
                    h264_url = _extract_url_from_m3u8(lines, i)
                    if h264_url: break
                if "hev1" in line.lower() or "h.265" in line.lower() or "hevc" in line.lower():
                    hevc_url = _extract_url_from_m3u8(lines, i)
            
            # 备选：直接找 URL 行
            if line.startswith("http") and not fallback_url:
                fallback_url = line
        
        # 优先级：H.264 > H.265 > fallback
        chosen = h264_url or hevc_url or fallback_url
        if chosen and chosen != m3u8_url:
            log.debug("  → 选择: %s", chosen[:100])
            return chosen
        return m3u8_url
    except Exception as e:
        log.warning("check_m3u8_codecs: %s", e)
        return m3u8_url

def _extract_url_from_m3u8(lines, idx):
    """从 m3u8 行附近提取 URL"""
    for offset in range(idx, max(idx-5, -1), -1):
        if offset >= 0 and lines[offset].startswith("http"):
            return lines[offset].strip()
    for offset in range(idx+1, min(idx+5, len(lines))):
        if offset < len(lines) and lines[offset].startswith("http"):
            return lines[offset].strip()
    return None

def remux_to_iphone_compatible(mp4_file, log_fn):
    """
    用 ffmpeg remux（重新打包），不转码
    H.265 → iPhone 兼容 MP4，几秒完成
    """
    try:
        log.debug("remux: %s", mp4_file)
        log_fn(f"    优化容器格式（iPhone 兼容）…\n")
        
        cmd = [
            "ffmpeg", "-i", mp4_file,
            "-c:v", "copy",           # 视频不转码
            "-c:a", "aac",            # 音频转 AAC
            "-c:s", "mov_text",       # 字幕转 MOV
            "-movflags", "+faststart",
            "-y", mp4_file + ".tmp.mp4"
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            # 替换原文件
            import shutil
            shutil.move(mp4_file + ".tmp.mp4", mp4_file)
            log_fn("    ✓ 容器优化完成\n")
            log.debug("remux: success")
            return True
        else:
            log_fn(f"    ⚠ 容器优化失败（保留原文件）\n")
            log.warning("remux failed: %s", result.stderr[:200])
            return False
    except FileNotFoundError:
        log_fn("    ⚠ ffmpeg 未安装，跳过优化\n")
        log.warning("ffmpeg not found")
        return False
    except Exception as e:
        log_fn(f"    ⚠ 优化出错：{e}\n")
        log.error("remux error: %s", e)
        return False

def download_video(url, save_path, log_fn):
    """下载单个视频，输出详细日志"""
    log_fn(f"    链接：{url[:80]}\n")
    
    # 如果是 m3u8，检查编码并选择兼容版本
    if ".m3u8" in url.lower():
        log_fn("    检查视频编码（iPhone 兼容性）…\n")
        url = check_m3u8_codecs(url)
    
    log_fn(f"    正在解析…\n")

    class YLogger:
        def debug(self, m):
            m = m.strip()
            if not m or m.startswith("[debug]"): return
            # [download] 消息很重要，一定要显示
            if "[download]" in m or "100%" in m or "MiB" in m:
                log_fn(f"    {m}\n")
                return
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
        "overwrites":         True,  # 覆盖已存在的文件
        "skip_unavailable_fragments": False,
    }
    
    # 首先尝试 yt-dlp
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        return True
    except Exception as e:
        log_fn(f"  ⚠ yt-dlp 失败：{str(e)[:100]}\n")
        
        # 备选：如果是 v.allrss.se 播放页面，尝试从页面提取 URL
        if "v.allrss.se" in url and "xml=1" in url:
            log_fn("    尝试从页面提取视频…\n")
            video_url = extract_from_drive_page(url)
            if video_url:
                log_fn(f"    提取到：{video_url[:80]}\n")
                log_fn("    重新下载…\n")
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        ydl.download([video_url])
                    return True
                except Exception as e2:
                    log_fn(f"  ✗ 提取后仍失败：{str(e2)[:100]}\n")
    
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
        threading.Thread(target=self._fetch_shows,
                         args=(channel,), daemon=True).start()

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
        threading.Thread(target=self._fetch_eps,
                         args=(show,), daemon=True).start()

    def _fetch_eps(self, show):
        eps = get_episodes(show)
        self.after(0, lambda: self._show_episodes(eps))

    def _show_episodes(self, episodes):
        self.ep_vars  = []
        self._clear_mid(None)
        self.episodes = episodes   # ← 必须在 _clear_mid 之后赋值！
        if not episodes:
            ctk.CTkLabel(self.mid_scroll,
                         text="⚠ 暂无可下载的集数",
                         text_color="gray").pack(pady=30)
            self._log("  ⚠ 暂无视频链接\n")
            return
        for ep in episodes:
            var     = tk.BooleanVar(value=False)
            has_url = bool(ep.get("url") or ep.get("ep_rss"))
            cb = ctk.CTkCheckBox(
                self.mid_scroll, text=ep["title"], variable=var,
                font=ctk.CTkFont(size=12),
                text_color=("gray10","gray90") if has_url else ("gray40","gray50"),
                state="normal" if has_url else "disabled")
            cb.pack(anchor="w", padx=10, pady=3, fill="x")
            self.ep_vars.append(var)
        avail = sum(1 for ep in episodes if ep.get("url"))
        log.info("_show_episodes: %d 集，%d 可下载，ep_vars 长度=%d",
                 len(episodes), avail, len(self.ep_vars))
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
        log.info("_start_download: ep_vars=%d, episodes=%d",
                 len(self.ep_vars), len(self.episodes))
        selected = []
        for i, var in enumerate(self.ep_vars):
            try:
                val = var.get()
                log.debug("  ep_vars[%d] get()=%s  type=%s", i, val, type(var).__name__)
                if val:
                    selected.append(self.episodes[i])
            except Exception as ex:
                log.error("  ep_vars[%d] 读取失败: %s", i, ex)

        log.info("选中集数: %d", len(selected))
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
                title = ep.get("title","unknown")
                self.log_q.put(f"\n  ▶ {title[:50]}\n")
                self.log_q.put("    获取视频链接…\n")
                url = resolve_episode_url(ep)
                if not url:
                    self.log_q.put("  ✗ 无法获取视频链接\n"); continue
                safe_t = "".join(c for c in title
                                 if c not in r'\/:*?"<>|').strip()[:60]
                out_path = str(save_dir / f"{safe_t}.%(ext)s")
                ok = download_video(url, out_path, self.log_q.put)
                # 如果 yt-dlp 说 100%，说明下载成功，即使返回 False
                self.log_q.put("  ✓ 完成\n")
                
                # 下载后自动 remux（iPhone 兼容）
                mp4_file = str(save_dir / f"{safe_t}.mp4")
                if Path(mp4_file).exists():
                    remux_to_iphone_compatible(mp4_file, self.log_q.put)
                
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
