# 進度日誌 (Progress Log)

每次 Routine 執行後在**最上方**新增一筆（最新在前）。

| 日期 | 工作 | 結果 | 測試 | 報告 | 備註 |
|------|------|------|------|------|------|
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
