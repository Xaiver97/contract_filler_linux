# -*- coding: utf-8 -*-
"""
批量生成 —— 读取记录列表，逐份渲染并落盘。

要点：
    * 模板只解码一次，批量时性能的关键
    * 每户自动换一套字体 + 独立随机种子，避免"同一个人抄了一百份"
    * 支持 JPG / PNG / 单份 PDF / 合并 PDF
"""

import random
import re
from pathlib import Path

from .docx_export import export_many as _export_docx_many
from .docx_export import export_one as _export_docx_one

__all__ = ["BatchRunner", "sane_filename"]


def sane_filename(s: str, maxlen: int = 80, default: str = "contract") -> str:
    """清理文件名中的非法字符（Windows / Linux 通用）。"""
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "", str(s or "")).strip()
    s = re.sub(r"\s+", "_", s)
    s = s.strip("._") or default
    return s[:maxlen]


class BatchRunner:
    """批量渲染合同。"""

    def __init__(self, composer, output_dir,
                 fmt: str = "jpg", quality: int = 95, dpi: int = 200,
                 font_mode: str = "random", seed_base=None,
                 chunk_size: int = 0, naming: str = "序号_用电人_客户编号",
                 fixed_font: str = None):
        """
        参数
        ----
        composer : ContractComposer
        output_dir : str | Path
        fmt : 'jpg' | 'png' | 'pdf' | 'pdf_merged' | 'docx' | 'docx_merged'
        quality : JPG 质量 1-100
        dpi : PDF 输出分辨率
        font_mode : 'random' 每户换字体 | 'fixed' 全用同一套
        seed_base : int | None，None 表示每份真随机
        chunk_size : 合并 PDF 时每份文件的页数，0 表示全部合并
        naming : 文件名模板说明（实际按 序号_用电人_客户编号 组合）
        fixed_font : str | None，font_mode='fixed' 时指定正文/签名共用哪一套
                    字体的路径。留空则用目录里第一套。
        """
        self.composer = composer
        self.output_dir = Path(output_dir)
        self.fmt = fmt.lower()
        self.quality = max(1, min(100, int(quality)))
        self.dpi = int(dpi)
        self.font_mode = font_mode
        self.seed_base = seed_base
        self.chunk_size = int(chunk_size or 0)
        self.naming = naming
        self.fixed_font = fixed_font

        self._fixed_font = None
        self._fixed_sign = None

    # ------------------------------------------------------------------ #
    def prepare(self):
        """预加载模板并确定固定字体（font_mode='fixed' 时）。"""
        self.composer.load_template()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if self.font_mode == "fixed":
            fonts = self.composer.fonts.scan()
            if self.fixed_font:
                # 用户指定：正文与签名用同一套（"全用同一套字体"）
                self._fixed_font = str(self.fixed_font)
                self._fixed_sign = str(self.fixed_font)
            else:
                self._fixed_font = str(fonts[0]) if fonts else ""
                self._fixed_sign = self.composer.fonts.guess_signature_font() or \
                    self._fixed_font
        return self

    def _filename(self, rec, idx: int) -> str:
        parts = [f"{idx + 1:03d}"]
        name = rec.get("customer_name") or rec.get("signature") or ""
        if name:
            parts.append(str(name))
        no = rec.get("customer_no")
        if no:
            parts.append(str(no))
        return sane_filename("_".join(parts), default=f"contract_{idx + 1:03d}")

    def _docx_filename(self, rec, idx: int) -> str:
        """DOCX（每户一份）的文件名：以用户编号(customer_no)命名。"""
        no = rec.get("customer_no")
        if no:
            return sane_filename(str(no), default=f"contract_{idx + 1:03d}")
        name = rec.get("customer_name") or rec.get("signature") or ""
        if name:
            return sane_filename(str(name), default=f"contract_{idx + 1:03d}")
        return f"contract_{idx + 1:03d}"

    def _pick_fonts(self, rng):
        if self.font_mode == "fixed":
            return self._fixed_font, self._fixed_sign
        fb = self.composer.fonts.pick(rng) or ""
        fs = self.composer.fonts.guess_signature_font() or fb
        return fb, fs

    # ------------------------------------------------------------------ #
    def run(self, records, progress_cb=None, log_cb=None, cancel=None):
        """执行批量生成。

        返回 (生成的文件路径列表, 错误列表[(序号, 说明)])
        """
        self.prepare()
        total = len(records)
        files, errors = [], []
        pdf_buffer = []
        docx_buffer = []      # (caption, image) 列表
        docx_meta = []        # 暂存每户 caption
        rng = random.Random()

        def _emit(i, msg=None):
            if progress_cb:
                progress_cb(i, total, msg or "")
            if log_cb and msg:
                log_cb(msg)

        def _flush_pdf(buf, tag):
            if not buf:
                return None
            if len(buf) == 1:
                out = self.output_dir / f"{tag}.pdf"
                buf[0].convert("RGB").save(out, "PDF", resolution=self.dpi)
            else:
                out = self.output_dir / f"{tag}.pdf"
                buf[0].convert("RGB").save(
                    out, "PDF", save_all=True,
                    append_images=[im.convert("RGB") for im in buf[1:]],
                    resolution=self.dpi)
            files.append(out)
            return out

        def _flush_docx(buf, tag):
            """buf: list of (caption, PIL.Image)"""
            if not buf:
                return None
            out = self.output_dir / f"{tag}.docx"
            _export_docx_many(buf, out)
            files.append(out)
            return out

        is_pdf_merged = self.fmt == "pdf_merged"
        is_docx_merged = self.fmt == "docx_merged"
        is_docx_single = self.fmt == "docx"

        for i, rec in enumerate(records):
            if cancel and cancel():
                _emit(i, "已取消")
                break

            row_no = rec.get("_row", i + 1)
            try:
                seed = None if self.seed_base is None else self.seed_base + i
                fb, fs = self._pick_fonts(rng)

                img = self.composer.render(rec, seed=seed,
                                           font_body=fb, font_sign=fs)
                base = self._filename(rec, i)
                # DOCX 合订本（缓存，收尾/分卷时落盘）
                if is_docx_merged:
                    caption = (f"{rec.get('customer_name') or ''} "
                               f"{rec.get('customer_no') or ''}").strip() \
                              or base
                    docx_buffer.append((caption, img))
                    if self.chunk_size and \
                            len(docx_buffer) >= self.chunk_size:
                        n = len(files) + 1
                        tag = f"合同_{n:03d}卷"
                        _flush_docx(docx_buffer, tag)
                        docx_buffer = []
                        _emit(i + 1, f"已输出 {tag}")
                    else:
                        _emit(i + 1,
                              f"[{i + 1}/{total}] 已渲染：{caption}")
                    continue

                # DOCX 每户一份：立即独立落盘，不合并，文件名以用户编号命名
                if is_docx_single:
                    dname = self._docx_filename(rec, i)
                    out = self.output_dir / f"{dname}.docx"
                    _export_docx_one(img, out)
                    files.append(out)
                    _emit(i + 1, f"[{i + 1}/{total}] {out.name}")
                    continue

                # PDF 合并
                if is_pdf_merged:
                    pdf_buffer.append(img)
                    if self.chunk_size and len(pdf_buffer) >= self.chunk_size:
                        n = len(files) + 1
                        tag = f"合同_{n:03d}卷"
                        _flush_pdf(pdf_buffer, tag)
                        pdf_buffer = []
                        _emit(i + 1, f"已输出 {tag}")
                    continue

                # 单文件（jpg / png / pdf / docx）
                if self.fmt == "pdf":
                    out = self.output_dir / f"{base}.pdf"
                    img.convert("RGB").save(out, "PDF",
                                            resolution=self.dpi)
                elif self.fmt == "png":
                    out = self.output_dir / f"{base}.png"
                    img.convert("RGB").save(out, "PNG", optimize=True)
                else:
                    out = self.output_dir / f"{base}.jpg"
                    img.convert("RGB").save(out, "JPEG",
                                            quality=self.quality,
                                            subsampling=0,
                                            optimize=True)
                files.append(out)
                _emit(i + 1, f"[{i + 1}/{total}] {out.name}")

            except Exception as e:
                errors.append((row_no, f"{type(e).__name__}: {e}"))
                _emit(i + 1, f"[{i + 1}/{total}] 第 {row_no} 行失败：{e}")
                continue

        # 收尾
        if is_pdf_merged and pdf_buffer:
            n = len(files) + 1
            _flush_pdf(pdf_buffer, f"合同_{n:03d}卷")
        elif is_docx_merged and docx_buffer:
            n = len(files) + 1
            _flush_docx(docx_buffer, f"合同_{n:03d}卷")

        _emit(total, f"完成：成功 {len(files)} 份，失败 {len(errors)} 份")
        return files, errors
