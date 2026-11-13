# AnyDocument

一個能「開啟、檢視、編輯、轉換」各種文件格式的統一系統。

| 類別 | 格式 |
|------|------|
| 文書 | DOCX、DOC、ODT、RTF、TXT、Markdown、HTML |
| 試算表 | XLSX、XLS、ODS、CSV、TSV |
| 簡報 | PPTX、PPT、ODP |
| 可攜文件 | PDF |
| CAD 圖檔 | DXF、DWG（透過轉換器） |
| 影像 | PNG、JPG、TIFF、BMP、WebP、SVG |
| 資料 | JSON、XML、YAML |

架構：**Python 核心函式庫（每種格式一個 Handler 外掛）+ CLI + FastAPI 服務 + Web 前端**。

## 專案文件

| 文件 | 內容 |
|------|------|
| [docs/01-PROJECT-CHARTER.md](docs/01-PROJECT-CHARTER.md) | 專案願景、範圍、各格式功能清單（功能對照表） |
| [docs/02-ARCHITECTURE.md](docs/02-ARCHITECTURE.md) | 系統架構、模組設計、技術選型 |
| [docs/03-ROADMAP.md](docs/03-ROADMAP.md) | **工作清單**：Routine 每次執行完成其中一項 |
| [docs/04-TESTING.md](docs/04-TESTING.md) | 測試策略與測試報告格式 |
| [docs/PROGRESS.md](docs/PROGRESS.md) | 每次執行的進度日誌 |
| [docs/test-reports/](docs/test-reports/) | 每次執行產生的測試報告 |
| [CLAUDE.md](CLAUDE.md) | 自動開發 Routine 的執行規範 |

## 自動開發流程

本專案由排程 Routine 每天自動推進：每次執行從 `docs/03-ROADMAP.md` 取出下一項未完成的工作 →
實作 → 撰寫/執行測試 → 產生測試報告 → 更新進度 → commit 並 push 到 `main`。
規範細節見 [CLAUDE.md](CLAUDE.md)。

## 快速開始（T001 完成後可用）

```bash
pip install -e ".[dev]"
pytest
anydoc info sample.docx
```

### API 服務（T100 起）

```bash
pip install -e ".[server]"
anydoc serve --port 8000            # 或 uvicorn --factory anydoc.server:create_app
curl -F "file=@sample.docx" http://127.0.0.1:8000/docs     # 上傳，回傳工作階段 id
curl -OJ http://127.0.0.1:8000/docs/<id>/download           # 下載
```

互動式 API 文件：`/api/docs`；設定：`ANYDOC_STORAGE_DIR`、`ANYDOC_MAX_UPLOAD_MB`（預設 200）、
`ANYDOC_SESSION_TTL`（秒，預設 3600）、`ANYDOC_CLEANUP_INTERVAL`。
