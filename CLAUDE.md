# CLAUDE.md — AnyDocument 自動開發規範

本專案由排程 Routine 每天自動推進。每次執行（一個全新 session）都必須遵守以下流程。
所有文件以**繁體中文**撰寫；程式碼、識別字、commit 標題使用英文。

## 專案文件（每次執行前先讀）

1. `docs/03-ROADMAP.md` — 工作清單（**唯一的工作來源**）
2. `docs/02-ARCHITECTURE.md` — 架構、目錄結構、技術選型（必須遵守）
3. `docs/01-PROJECT-CHARTER.md` — 範圍與功能對照表
4. `docs/04-TESTING.md` — 測試策略與報告格式
5. `docs/PROGRESS.md` — 最近幾次執行的結果（了解上次狀況）

## 每次執行流程（一次只做一項工作）

1. **同步**：`git fetch origin main && git checkout main && git reset --hard origin/main`（每次都是全新容器，以遠端 `main` 為準）。
2. **環境**：若 `pyproject.toml` 存在，執行 `pip install -e ".[dev]"`；先跑一次完整測試確認 `main` 目前是綠燈。
   - 若 `main` 已是紅燈：本次工作改為「修復 main」，修好後依第 6–8 步記錄並推送，**不開始新工作**。
3. **選工作**：
   - 若有 `[~]` 進行中的項目 → 接續它。
   - 否則選第一個 `[ ]` 且所有相依項目皆為 `[x]` 的工作。
   - 若工作明顯超過一次可完成的量，先在 ROADMAP 拆成子項（`T0xxa`、`T0xxb`），本次只做第一個子項。
4. **實作**：遵守架構文件；只做該工作範圍內的事，不順手改無關程式。新增相依套件時寫入 `pyproject.toml`，
   並確認授權不是 AGPL/GPL（見章程 §7）。
5. **測試**：
   - 為本工作撰寫單元測試＋整合/往返測試，逐條對應 ROADMAP 的驗收條件。
   - 執行 `ruff check`、`ruff format --check`、`mypy src`、完整 `pytest --cov`，**全部必須通過**。
   - 不可刪除、skip 或弱化既有測試來換取通過（外部工具不存在時的 `requires` skip 除外）。
6. **測試報告**：產生 `docs/test-reports/<YYYY-MM-DD>_<TaskID>.md`（格式見 `docs/04-TESTING.md` §4）並更新 `INDEX.md`。
   T005 完成前請手動依格式撰寫；完成後使用 `python scripts/make_test_report.py`。
7. **更新文件**：
   - `docs/03-ROADMAP.md`：狀態改為 `[x]`（或 `[~]` / `[!]`）。
   - `docs/01-PROJECT-CHARTER.md` 功能對照表：更新相關功能狀態（✅/🟨）。
   - `docs/PROGRESS.md`：最上方新增一列（日期、工作、結果、測試數、報告連結、備註）。
   - 若發現新的必要工作或遺漏的原生軟體功能，追加到 ROADMAP 適當 Phase 尾端並註明「（由 T0xx 執行時新增）」。
8. **提交與推送**：
   - Commit 訊息：`T0xx: <英文簡述>`，內文列出變更重點與測試結果摘要。
   - `git push origin main`；網路失敗時以 2s/4s/8s/16s 退避重試最多 4 次。
   - 若 push 被拒（遠端有新 commit）：`git pull --rebase origin main`，重跑測試後再推。

## 失敗處理

- **測試無法通過且本次無法修好**：不要推送壞掉的程式碼。`git stash`/捨棄程式變更，只把 ROADMAP 標為 `[!]`（附原因與已嘗試的做法），
  在 PROGRESS.md 記錄，並寫一份標示「失敗」的測試報告，然後推送這些文件變更。下次執行會自動挑下一個可做的工作。
- **部分完成但程式穩定**（測試全綠）：可推送，ROADMAP 標 `[~]` 並在項目下註明剩餘內容，下次優先接續。
- **外部工具缺少**（LibreOffice 以外的 Tesseract、ODA 等）：實作偵測與優雅降級，測試以 `requires` marker skip，工作仍可標 `[x]`，
  並在報告「跳過的測試」中說明。

## 程式規範

- Python ≥ 3.11，完整型別標註，`mypy src` 須通過。
- 公開 API 需 docstring（英文）。
- 不在測試中存取網路；樣本由 `tests/fixtures/generate.py` 產生。
- 不提交大型二進位檔（> 200 KB）；`tests/fixtures/generated/` 列入 `.gitignore`。
- 每次執行結束時工作目錄必須乾淨（全部 commit 或捨棄）。
