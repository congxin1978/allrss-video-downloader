# allrss-video-downloader

从 [allrss.se](https://allrss.se/dramas/) 自动爬取并下载亚洲剧集、动漫等视频。

支持频道：韩剧、港剧、中剧、台剧、日剧、动漫、美剧等。

---

## 安装

需要 Python 3.10+

```bash
git clone https://github.com/congxin1978/allrss-video-downloader.git
cd allrss-video-downloader
pip install -r requirements.txt
```

---

## 用法

### 查看所有可用频道

```bash
python downloader.py --list
```

输出示例：
```
   1. All Channel
   2. Recently
   3. HK Drama
   4. Korean Drama
   5. Chinese Drama
   ...
```

---

### 下载某个频道（推荐）

```bash
python downloader.py --channel "Korean Drama"
```

---

### 限制下载集数

```bash
# 只下载最新 3 集
python downloader.py --channel "Korean Drama" --max 3
```

---

### 指定保存目录

```bash
python downloader.py --channel "Anime #1" --out /Volumes/MyDisk/videos
```

---

### 下载全部频道（慎用，体积很大）

```bash
python downloader.py
```

---

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--channel` | 频道名称（支持模糊匹配） | 空（全部） |
| `--max` | 每频道最多下载集数，0=不限 | 0 |
| `--out` | 下载目录 | `./downloads` |
| `--list` | 列出所有频道，不下载 | — |

---

## 文件结构

```
downloads/
├── Korean Drama/
│   ├── 某某剧 EP01.mp4
│   └── 某某剧 EP02.mp4
├── HK Drama/
│   └── ...
└── Anime #1/
    └── ...
```

---

## 常见问题

**Q: 某频道显示"找到 0 个视频"？**  
A: 该子 RSS 的视频链接可能嵌在 iframe 或播放器 JS 里，需要额外解析。欢迎提 Issue。

**Q: 下载速度慢 / 失败？**  
A: yt-dlp 会自动重试 3 次。网络问题可尝试挂代理后运行。

**Q: 提示未找到 yt-dlp？**  
A: 运行 `pip install yt-dlp` 安装，或 `pip install --upgrade yt-dlp` 更新。

---

## License

MIT
