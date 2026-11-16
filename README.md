# JM 下载器

基于 [JMComic-Crawler-Python](https://github.com/hect0x7/JMComic-Crawler-Python) 的图形界面下载工具（tkinter）。

## 环境准备

需要 **Python 3.10+**

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -U pip
pip install -r requirements.txt
```



启动

```bash
python main.py
```

Windows 也可以直接双击 `run.bat`

## 使用

界面分三个标签页：

- **下载**：输入车号 → 查询详情 → 开始下载
- **下载记录**：历史记录，双击可回填车号
- **设置**：保存目录、并发数、代理、账号密码、导出选项、界面字号

### 车号输入格式

```
350234                              单个本子
350234 438516                       多个本子
p350235                             单个章节
JM350234                            带前缀也可
https://18comic.vip/album/350234/   整条链接也可
```

多个用空格或逗号分隔，自动去重。

### 下载产物

```
downloads/
└── 350234-本子标题/
    ├── 350234.pdf          ← 开启「合并 PDF」时生成
    └── images/
        └── 章节名/
            ├── 00001.webp
            └── 00002.webp
```



---



启动时会并发测速，挑出最快的 API 域名并缓存到 `.runtime/domain_cache.json`。缓存永久有效，域名失效时删掉该文件即可重新测速。

**偶尔有图片下载失败？**
图床 CDN 节点不稳定。把 `设置 → 请求策略 → 重试` 调高（默认 4）。失败的图片在重新下载同一本子时会自动补上（已下载的会跳过）。



账号密码保存在本目录的 `config.json`，该文件已加入 `.gitignore`。大部分本子不需要登录。

---

## 单独合并 PDF

```bash
python src/merge_pdf.py 350234 438516
```

默认在 `downloads/` 下找车号目录，也可直接传绝对路径。

## 文件结构

```
jm-downloader/
├── main.py            入口
├── run.bat            Windows 启动脚本
├── requirements.txt
├── src/
│   ├── ui.py          界面
│   ├── core.py        下载逻辑
│   ├── fastnet.py     域名测速
│   ├── settings.py    配置读写
│   ├── logbook.py     日志与记录
│   └── merge_pdf.py   独立合并 PDF 工具
├── config.json        用户设置
└── .runtime/          日志、缓存、临时文件
```

## 免责声明

仅供个人学习与技术研究使用。请遵守当地法律法规，不要用于任何商业用途或大规模抓取。
