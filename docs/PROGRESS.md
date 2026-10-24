# 進度日誌 (Progress Log)

每次 Routine 執行後在**最上方**新增一筆（最新在前）。

| 日期 | 工作 | 結果 | 測試 | 報告 | 備註 |
|------|------|------|------|------|------|
| 2026-10-24 | T005 測試報告產生器 | ✅ 完成 | 147 passed | [報告](test-reports/2026-10-24_T005.md) | scripts/make_test_report.py（ruff/mypy/pytest/coverage → 報告＋INDEX）；CLAUDE.md 第 6 步起可直接使用 |
| 2026-10-23 | T004 測試樣本產生器 | ✅ 完成 | 141 passed | [報告](test-reports/2026-10-23_T004.md) | tests/fixtures/generate.py 12 種樣本、`sample(fmt)` fixture；dev extras 新增文件函式庫 |
| 2026-10-22 | T003 Handler 註冊表與格式辨識 | ✅ 完成 | 117 passed | [報告](test-reports/2026-10-22_T003.md) | core/registry.py、core/detect.py、anydoc.detect/open；無新增相依套件（未用 filetype） |
| 2026-10-21 | T002 統一文件模型與例外 | ✅ 完成 | 19 passed | [報告](test-reports/2026-10-21_T002.md) | core/model.py、core/errors.py；環境請用 /usr/bin/python3（3.13） |
| 2026-10-20 | T001 專案骨架 | ✅ 完成 | 2 passed | [報告](test-reports/2026-10-20_T001.md) | pyproject、src layout、ruff/mypy/pytest、CI |
| 2026-10-20 | T000 專案規劃文件 | ✅ 完成 | — | — | 建立章程、架構、Roadmap、測試策略、Routine 規範 |
