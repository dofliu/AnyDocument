# 03 — 工作清單 (Roadmap)

> **Routine 每次執行只完成「一項」工作。** 依序挑選第一個狀態為 `[ ]` 且相依工作皆為 `[x]` 的項目。
> 規範見 [CLAUDE.md](../CLAUDE.md)。

狀態標記：`[ ]` 未開始 · `[~]` 進行中（上次未完成，下次優先接續）· `[x]` 完成 · `[!]` 受阻（附原因）

工作太大無法一次完成時，拆成 `T0xxa`、`T0xxb`… 子項插入原位置，本次只完成第一個子項。

---

## Phase 0 — 專案基礎

- [x] **T001 專案骨架**
  - 相依：無
  - 內容：`pyproject.toml`（hatchling、`src/` layout、套件名 `anydoc`、extras `dev`）；ruff、mypy、pytest、pytest-cov 設定；
    `src/anydoc/__init__.py`（`__version__`）；`tests/` 結構；`.gitignore`；GitHub Actions CI（lint + typecheck + test）。
  - 驗收：`pip install -e ".[dev]"` 成功；`ruff check`、`mypy src`、`pytest` 全部通過（至少 1 個 smoke test）。

- [x] **T002 統一文件模型與例外**
  - 相依：T001
  - 內容：`core/model.py`（`Capability`、`Metadata`、`Part`、`Document` 基底類別）、`core/errors.py` 例外階層（見架構 §6）。
  - 驗收：單元測試涵蓋模型建立、序列化為 dict/JSON、例外繼承關係。

- [x] **T003 Handler 註冊表與格式辨識**
  - 相依：T002
  - 內容：`core/registry.py`（註冊、依 format/副檔名/MIME 查詢、entry_points 載入）；`core/detect.py`
    （magic bytes：PDF、ZIP-based OOXML/ODF 依內部檔名分辨 docx/xlsx/pptx/odt…、OLE2、DXF 文字頭、DWG `AC10xx`、PNG/JPG…；
    文字類以內容探測）；公開 `anydoc.detect(path)`、`anydoc.open(path)`。
  - 驗收：對每種格式的樣本（可用最小位元組串）辨識正確；副檔名錯誤時仍以內容為準；未知格式拋 `UnsupportedFormatError`。

- [ ] **T004 測試樣本產生器**
  - 相依：T001
  - 內容：`tests/fixtures/generate.py` 以程式產生 docx/xlsx/pptx/pdf/csv(UTF-8, UTF-8-BOM, Big5)/dxf/png/txt/json 等樣本到
    `tests/fixtures/generated/`（git ignore），提供 pytest fixture `sample(fmt)`；內容需含中文。
  - 驗收：`pytest` 執行時自動產生；每種樣本可被對應函式庫開啟。

- [ ] **T005 測試報告產生器**
  - 相依：T001
  - 內容：`scripts/make_test_report.py`：執行 ruff、mypy、pytest（`--junitxml` + coverage json），輸出
    `docs/test-reports/<YYYY-MM-DD>_<TaskID>.md`（格式見 `docs/04-TESTING.md` §4），並更新 `docs/test-reports/INDEX.md`。
  - 驗收：執行 `python scripts/make_test_report.py --task T005 --title "..."` 產生正確報告。

- [ ] **T006 CLI 骨架**
  - 相依：T003
  - 內容：Typer 應用 `anydoc`，指令：`formats`（列出支援格式與能力）、`detect <file>`、`info <file>`（JSON 輸出 metadata/parts）。
  - 驗收：CliRunner 測試涵蓋各指令與錯誤輸出（非零結束碼）。

- [ ] **T007 外部工具偵測層**
  - 相依：T002
  - 內容：`external/`：LibreOffice、Tesseract、ODA File Converter、LibreDWG 的存在偵測與執行包裝（逾時、暫存目錄）；
    pytest marker `requires(tool)` 自動 skip。
  - 驗收：存在/不存在兩種情境皆有測試（以 monkeypatch 模擬）。

## Phase 1 — 讀取與檢視（各格式 Handler）

