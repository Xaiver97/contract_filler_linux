#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
供用电合同手写填充工具 · 入口

两种用法::

    python main.py                      # 启动图形界面
    python main.py --excel 数据.xlsx     # 命令行批量生成（无需图形界面）
"""

import argparse
import sys
import traceback
from pathlib import Path

# 源码运行才需要把项目根加入 sys.path；打包成 exe 后由 PyInstaller 负责。
if not getattr(sys, "frozen", False):
    ROOT = Path(__file__).resolve().parent
    sys.path.insert(0, str(ROOT))


# --------------------------------------------------------------------------- #
#  命令行批量模式
# --------------------------------------------------------------------------- #
def cli_main(args) -> int:
    from batch import BatchRunner, load_excel
    from core.composer import ContractComposer

    composer = ContractComposer(config_path=args.config)

    st = composer.status()
    print(f"模板：{st['template']} ({'已找到' if st['template_exists'] else '缺失'})")
    print(f"字体：{st['fonts_total']} 套（含中文 {st['fonts_cjk']} 套）")
    print(f"坐标：{'已校准' if st['calibrated'] else '未校准'}")
    if not st["template_exists"]:
        print("\n[错误] 找不到合同模板，请先放入。")
        return 2
    if st["fonts_total"] == 0:
        print("\n[警告] fonts/ 目录为空，将回退到系统默认字体。")

    records, warnings = load_excel(args.excel, composer.fields)
    print(f"\n读取 {len(records)} 条记录")
    for w in warnings[:20]:
        print(f"  [提示] {w}")

    if not records:
        print("[错误] 没有可生成的记录。")
        return 2

    runner = BatchRunner(composer, args.out, fmt=args.fmt,
                         quality=args.quality, seed_base=args.seed,
                         chunk_size=args.chunk)

    def on_progress(i, total, msg):
        if msg:
            print(f"  {msg}", flush=True)

    files, errors = runner.run(records, progress_cb=on_progress)
    print(f"\n完成：成功 {len(files)} 份，失败 {len(errors)} 份")
    print(f"输出目录：{Path(args.out).resolve()}")
    return 0 if not errors else 1


# --------------------------------------------------------------------------- #
#  图形界面模式
# --------------------------------------------------------------------------- #
def gui_main(args) -> int:
    try:
        from gui import qt_compat
        from gui.qt_compat import QT_BINDING, Qt, QtWidgets
    except ImportError as e:
        print("[错误] 无法加载图形界面依赖：\n")
        print(str(e))
        print("\n请安装任意一个 Qt 绑定后重试：")
        print("    pip install PyQt5      或    pip install PySide6")
        print("\n或者直接用命令行批量模式，无需图形界面：")
        print("    python main.py --excel 数据.xlsx")
        return 3

    # 高 DPI 适配（Qt5 需要手动开启，且必须在 QApplication 之前设置）
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QtWidgets.QApplication.setAttribute(
            Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, "AA_UseHighDpiPixmaps"):
        QtWidgets.QApplication.setAttribute(
            Qt.AA_UseHighDpiPixmaps, True)

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("供用电合同手写填充工具")
    app.setStyle("Fusion")   # 跨平台外观一致

    def _excepthook(etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        sys.stderr.write(text)
        try:
            QtWidgets.QMessageBox.critical(
                None, "程序出错了",
                f"{etype.__name__}: {value}\n\n{text[-2000:]}")
        except Exception:
            pass

    sys.excepthook = _excepthook

    from core.composer import ContractComposer
    from gui.main_window import MainWindow

    try:
        composer = ContractComposer(config_path=args.config)
    except Exception as e:
        QtWidgets.QMessageBox.critical(
            None, "初始化失败", f"{type(e).__name__}: {e}")
        return 4

    win = MainWindow(composer)
    win.show()

    exec_fn = getattr(app, "exec", None) or getattr(app, "exec_")
    return exec_fn()


# --------------------------------------------------------------------------- #
def build_parser():
    ap = argparse.ArgumentParser(
        description="供用电合同手写填充工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python main.py\n"
            "  python main.py --excel 客户数据.xlsx --out 输出 --fmt pdf_merged\n"))
    ap.add_argument("--excel", help="Excel 路径；给出则进入命令行批量模式")
    ap.add_argument("--out", default="output", help="输出目录（默认 output）")
    ap.add_argument("--fmt", default="jpg",
                    choices=["jpg", "png", "pdf", "pdf_merged",
                             "docx", "docx_merged"],
                    help="输出格式")
    ap.add_argument("--quality", type=int, default=95, help="JPG 质量 1-100")
    ap.add_argument("--chunk", type=int, default=0,
                    help="合订本每卷页数（pdf_merged/docx_merged 用），0 表示不分卷")
    ap.add_argument("--seed", type=int, default=None,
                    help="随机种子；留空则每份都不同")
    ap.add_argument("--config", default=None, help="指定配置文件路径")
    return ap


def main():
    args = build_parser().parse_args()
    try:
        if args.excel:
            return cli_main(args)
        return gui_main(args)
    except KeyboardInterrupt:
        print("\n已中断")
        return 130
    except Exception as e:
        traceback.print_exc()
        print(f"\n[错误] {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
