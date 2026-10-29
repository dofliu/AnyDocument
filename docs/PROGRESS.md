# 進度日誌 (Progress Log)

每次 Routine 執行後在**最上方**新增一筆（最新在前）。

| 日期 | 工作 | 結果 | 測試 | 報告 | 備註 |
|------|------|------|------|------|------|
| 2026-10-29 | T019 DXF 讀取 | ✅ 完成 | 436 passed | [報告](test-reports/2026-10-29_T019.md) | anydoc.formats.dxf（DxfHandler/DxfDocument：版本/release、單位、圖層（顏色/線型/開關/凍結/鎖定）、圖元統計與清單、圖塊、配置、TEXT/MTEXT/ATTRIB 文字擷取；ezdxf 升為正式相依；唯讀，save 以 ezdxf 寫回）；entry_points 註冊 dxf；CLI test_info_no_handler 改用 png 樣本 |
| 2026-10-29 | T018 ODF（ODT/ODS/ODP） | ✅ 完成 | 419 passed | [報告](test-reports/2026-10-29_T018.md) | anydoc.formats.odf（OdtHandler/OdsHandler/OdpHandler：defusedxml 直接解析 content.xml/meta.xml，無新增相依；ODT 區塊/標題/清單/表格/圖片、ODS 工作表/儲存格型別/公式/重複列欄、ODP 投影片/版面/圖形/備忘稿；唯讀，save 寫回原封包）；entry_points 註冊 odt/ods/odp |
| 2026-10-29 | T017 舊版 DOC/XLS/PPT | ✅ 完成 | 381 passed | [報告](test-reports/2026-10-29_T017.md) | anydoc.formats.legacy（DocHandler/XlsHandler/PptHandler：LibreOffice 轉 OOXML 後委派 Docx/Xlsx/Pptx Handler，文件 format 回報 doc/xls/ppt 並與來源路徑脫鉤）；新增 external.convert_with_libreoffice（獨立 profile）；非 OLE2 內容拋 CorruptFileError；LibreOffice 缺少拋 ExternalToolMissingError；entry_points 註冊 doc/xls/ppt |
| 2026-10-29 | T016 PDF 頁面渲染 | ✅ 完成 | 363 passed | [報告](test-reports/2026-10-29_T016.md) | PdfDocument.render_page(index, dpi=, scale=, path=)（pypdfium2 → PNG，套用頁面旋轉、加密 PDF 以密碼渲染）；PdfHandler 新增 RENDER 能力；pypdfium2、pillow 升為正式相依；mypy 對 pypdfium2 設 ignore_missing_imports；ruff format 檢查範圍同 CI（src tests） |
| 2026-10-28 | T015 PDF 讀取 | ✅ 完成 | 349 passed | [報告](test-reports/2026-10-28_T015.md) | anydoc.formats.pdf（PdfHandler/PdfDocument：中繼資料、頁數、頁面尺寸/旋轉、page_text/words（含座標，pdfplumber）、images、加密狀態與密碼解鎖）；pypdf、pdfplumber 升為正式相依；修正 T004 PDF 樣本中文字型（STSong-Light/UniGB）；CLI/registry「無 handler」測試改用 dxf/png |
| 2026-10-28 | T014 PPTX 讀取 | ✅ 完成 | 325 passed | [報告](test-reports/2026-10-28_T014.md) | anydoc.formats.pptx（PptxHandler/PptxDocument：投影片＋版面配置名稱、標題、圖形樹（文字框/圖片/表格/圖表/群組）、備忘稿、text()）；python-pptx 升為正式相依；entry_points 註冊 pptx；損毀檔案拋 CorruptFileError |
| 2026-10-28 | T013 XLSX 讀取 | ✅ 完成 | 305 passed | [報告](test-reports/2026-10-28_T013.md) | anydoc.formats.xlsx（XlsxHandler/XlsxDocument：工作表狀態與使用範圍、儲存格值/型別/公式/快取值、合併儲存格、樣式摘要、定義名稱、text()）；openpyxl 升為正式相依、dev 加 types-openpyxl；entry_points 註冊 xlsx；損毀檔案拋 CorruptFileError |
| 2026-10-28 | T012 DOCX 讀取 | ✅ 完成 | 280 passed | [報告](test-reports/2026-10-28_T012.md) | anydoc.formats.docx（DocxHandler/DocxDocument：段落＋標題層級、表格、圖片清單、頁首頁尾、註腳（解析 footnotes.xml）、核心屬性、閱讀順序 text()）；python-docx 升為正式相依；以 entry_points 註冊 docx；損毀檔案統一拋 CorruptFileError |
| 2026-10-28 | T011 Text 系列 Handler | ✅ 完成 | 255 passed | [報告](test-reports/2026-10-28_T011.md) | anydoc.formats.text（txt/markdown/json/yaml/xml；編碼與換行保留、Markdown 標題、XML 以 defusedxml 解析防 XXE/entity bomb、YAML safe_load）；新增相依 pyyaml、defusedxml；XML 非 expat 編碼（Big5）自動轉碼 |
| 2026-10-27 | T010 CSV/TSV Handler | ✅ 完成 | 224 passed | [報告](test-reports/2026-10-27_T010.md) | anydoc.formats.csv（編碼 utf-8/BOM/big5/cp950/utf-16、分隔符號、型別推斷、往返保留編碼）；以 pyproject entry_points 註冊 csv/tsv；修正 CLI 測試不再假設 registry 僅含 fake handler；複查時修正 detect 對 ; / \| 分隔 .csv 誤判為 txt |
| 2026-10-26 | T007 外部工具偵測層 | ✅ 完成 | 174 passed | [報告](test-reports/2026-10-26_T007.md) | anydoc.external（libreoffice/tesseract/oda/libredwg 偵測、run_tool 逾時/暫存目錄）；conftest requires marker 自動 skip；ANYDOC_<TOOL> 可覆寫路徑 |
| 2026-10-25 | T006 CLI 骨架 | ✅ 完成 | 160 passed | [報告](test-reports/2026-10-25_T006.md) | anydoc.cli（formats/detect/info、--version）；新增相依 typer、console script `anydoc`；尚無 Handler 故 info 對真實檔回報 No handler |
| 2026-10-24 | T005 測試報告產生器 | ✅ 完成 | 147 passed | [報告](test-reports/2026-10-24_T005.md) | scripts/make_test_report.py（ruff/mypy/pytest/coverage → 報告＋INDEX）；CLAUDE.md 第 6 步起可直接使用 |
| 2026-10-23 | T004 測試樣本產生器 | ✅ 完成 | 141 passed | [報告](test-reports/2026-10-23_T004.md) | tests/fixtures/generate.py 12 種樣本、`sample(fmt)` fixture；dev extras 新增文件函式庫 |
| 2026-10-22 | T003 Handler 註冊表與格式辨識 | ✅ 完成 | 117 passed | [報告](test-reports/2026-10-22_T003.md) | core/registry.py、core/detect.py、anydoc.detect/open；無新增相依套件（未用 filetype） |
| 2026-10-21 | T002 統一文件模型與例外 | ✅ 完成 | 19 passed | [報告](test-reports/2026-10-21_T002.md) | core/model.py、core/errors.py；環境請用 /usr/bin/python3（3.13） |
| 2026-10-20 | T001 專案骨架 | ✅ 完成 | 2 passed | [報告](test-reports/2026-10-20_T001.md) | pyproject、src layout、ruff/mypy/pytest、CI |
| 2026-10-20 | T000 專案規劃文件 | ✅ 完成 | — | — | 建立章程、架構、Roadmap、測試策略、Routine 規範 |