- [ ] **T010 CSV/TSV Handler**：編碼偵測（UTF-8、BOM、Big5、CP950）、分隔符號偵測、型別推斷、讀寫、`parts()`=單一表格。相依：T003, T004
- [ ] **T011 Text 系列 Handler**：TXT、Markdown、JSON、XML、YAML 讀寫與結構化（JSON/YAML→dict，XML→樹）。相依：T003
- [ ] **T012 DOCX 讀取**：段落（含樣式/標題層級）、表格、圖片清單、頁首頁尾、註腳、核心屬性；`text()` 保留閱讀順序。相依：T003, T004
- [ ] **T013 XLSX 讀取**：工作表、使用範圍、儲存格值/型別/公式/快取值、合併儲存格、樣式摘要、定義名稱。相依：T003, T004
- [ ] **T014 PPTX 讀取**：投影片、版面配置名稱、圖形樹（文字框/圖片/表格/圖表）、備忘稿、文字擷取。相依：T003, T004
- [ ] **T015 PDF 讀取**：中繼資料、頁數、頁面尺寸/旋轉、文字擷取（含座標）、影像清單、是否加密。相依：T003, T004
- [ ] **T016 PDF 頁面渲染**：pypdfium2 渲染指定頁為 PNG（DPI、縮放），回傳 bytes/儲存。相依：T015
- [ ] **T017 舊版 DOC/XLS/PPT**：透過 LibreOffice 轉為 OOXML 後交給對應 Handler；工具不存在時拋 `ExternalToolMissingError`。相依：T007, T012, T013, T014
- [ ] **T018 ODF（ODT/ODS/ODP）**：讀取（odfpy 或直接解析 XML）文字/表格/投影片結構。相依：T003
- [ ] **T019 DXF 讀取**：版本、單位、圖層（顏色/線型/開關）、圖元統計與清單、圖塊、配置 (layouts)、文字擷取（TEXT/MTEXT/屬性）。相依：T003, T004
- [ ] **T020 DXF 渲染**：ezdxf drawing addon 輸出 SVG、PNG、PDF；可指定配置與圖層。相依：T019
- [ ] **T021 DWG 支援**：偵測 DWG 版本；透過 ODA File Converter 或 LibreDWG 轉 DXF 後開啟；工具不存在時優雅降級（僅回傳版本中繼資料）。相依：T007, T019
- [ ] **T022 影像 Handler**：PNG/JPG/TIFF(多頁)/BMP/WebP/GIF/SVG；中繼資料、EXIF、縮放、旋轉、格式轉換。相依：T003, T004
- [ ] **T023 RTF/HTML/EPUB 讀取**：文字與結構擷取。相依：T003

## Phase 2 — 統一 API 與轉換

- [ ] **T030 統一文字擷取 API**：`anydoc.extract_text(path)` 與 CLI `anydoc text`；所有已完成 Handler 的整合測試。相依：T010–T016, T019
- [ ] **T031 轉換圖引擎**：`ConversionGraph`、Dijkstra 路徑、略過缺少外部工具的邊；CLI `anydoc convert in out`。相依：T003, T007
- [ ] **T032 Office/ODF → PDF**：LibreOffice headless 轉換器註冊進轉換圖。相依：T031
- [ ] **T033 PDF ↔ 影像**：PDF→PNG（每頁）、影像→PDF（多張合一）。相依：T016, T022, T031
- [ ] **T034 DOCX ↔ Markdown / HTML**：雙向轉換（標題、清單、表格、粗斜體、圖片）。相依：T012, T031
- [ ] **T035 XLSX/CSV/JSON 互轉**：含多工作表→多 CSV、指定工作表、型別保留。相依：T010, T013, T031
- [ ] **T036 縮圖與預覽影像**：所有格式產生縮圖（PPTX 投影片經 PDF 渲染；DXF 經 T020）。相依：T020, T032, T033

## Phase 3 — 編輯功能

### PDF
- [ ] **T040 PDF 頁面操作**：合併、分割、擷取、旋轉、重新排序、刪除、插入空白頁；CLI `anydoc pdf ...`。相依：T015
- [ ] **T041 浮水印 / 頁首頁尾 / 頁碼**（文字與影像浮水印，支援中文字型）。相依：T040
- [ ] **T042 加密 / 解密 / 權限**。相依：T040
- [ ] **T043 表單欄位**：列出、填寫、扁平化。相依：T040
- [ ] **T044 註解**：讀取所有註解；新增螢光、文字註解、矩形、圖章。相依：T040
- [ ] **T045 書籤（大綱）讀寫**。相依：T040
- [ ] **T046 PDF 表格擷取**（pdfplumber）→ CSV/XLSX。相依：T015, T035
- [ ] **T047 OCR**：Tesseract（chi_tra + eng）產生可搜尋 PDF；工具缺少時 skip。相依：T007, T016
- [ ] **T048 壓縮 / 最佳化**（影像重新取樣、移除重複物件）。相依：T040
- [ ] **T049 遮蔽 (Redaction)**：依文字/區域真正移除內容並驗證不可擷取。相依：T040

### Word / DOCX
- [ ] **T050 DOCX 建立與基本編輯**：新文件、段落、標題、文字格式、清單、分頁。相依：T012
- [ ] **T051 尋找與取代**（跨 run 合併、保留格式、支援正規表示式、含表格與頁首頁尾）。相依：T050
- [ ] **T052 表格與圖片**：建立/編輯表格（合併儲存格、框線）、插入圖片（尺寸）。相依：T050
- [ ] **T053 頁首頁尾、分節、版面**：紙張大小、方向、邊界、頁碼欄位。相依：T050
- [ ] **T054 目錄產生**（TOC 欄位 + 依標題產生靜態目錄）。相依：T050
- [ ] **T055 追蹤修訂與註解**：讀取修訂/註解；接受/拒絕全部修訂；新增註解。相依：T050
- [ ] **T056 合併列印**：DOCX 範本 + CSV/XLSX 資料 → 多份 DOCX 或單一合併文件 / PDF。相依：T051, T035

