# 02 — 系統架構 (Architecture)

## 1. 總覽

```
┌──────────────────────────────────────────────────────────────┐
│  Web 前端 (web/)  — 檢視器 / 編輯器（Vanilla TS + Vite）       │
└──────────────▲───────────────────────────────────────────────┘
               │ REST / JSON
┌──────────────┴───────────────┐   ┌───────────────────────────┐
│  API 服務 anydoc.server       │   │  CLI anydoc.cli (Typer)    │
│  (FastAPI)                    │   │                           │
└──────────────▲───────────────┘   └────────────▲──────────────┘
               │                                │
┌──────────────┴────────────────────────────────┴──────────────┐
│  核心 anydoc.core                                              │
│   ├─ detect      格式辨識（magic bytes + 副檔名 + 內容探測）     │
│   ├─ model       統一文件模型 Document / Part / Metadata        │
│   ├─ registry    Handler 外掛註冊表 + 能力 (Capability) 查詢     │
│   ├─ convert     轉換圖 (ConversionGraph) 與路徑搜尋             │
│   ├─ ops         跨格式操作（文字擷取、縮圖、搜尋、diff）         │
│   └─ security    大小限制、ZIP bomb 防護、巨集剝除               │
└──────────────▲───────────────────────────────────────────────┘
               │ Handler 介面
┌──────────────┴───────────────────────────────────────────────┐
│  格式 Handlers anydoc.formats.*                                 │
│   pdf · docx · xlsx · pptx · csv · text · dxf · dwg · image ·   │
│   odf · legacy(doc/xls/ppt via LibreOffice) · rtf/html/epub     │
└──────────────▲───────────────────────────────────────────────┘
               │
   第三方函式庫 / 外部工具（選用）
```

## 2. 目錄結構

```
AnyDocument/
├─ CLAUDE.md                 # Routine 執行規範
├─ pyproject.toml
├─ src/anydoc/
│  ├─ __init__.py            # 公開 API：open(), convert(), detect()
│  ├─ core/ (detect.py, model.py, registry.py, convert.py, ops/, security.py, errors.py)
│  ├─ formats/ (pdf/, docx/, xlsx/, pptx/, csv/, text/, cad/, image/, odf/, legacy/, ...)
│  ├─ external/              # LibreOffice / Tesseract / ODA 外部工具包裝（可偵測是否存在）
│  ├─ pipeline/              # 批次管線
│  ├─ cli/                   # Typer CLI
│  └─ server/                # FastAPI
├─ web/                      # 前端
├─ tests/
│  ├─ fixtures/generate.py   # 以程式產生測試檔（不提交大型二進位檔）
│  ├─ unit/  integration/  e2e/
├─ scripts/make_test_report.py
└─ docs/ (01..04, PROGRESS.md, test-reports/)
```

## 3. 核心抽象

```python
class Capability(StrEnum):
    READ = "read"; WRITE = "write"; RENDER = "render"; EXTRACT_TEXT = "extract_text"
    EDIT = "edit"; CONVERT = "convert"; THUMBNAIL = "thumbnail"

@dataclass
class Metadata:
    format: str; mime: str; title: str | None; author: str | None
    created: datetime | None; modified: datetime | None
    page_count: int | None; extra: dict[str, Any]

@dataclass
class Part:          # 頁 / 工作表 / 投影片 / 圖層 / 配置 (layout)
    kind: str; index: int; name: str | None; size: tuple[float, float] | None

class Document(Protocol):
    path: Path | None
    format: str
    metadata: Metadata
    def parts(self) -> list[Part]: ...
    def text(self) -> str: ...
    def save(self, path: Path | None = None) -> Path: ...
    def close(self) -> None: ...

class FormatHandler(ABC):
    format_id: ClassVar[str]               # "pdf", "docx", ...
    extensions: ClassVar[tuple[str, ...]]
    mime_types: ClassVar[tuple[str, ...]]
    capabilities: ClassVar[frozenset[Capability]]
    @classmethod
    def sniff(cls, head: bytes, path: Path | None) -> float: ...   # 0..1 信心度
    def open(self, source: Path | bytes) -> Document: ...
    def create(self) -> Document: ...      # 若支援 WRITE
```

- 每個格式的 `Document` 子類別再提供**格式專屬的編輯 API**（例如 `PdfDocument.rotate_pages()`、
  `XlsxDocument.sheet("A").cell("B2").value = 3`），CLI 與 API 層直接對應這些方法。
- Handler 透過 `entry_points`（群組 `anydoc.formats`）註冊，第三方可擴充。

## 4. 轉換圖

- 每個轉換器宣告 `(src_format, dst_format, cost, requires_external)`。
- `ConversionGraph.find_path(src, dst)` 以 Dijkstra 找最低成本路徑，跳過外部工具不存在的邊。
- 例：`dwg → dxf (ODA) → svg (ezdxf) → png (cairosvg)`；`pptx → pdf (LibreOffice) → png (pdfium)`。

## 5. 技術選型

| 用途 | 套件 | 授權 |
|------|------|------|
| PDF 讀寫/編輯 | pypdf | BSD |
| PDF 文字/表格位置 | pdfplumber | MIT |
| PDF 渲染 | pypdfium2 | Apache-2.0/BSD |
| PDF 產生/浮水印 | reportlab | BSD |
| DOCX | python-docx | MIT |
| XLSX | openpyxl；公式計算 `formulas` 或 `pycel` | MIT / EUPL→評估 |
| PPTX | python-pptx | MIT |
| CSV / 資料 | pandas、charset-normalizer | BSD / MIT |
| DXF | ezdxf（含 drawing addon、matplotlib 後端） | MIT |
| DWG | ODA File Converter 或 LibreDWG `dwg2dxf`（選用外部工具） | 外部 |
| 影像 | Pillow | HPND |
| 格式辨識 | 自寫 magic bytes + `filetype` | MIT |
| 舊版 Office / ODF→PDF | LibreOffice headless（選用外部工具） | MPL |
| OCR | Tesseract + pytesseract（選用） | Apache |
| CLI | Typer | MIT |
| API | FastAPI + Uvicorn | MIT/BSD |
| 前端 | TypeScript + Vite；試算表格線以自建或 MIT 元件 | MIT |
| 測試 | pytest、pytest-cov、hypothesis、Playwright | |
| 品質 | ruff、mypy | |

Python 版本：**≥ 3.11**（容器內為 3.13）。

## 6. 錯誤處理原則

- 例外階層：`AnyDocError` → `UnsupportedFormatError`、`CorruptFileError`、
  `ExternalToolMissingError`、`SecurityLimitError`、`OperationNotSupportedError`。
- 外部工具缺少時拋 `ExternalToolMissingError`，測試以 `pytest.mark.requires("libreoffice")` 自動 skip。

## 7. 安全

- 預設上限：單檔 200 MB、ZIP 解壓比 100:1、解壓總量 1 GB。
- Office 檔可剝除 `vbaProject.bin`；不執行任何巨集。
- 伺服器上傳檔存放於隔離暫存目錄，逾時自動清除。
