# 04 — 測試策略 (Testing)

## 1. 測試層級

| 層級 | 目錄 | 內容 | 執行時機 |
|------|------|------|------|
| 單元測試 | `tests/unit/` | 單一函式/類別，無 I/O 或使用 `tmp_path` | 每次 |
| 整合測試 | `tests/integration/` | 實際開啟/寫入檔案、跨模組、轉換鏈 | 每次 |
| 往返測試 | `tests/integration/roundtrip/` | 開啟→編輯→儲存→重新開啟→驗證內容 | 每次 |
| 外部工具測試 | 標記 `@pytest.mark.requires("libreoffice")` | LibreOffice/Tesseract/ODA | 工具存在才執行 |
| API 測試 | `tests/api/` | FastAPI TestClient | Phase 4 起 |
| E2E | `tests/e2e/` | Playwright（Chromium 已預裝於 `/opt/pw-browsers`） | Phase 5 起 |

## 2. 原則

1. **每項 Roadmap 工作都必須新增測試**，並且整個測試套件必須通過（不得造成回歸）。
2. 測試樣本以 `tests/fixtures/generate.py` 程式產生，**不提交大型二進位檔**；必要的小型真實樣本（< 200 KB）
   可放 `tests/fixtures/static/` 並註明來源與授權。
3. 樣本內容須包含**中文（繁體）**，驗證編碼與字型處理。
4. 編輯功能必須有**往返測試**：輸出檔需能被原生函式庫（或 LibreOffice）重新開啟。
5. 禁止為了讓測試通過而 skip、刪除或弱化既有測試。外部工具缺少才可 skip。
6. 覆蓋率目標：整體 ≥ 80%，新程式碼 ≥ 85%。

## 3. 指令

```bash
pip install -e ".[dev]"
ruff check src tests && ruff format --check src tests
mypy src
pytest --cov=anydoc --cov-report=term-missing
python scripts/make_test_report.py --task T0xx --title "工作標題"   # T005 完成後
```

## 4. 測試報告格式

檔名：`docs/test-reports/<YYYY-MM-DD>_<TaskID>.md`，並在 `docs/test-reports/INDEX.md` 加一列。

```markdown
# 測試報告 — T0xx 工作標題

- 日期：YYYY-MM-DD
- Commit：<short sha>（報告產生時的 HEAD；完成 commit 見 PROGRESS.md）
- 環境：Python 3.x / OS / 外部工具：LibreOffice ✅ Tesseract ❌ ODA ❌

## 摘要
| 項目 | 結果 |
|------|------|
| ruff | ✅ 通過 |
| mypy | ✅ 通過 |
| pytest | ✅ 132 passed, 3 skipped, 0 failed（12.3s） |
| 覆蓋率 | 86.4%（本次新增模組：92.1%） |

## 本次新增/修改的測試
| 測試 | 驗證內容 | 結果 |
|------|------|------|

## 驗收條件檢查
- [x] 條件 1 — 對應測試 `test_xxx`
- [x] 條件 2 — ...

## 跳過的測試與原因
## 已知問題 / 後續建議
```