### Excel / XLSX
- [ ] **T060 XLSX 建立與編輯**：儲存格/範圍寫入、新增/刪除/重新命名/複製工作表、插入/刪除列欄。相依：T013
- [ ] **T061 公式計算引擎**：評估工作簿公式（評估 `formulas` 套件授權後選用），支援常用函數，與 LibreOffice 結果比對。相依：T060
- [ ] **T062 格式與版面**：數值格式、字型、框線、填色、對齊、欄寬列高、凍結窗格、隱藏。相依：T060
- [ ] **T063 排序 / 篩選 / 移除重複**（寫回工作表、AutoFilter）。相依：T060
- [ ] **T064 圖表建立**：長條、折線、圓餅、散佈圖。相依：T060
- [ ] **T065 條件式格式與資料驗證**。相依：T062
- [ ] **T066 樞紐分析**（pandas 計算，輸出為新工作表）。相依：T060, T035

### PowerPoint / PPTX
- [ ] **T070 投影片管理**：新增（依版面配置）、刪除、複製、重新排序、備忘稿編輯。相依：T014
- [ ] **T071 文字與圖形**：文字方塊、格式、圖片、基本圖形、位置大小。相依：T070
- [ ] **T072 表格與圖表**。相依：T071
- [ ] **T073 版面/母片/主題套用**、以範本產生簡報（大綱 JSON → PPTX）。相依：T071

### CSV
- [ ] **T080 CSV 資料操作**：篩選、排序、去重、欄位選取/改名、join、group-by 統計；CLI `anydoc csv ...`。相依：T010

### CAD
- [ ] **T081 圖層管理**：新增/改名/刪除、顏色、線型、開關、凍結、鎖定。相依：T019
- [ ] **T082 圖元編輯**：新增/修改/刪除 LINE、CIRCLE、ARC、LWPOLYLINE、TEXT、MTEXT；移動/旋轉/縮放/複製。相依：T019
- [ ] **T083 量測**：兩點距離、聚合線長度、封閉區域面積、邊界框、圖元查詢（點選範圍）。相依：T019
- [ ] **T084 圖塊**：定義、插入、屬性讀寫、分解。相依：T082
- [ ] **T085 尺寸標註**：讀取量測值、建立線性/對齊/半徑標註。相依：T082

## Phase 4 — API 服務

- [ ] **T100 FastAPI 骨架**：健康檢查、上傳（大小限制）、文件工作階段儲存（暫存目錄 + TTL）、下載、OpenAPI。相依：T003
- [ ] **T101 檢視端點**：`/docs/{id}/info`、`/parts`、`/text`、`/render/{part}`（PNG/SVG）。相依：T100, T036
- [ ] **T102 轉換端點**：`/docs/{id}/convert?to=`。相依：T100, T031
- [ ] **T103 編輯端點**：以「操作 (operation) JSON」描述編輯，對應各格式編輯 API，支援 undo（版本快照）。相依：T100, T040, T050, T060, T070, T082
- [ ] **T104 安全強化**：ZIP bomb、巨集剝除、MIME 驗證、速率限制。相依：T100

## Phase 5 — Web 前端

- [ ] **T110 前端骨架**：Vite + TypeScript，檔案上傳、檔案清單、格式路由；Playwright 設定。相依：T100
- [ ] **T111 通用檢視器**：分頁渲染影像、縮放、頁面縮圖列、文字搜尋。相依：T110, T101
- [ ] **T112 PDF 編輯介面**：頁面拖曳排序、旋轉、刪除、合併、註解。相依：T111, T103
- [ ] **T113 試算表介面**：格線、儲存格編輯、公式列、工作表分頁。相依：T110, T103
- [ ] **T114 文書編輯介面**：段落/格式編輯、尋找取代。相依：T110, T103
- [ ] **T115 簡報介面**：投影片列、投影片編輯、放映模式。相依：T110, T103
- [ ] **T116 CAD 檢視器**：SVG 平移縮放、圖層面板、量測工具。相依：T110, T101, T083
- [ ] **T117 轉換介面**：選擇目標格式、批次轉換下載 ZIP。相依：T110, T102

## Phase 6 — 進階與交付

- [ ] **T090 全文搜尋**：資料夾索引（SQLite FTS5），CLI `anydoc search`。相依：T030
- [ ] **T091 文件比較**：文字 diff（DOCX/PDF/TXT）、XLSX 儲存格 diff、PPTX 投影片 diff，輸出 HTML 報告。相依：T030, T013, T014
- [ ] **T092 CAD 圖面比較**：新增/刪除/修改圖元標示。相依：T082
- [ ] **T093 批次管線**：YAML 定義步驟（轉換、浮水印、合併、OCR…），CLI `anydoc run pipeline.yaml`，支援資料夾監看。相依：T031, T040
- [ ] **T094 PDF 數位簽章驗證**。相依：T040
- [ ] **T095 Docker 映像**：含 LibreOffice、Tesseract（chi_tra）、LibreDWG；docker compose 啟動 API + 前端。相依：T100, T110
- [ ] **T096 效能與大型檔案**：串流讀取、10 萬列 CSV/XLSX、500 頁 PDF 基準測試。相依：T030
- [ ] **T097 使用者文件**：使用手冊、API 文件、CLI 範例。相依：T117
