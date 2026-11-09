"""受限文件解析子进程；仅输出 JSON，不加载应用配置或数据库。"""
import csv
import io
import json
import sys
import zipfile
from pathlib import Path

from qbot.knowledge_config import MAX_CHUNKS, MAX_FILE_BYTES, MAX_TEXT_BYTES


class ParseError(ValueError):
    pass


def check_office(path: Path, expected: str) -> None:
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > 5000 or sum(item.file_size for item in members) > 60 * 1024 * 1024:
            raise ParseError("Office 文件解压后超过60 MB或条目过多")
        if expected not in archive.namelist() or "[Content_Types].xml" not in archive.namelist():
            raise ParseError("文件内容与扩展名不匹配")
        if any(item.flag_bits & 1 for item in members):
            raise ParseError("暂不支持加密 Office 文件")


def extract(path: Path) -> list[dict]:
    """保留位置，超限整份失败，避免将不完整索引标为可用。"""
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ParseError("文件超过30 MB")
    chunks: list[dict] = []
    total = 0

    def add(text: str, location: str) -> None:
        nonlocal total
        text = text.replace("\x00", "").strip()
        if not text:
            return
        total += len(text.encode("utf-8"))
        if total > MAX_TEXT_BYTES:
            raise ParseError("提取正文超过2 MB，请拆分文件")
        # 短段以位置为边界，长段保留少量重叠，避免跨文件/跨页拼接。
        for start in range(0, len(text), 650):
            chunks.append({"content": text[start:start + 750], "location": location[:200]})
            if len(chunks) > MAX_CHUNKS:
                raise ParseError("文件超过1000个片段，请拆分文件")
            if start + 750 >= len(text):
                break

    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        with path.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise ParseError("文件不是有效 PDF")
            source.seek(0)
            reader = PdfReader(source, strict=True)
            if reader.is_encrypted:
                raise ParseError("暂不支持加密 PDF，请先解密")
            if len(reader.pages) > 300:
                raise ParseError("PDF超过300页，请拆分文件")
            for number, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                if not text.strip() and len(page.images):
                    raise ParseError("PDF含无法提取文字的图片页，请先OCR后上传")
                add(text, f"第{number}页")
    elif ext == ".docx":
        check_office(path, "word/document.xml")
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        document = Document(path)
        for number, block in enumerate(document.iter_inner_content(), 1):
            if isinstance(block, Paragraph):
                add(block.text, f"段落{number}")
            elif isinstance(block, Table):
                for row_number, row in enumerate(block.rows, 1):
                    add(" | ".join(cell.text for cell in row.cells), f"表格{number}·行{row_number}")
    elif ext == ".xlsx":
        check_office(path, "xl/workbook.xml")
        from openpyxl import load_workbook
        # 不执行公式；读取公式文本，避免将缺失/过期的缓存当成计算结果。
        workbook = load_workbook(path, read_only=True, data_only=False, keep_links=False)
        try:
            if len(workbook.worksheets) > 30:
                raise ParseError("工作表超过30张，请拆分文件")
            cells = 0
            for sheet in workbook.worksheets:
                if (sheet.max_row or 0) > 20000 or (sheet.max_column or 0) > 100:
                    raise ParseError("工作表超过20000行或100列，请拆分文件")
                header = None
                for row_number, row in enumerate(sheet.iter_rows(values_only=True), 1):
                    cells += len(row)
                    if row_number > 20000 or len(row) > 100 or cells > 200000:
                        raise ParseError("工作簿单元格数量超过限制")
                    values = [str(value) if value is not None else "" for value in row]
                    if not any(values):
                        continue
                    if header is None:
                        header = values
                    text = "；".join(f"{header[i] if i < len(header) and header[i] else '列' + str(i + 1)}: {value}"
                                      for i, value in enumerate(values) if value)
                    add(text, f"工作表 {sheet.title}·行{row_number}")
        finally:
            workbook.close()
    elif ext in (".txt", ".md", ".csv"):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            raise ParseError("文本文件请使用 UTF-8 编码") from None
        if "\x00" in text:
            raise ParseError("文件包含二进制内容")
        if ext == ".csv":
            csv.field_size_limit(100000)
            header = None
            for number, row in enumerate(csv.reader(io.StringIO(text)), 1):
                if number > 20000 or len(row) > 100:
                    raise ParseError("CSV超过20000行或100列")
                if header is None:
                    header = row
                add("；".join(f"{header[i] if i < len(header) and header[i] else '列' + str(i + 1)}: {value}"
                              for i, value in enumerate(row) if value), f"行{number}")
        else:
            lines = text.splitlines()
            for start in range(0, len(lines), 20):
                add("\n".join(lines[start:start + 20]), f"行{start + 1}–{min(start + 20, len(lines))}")
    else:
        raise ParseError("不支持该文件类型")
    if not chunks:
        raise ParseError("未提取到可用文字，扫描文件请先OCR")
    return chunks


if __name__ == "__main__":
    try:
        # Linux部署额外限制解析进程地址空间；Windows保留父进程的60秒硬超时。
        if sys.platform == "linux":
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        result = {"chunks": extract(Path(sys.argv[1]))}
    except ParseError as exc:
        result = {"error": str(exc)}
    except Exception:
        result = {"error": "文件解析失败，请检查格式、加密或文件损坏"}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
