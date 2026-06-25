"""
一键启动 — 双击这个文件或运行 python start.py
会自动：1) 安装依赖  2) 打开浏览器  3) 启动下载器
"""
import subprocess
import sys
import time
import webbrowser
import threading
from pathlib import Path

URL = "http://localhost:5000"

def install_deps():
    req = Path(__file__).parent / "requirements.txt"
    print("正在检查依赖...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(req), "-q"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print("依赖安装失败：")
        print(result.stderr)
        input("按 Enter 退出...")
        sys.exit(1)
    print("依赖已就绪 ✓")

def open_browser():
    time.sleep(2)
    webbrowser.open(URL)

def start_server():
    app_path = Path(__file__).parent / "app.py"
    subprocess.run([sys.executable, str(app_path)])

if __name__ == "__main__":
    print("=" * 40)
    print("  allrss 视频下载器")
    print("=" * 40)

    install_deps()

    print(f"\n启动服务器... 浏览器即将自动打开")
    print(f"如未自动打开，请手动访问: {URL}")
    print("关闭此窗口 = 停止下载器\n")

    threading.Thread(target=open_browser, daemon=True).start()
    start_server()
