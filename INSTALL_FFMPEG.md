# 安装 FFmpeg

FFmpeg 是视频处理工具，本下载器用它来快速优化视频容器格式（remux）。

## Windows 用户

### 方法 1：下载绿色版（推荐，无需安装）

1. 打开浏览器，访问：https://ffmpeg.org/download.html
2. 点击 **Windows builds by BtbN**（或类似的 Windows 预编译版本）
3. 下载最新的 **ffmpeg-master-latest-win64-gpl.zip**（约 100MB）
4. 解压到任意位置，比如 `C:\ffmpeg\`
5. 把 `C:\ffmpeg\bin` **添加到系统 PATH**：
   - 右键「此电脑」→ 属性
   - 点「高级系统设置」
   - 点「环境变量」
   - 在「系统变量」中找 `Path`，点「编辑」
   - 点「新建」，输入 `C:\ffmpeg\bin`（改成你的解压路径）
   - 一路点「确定」保存
6. **重启电脑**（或重启下载器应用）
7. 打开命令提示符（Win+R → cmd），输入 `ffmpeg -version`，如果显示版本号说明成功

### 方法 2：用 Chocolatey（一键安装）

如果你电脑上装了 Chocolatey：
```
choco install ffmpeg
```

如果没装，可以先装 Chocolatey（管理员模式打开 PowerShell）：
```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
```

然后再装 FFmpeg：
```
choco install ffmpeg
```

---

## Mac 用户

用 Homebrew：
```bash
brew install ffmpeg
```

如果没装 Homebrew，先装它：
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

---

## Linux 用户

### Ubuntu / Debian：
```bash
sudo apt update
sudo apt install ffmpeg
```

### Fedora：
```bash
sudo dnf install ffmpeg
```

### Arch：
```bash
sudo pacman -S ffmpeg
```

---

## 验证安装

安装完后，打开**命令提示符**（Windows）或**终端**（Mac/Linux），输入：
```
ffmpeg -version
```

如果显示版本号和信息，说明安装成功 ✓

---

## 常见问题

**Q: 我装了但下载器还是说"ffmpeg 未安装"**

A: 重启下载器或重启电脑，PATH 环境变量需要重新加载。

**Q: 我不想装 ffmpeg，可以不 remux 吗？**

A: 可以。不装 ffmpeg 的话，下载器会：
- 仍然正常下载视频到 Windows
- remux 步骤会跳过，显示"⚠ ffmpeg 未安装，跳过优化"
- 视频仍然能在 Windows 上播，但可能在 iPhone 上播不了

**Q: 绿色版 ffmpeg 去哪里下？**

A: 这些网站提供预编译的 ffmpeg：
- https://ffmpeg.org/download.html （官方，点 Windows builds）
- https://github.com/BtbN/FFmpeg-Builds/releases （直接下载）
- https://www.gyan.dev/ffmpeg/builds/ （另一个编译源）

推荐下载 **full** 版本（包含所有编码器），约 100-200MB。

