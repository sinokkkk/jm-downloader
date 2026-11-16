"""把 downloads/<车号>/<章节>/00001.webp... 合并成单个 PDF。

用法: python merge_pdf.py <车号或目录> [...]

特点:
- 按章节目录名排序，章节内按图片文件名自然排序
- 用 img2pdf 无损打包（不重新编码，保全原图分辨率）
- 输出到 <车号目录>/<车号>.pdf
"""
import io
import os
import sys

import img2pdf

IMAGE_EXTS = ('.webp', '.jpg', '.jpeg', '.png', '.gif', '.bmp')


def natural_key(name: str):
    """让 00002 排在 00010 前面（纯字符串排序也对，但这里更稳）。"""
    import re
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', name)]


def collect_images(album_dir: str):
    """收集章节目录下的图片，返回按顺序排列的路径列表。"""
    chapters = []
    for name in sorted(os.listdir(album_dir), key=natural_key):
        sub = os.path.join(album_dir, name)
        if not os.path.isdir(sub):
            continue
        imgs = [
            os.path.join(sub, f) for f in sorted(os.listdir(sub), key=natural_key)
            if f.lower().endswith(IMAGE_EXTS)
        ]
        if imgs:
            chapters.append((name, imgs))

    # 兼容图片直接放在车号根目录的情况
    if not chapters:
        imgs = [
            os.path.join(album_dir, f) for f in sorted(os.listdir(album_dir), key=natural_key)
            if f.lower().endswith(IMAGE_EXTS)
        ]
        if imgs:
            chapters.append((os.path.basename(album_dir), imgs))

    return chapters


def merge(album_dir: str) -> bool:
    album_dir = os.path.abspath(album_dir)
    album_id = os.path.basename(album_dir.rstrip('\\/'))
    if not os.path.isdir(album_dir):
        print(f'[跳过] 目录不存在: {album_dir}')
        return False

    chapters = collect_images(album_dir)
    if not chapters:
        print(f'[跳过] {album_id}: 没有找到任何图片')
        return False

    all_images = []
    for name, imgs in chapters:
        all_images.extend(imgs)
        print(f'  · {name} ({len(imgs)} 张)')

    out_pdf = os.path.join(album_dir, f'{album_id}.pdf')
    try:
        pdf_bytes = img2pdf.convert(all_images)
    except Exception as e:
        print(f'[失败] {album_id}: 整体转换出错 ({e})，改为逐张容错模式')
        valid = []
        for p in all_images:
            try:
                img2pdf.convert([p])
                valid.append(p)
            except Exception as item_err:
                print(f'   跳过坏图 {os.path.basename(p)}: {item_err}')
        if not valid:
            print(f'[失败] {album_id}: 无可转换图片')
            return False
        pdf_bytes = img2pdf.convert(valid)
        all_images = valid

    with open(out_pdf, 'wb') as fp:
        fp.write(pdf_bytes)

    size_mb = len(pdf_bytes) / 1024 / 1024
    print(f'[完成] {album_id}: {len(all_images)} 页, {size_mb:.1f} MB -> {out_pdf}')
    return True


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1

    base = os.path.dirname(os.path.abspath(__file__))
    default_root = os.path.join(os.path.dirname(base), 'downloads')

    ok = fail = 0
    for arg in args:
        target = arg if os.path.isdir(arg) else os.path.join(default_root, arg)
        print(f'=== {os.path.basename(target.rstrip(chr(92) + "/"))} ===')
        if merge(target):
            ok += 1
        else:
            fail += 1

    print(f'\n汇总: 成功 {ok} 个, 失败/跳过 {fail} 个')
    return 0 if fail == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
