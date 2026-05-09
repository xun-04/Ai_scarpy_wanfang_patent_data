import os
import sys
import time
import random
import json

# ===================== 核心配置 =====================
TARGET_CLASS_CODE = "B64U10/10"
RESULT_DIR = "./result/data"
CHECKPOINT_FILE = os.path.join(RESULT_DIR, "checkpoint.json")
COOKIE_FILE = os.path.join(RESULT_DIR, "cookies.json")

PAGE_SIZE = 50
REQUEST_DELAY_MIN = 2
REQUEST_DELAY_MAX = 5
COOLDOWN_EVERY = 20
COOLDOWN_SECONDS = 3
AUTO_SAVE_EVERY = 5
MAX_API_PAGES = 121

# 半年拆分：避免单次查询超过 121 页 API 上限，可根据需要调整
HALF_YEAR_PERIODS = [
    ("1-6月",  "01-01", "06-30"),
    ("7-12月", "07-01", "12-31"),
]


def get_result_path(year):
    """每年一个 Excel 文件"""
    return os.path.join(RESULT_DIR, f"境外专利结果_{year}.xlsx")


def ensure_dir():
    os.makedirs(RESULT_DIR, exist_ok=True)


# ===================== 国家名称 → 代码映射 =====================
COUNTRY_NAME_TO_CODE = {
    "美国": "US", "欧洲专利局": "EP", "法国": "FR", "德国": "DE",
    "世界知识产权组": "WO", "英国": "GB", "日本": "JP", "韩国": "KR",
    "加拿大": "CA", "俄罗斯": "RU", "澳大利亚": "AU", "巴西": "BR",
    "西班牙": "ES", "以色列": "IL", "奥地利": "AT", "意大利": "IT",
    "荷兰": "NL", "比利时": "BE", "印度": "IN", "瑞典": "SE",
    "瑞士": "CH", "波兰": "PL", "中国台湾": "TW", "丹麦": "DK",
    "挪威": "NO", "新加坡": "SG", "墨西哥": "MX", "芬兰": "FI",
    "南非": "ZA", "葡萄牙": "PT", "中国香港": "HK", "欧亚专利局": "EA",
    "土耳其": "TR", "新西兰": "NZ", "匈牙利": "HU", "卢森堡": "LU",
    "捷克": "CZ", "希腊": "GR", "乌克兰": "UA", "阿根廷": "AR",
    "马来西亚": "MY", "斯洛文尼亚": "SI", "智利": "CL", "菲律宾": "PH",
    "克罗地亚": "HR", "摩洛哥": "MA", "沙特阿拉伯": "SA", "保加利亚": "BG",
    "罗马尼亚": "RO",
}


# ===================== HTTP 配置 =====================
HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Content-Type": "application/grpc-web+proto",
    "Origin": "https://s.wanfangdata.com.cn",
    "Referer": "https://s.wanfangdata.com.cn/advanced-search/patent",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
}

SEARCH_URL = "https://s.wanfangdata.com.cn/SearchService.SearchService/search"
CAPTCHA_KEYWORDS = ["滑块", "验证", "captcha", "verify", "滑块验证", "请完成安全验证"]

# ===================== Espacenet 配置 =====================
ESPACENET_PROFILE_DIR = os.path.join(RESULT_DIR, "browser_profile")
ESPACENET_ENRICH_DELAY = (0.2, 0.4)   # 请求间隔（秒），浏览器内调 API 无需大延迟
ESPACENET_ENRICH_SAVE_EVERY = 20      # 每 N 条保存一次
ENRICH_CHECKPOINT_FILE = os.path.join(RESULT_DIR, "checkpoint_enrich.json")

# ===================== 子模块导入（常量已就绪） =====================
from ai_scrapy.proto_utils import (build_search_request, _parse_search_response,
    SearchError, parse_patent_item)
from ai_scrapy.api_client import (search_with_retry, save_cookies_to_file,
    get_session, open_login_browser, _open_captcha_browser)
from ai_scrapy.espacenet import EspacenetClient
from ai_scrapy.persistence import (load_existing_data, save_result, load_checkpoint,
    save_checkpoint, clear_checkpoint, repair_checkpoint_from_excel,
    _save_enriched_excel, _load_enrich_checkpoint, _save_enrich_checkpoint,
    _clear_enrich_checkpoint)
from ai_scrapy.gui_app import show_gui, get_countries_to_crawl


# ===================== 主爬取逻辑 =====================
def crawl(nation, year, all_patients, start_page, end_page, start_item_index, global_idx,
          search_word, year_str, country_code, country_index=None, sheet_name=None):
    """使用 API 翻页爬取，返回 (更新后的 all_patients, global_idx, 是否正常完成)"""
    first_page_total = None
    page = start_page

    while page <= end_page:
        print(f"\n{'=' * 50}")
        print(f"Page {page} (idx={global_idx})")
        print(f"{'=' * 50}")

        try:
            total, items = search_with_retry(
                search_word, page=page, page_size=PAGE_SIZE,
                year=year_str, country_code=country_code
            )
        except SearchError as e:
            print(f"  ERROR: {e}")
            save_result(all_patients, year, sheet_name)
            save_checkpoint(nation, year, page, 0, global_idx, country_index, sheet_name=sheet_name)
            return all_patients, global_idx, False

        # 第一次请求获取总数
        if first_page_total is None:
            first_page_total = total
            if total == 0:
                print(f"  0 results, skip.")
                return all_patients, global_idx, True
            actual_end = min(end_page, MAX_API_PAGES,
                             (total + PAGE_SIZE - 1) // PAGE_SIZE)
            print(f"  total={total}, actual_end_page={actual_end}")
            if end_page is None:
                end_page = actual_end
            if page > actual_end:
                print(f"  Start page {page} exceeds max {actual_end}, nothing to do.")
                return all_patients, global_idx, True
        else:
            if total == 0:
                print(f"  Page {page}: API returned total=0, stopping (API limit).")
                save_result(all_patients, year, sheet_name)
                save_checkpoint(nation, year, page, 0, global_idx, country_index, sheet_name=sheet_name)
                break

        if not items:
            print(f"  Page {page}: no items, stopping.")
            break

        page_added = 0
        for i, item in enumerate(items):
            item_num = i + 1

            # 断点续爬：跳过已处理的项目
            if page == start_page and item_num <= start_item_index:
                continue

            patent = parse_patent_item(
                item, nation, year, TARGET_CLASS_CODE,
                page, item_num, global_idx
            )
            all_patients.append(patent)
            global_idx += 1
            page_added += 1

        print(f"    +{page_added} | total collected: {len(all_patients)}")

        # 自动保存
        if page_added > 0 and (page % AUTO_SAVE_EVERY == 0 or page >= end_page):
            save_result(all_patients, year, sheet_name)
            save_checkpoint(nation, year, page + 1, 0, global_idx, country_index, sheet_name=sheet_name)

        if page >= end_page:
            break

        page += 1

        # 冷却
        if (page - start_page) > 0 and (page - start_page) % COOLDOWN_EVERY == 0:
            print(f"    [Cooldown] {COOLDOWN_SECONDS}s...")
            time.sleep(COOLDOWN_SECONDS)
        else:
            time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))

    return all_patients, global_idx, True


# ===================== Espacenet 分类号补充（Phase 2） =====================
def enrich_classifications(year, sheet_name=None, max_rows=None):
    """Phase 2: 从 Espacenet 补充主分类号（IPC）和分类号（CPC）

    读取 Excel → 对分类号为空的行调 Espacenet → 更新并保存。

    Args:
        year: 年份
        sheet_name: 可选指定 sheet，默认处理所有半年 sheet
        max_rows: 可选，限制处理行数（测试用）
    """
    ensure_dir()
    filepath = get_result_path(year)
    if not os.path.exists(filepath):
        print(f"[Enrich] File not found: {filepath}")
        return

    # 确定要处理的 sheets
    if sheet_name:
        sheets_to_process = [(sheet_name, None, None)]
    else:
        sheets_to_process = [(sn, sd, ed) for sn, sd, ed in HALF_YEAR_PERIODS]

    # 加载断点
    ckpt = _load_enrich_checkpoint()
    resume_sheet = ckpt.get("sheet_name") if ckpt else None
    resume_row = ckpt.get("last_row", 0) if ckpt else 0
    sheet_completed = resume_row == -1  # -1 标记 sheet 已完成
    resume_row = max(0, resume_row)

    print(f"\n{'=' * 60}")
    print(f"Phase 2: Espacenet Classification Enrichment")
    print(f"Year: {year} | Sheets: {[s[0] for s in sheets_to_process]}")
    print(f"{'=' * 60}")

    import pandas as pd

    client = None
    try:
        client = EspacenetClient(headless=True)
        total_enriched = 0

        for sn, _sd, _ed in sheets_to_process:
            # 断点恢复：跳过已完成的 sheet
            if resume_sheet and sn != resume_sheet:
                if resume_sheet in [s[0] for s in sheets_to_process]:
                    continue
            if sheet_completed and resume_sheet == sn:
                print(f"  Sheet '{sn}' already completed, skipping.")
                sheet_completed = False
                resume_sheet = None
                continue
            resume_sheet = None

            print(f"\n--- Sheet: {sn} ---")
            try:
                df = pd.read_excel(filepath, sheet_name=sn)
            except ValueError:
                print(f"  Sheet '{sn}' not found, skipping.")
                continue

            if df.empty:
                print("  Empty sheet, skipping.")
                continue

            # 只处理有公开号且分类号为空的行
            need_enrich = (
                df["公开/公告号"].notna() & (df["公开/公告号"].astype(str).str.strip() != "") &
                (df["主分类号"].isna() | (df["主分类号"].astype(str).str.strip() == "")) &
                (df["分类号"].isna() | (df["分类号"].astype(str).str.strip() == ""))
            )
            enrich_indices = df[need_enrich].index.tolist()

            # 断点恢复
            if resume_row > 0:
                enrich_indices = [i for i in enrich_indices if i >= resume_row]
                print(f"  Resuming from row {resume_row}")
                resume_row = 0

            if max_rows is not None:
                enrich_indices = enrich_indices[:max_rows]

            print(f"  {len(enrich_indices)} rows to enrich")

            for idx in enrich_indices:
                row = df.iloc[idx]
                pub_num = str(row["公开/公告号"]).strip()
                current_main = str(row.get("主分类号", "")).strip()

                result = None
                try:
                    result = client.fetch_classifications(pub_num)
                except Exception as e:
                    err_name = type(e).__name__
                    print(f"  [Enrich] {err_name} for {pub_num}: {e}")
                    if "TargetClosed" in err_name or "closed" in str(e).lower():
                        print("  [Enrich] Browser closed, recreating client...")
                        try:
                            client.close()
                        except Exception:
                            pass
                        client = EspacenetClient(headless=True)
                        print("  [Enrich] Client recreated, retrying...")
                        try:
                            result = client.fetch_classifications(pub_num)
                        except Exception as e2:
                            print(f"  [Enrich] Retry also failed: {e2}")
                            result = None

                if result and result.get("ipc"):
                    df.at[idx, "主分类号"] = result["ipc"][0]
                    df.at[idx, "分类号"] = "；".join(result["ipc"])
                    total_enriched += 1
                elif result and result.get("cpc"):
                    df.at[idx, "主分类号"] = result["cpc"][0]
                    df.at[idx, "分类号"] = "；".join(result["cpc"])
                    total_enriched += 1
                elif result is not None:
                    print(f"no classification found for {pub_num}")

                # 延迟
                time.sleep(random.uniform(*ESPACENET_ENRICH_DELAY))

                # 定期保存
                if total_enriched > 0 and total_enriched % ESPACENET_ENRICH_SAVE_EVERY == 0:
                    _save_enriched_excel(df, filepath, sn)
                    _save_enrich_checkpoint(sn, idx + 1)
                    print(f"  [Saved] {total_enriched} enriched so far")

            # Sheet 完成，最终保存
            _save_enriched_excel(df, filepath, sn)
            _save_enrich_checkpoint(sn, -1)  # -1 表示该 sheet 完成
            print(f"  Sheet '{sn}' done: {total_enriched} enriched")

        print(f"\n{'=' * 60}")
        print(f"Enrichment complete! {total_enriched} total enriched.")
        print(f"File: {filepath}")
        print(f"{'=' * 60}")
        _clear_enrich_checkpoint()

    finally:
        if client:
            client.close()


# ===================== 入口 =====================
def main(nation=None, year=None, page_limit=None, mode="full"):
    """统一入口。
    mode: 'search' | 'enrich' | 'full'
    无参数 → GUI 选年份和模式
    """
    ensure_dir()

    # 1) 获取年份
    if year:
        pass
    else:
        year, mode = show_gui()
        if not year:
            print("Selection cancelled, exiting.")
            sys.exit(1)

    year_filter = year

    # Phase 2 only: 直接补充分类号
    if mode == "enrich":
        enrich_classifications(year_filter)
        return

    # Phase 1: 搜索爬取
    max_page_cap = page_limit or MAX_API_PAGES

    # 2) 确定国家列表
    countries = get_countries_to_crawl(nation)
    total_countries = len(countries)

    # 4) 从 checkpoint 确定断点（年份 + 半年 + 国家 + 页码）
    start_period_idx = 0
    start_country_idx = 0
    resume_page = 1
    resume_item_index = 0

    ckpt = load_checkpoint()
    if ckpt and ckpt.get("target_class_code") == TARGET_CLASS_CODE:
        ckpt_year = str(ckpt.get("year", ""))
        if ckpt_year == str(year_filter):
            ckpt_sheet = ckpt.get("sheet_name", "")
            # 定位半年索引
            ckpt_period_idx = 0
            for pi, (sn, _, _) in enumerate(HALF_YEAR_PERIODS):
                if sn == ckpt_sheet:
                    ckpt_period_idx = pi
                    break

            ckpt_country_idx = ckpt.get("country_index")
            ckpt_nation = ckpt.get("nation", "")
            if ckpt_country_idx is None:
                if ckpt_nation:
                    for idx, (name, _) in enumerate(countries):
                        if name == ckpt_nation:
                            ckpt_country_idx = idx
                            break
                    else:
                        ckpt_country_idx = 0
                else:
                    ckpt_country_idx = 0

            if ckpt_country_idx < total_countries:
                start_period_idx = ckpt_period_idx
                start_country_idx = ckpt_country_idx
                resume_page = ckpt.get("current_page", 1)
                resume_item_index = ckpt.get("next_item_index", 0)
                print(f"[Resume] {ckpt_sheet} | Country {start_country_idx + 1}/{total_countries} "
                      f"({ckpt_nation}), page={resume_page}")
        else:
            print(f"[Info] Year changed ({ckpt_year} -> {year_filter}), starting fresh.")
            clear_checkpoint()

    # 如果没有有效 checkpoint，尝试从 Excel 修复（H2 优先，因为它是最近被写入的）
    if start_period_idx == 0 and start_country_idx == 0 and resume_page == 1:
        for pi, (sn, _, _) in reversed(list(enumerate(HALF_YEAR_PERIODS))):
            repair_checkpoint_from_excel(year_filter, sn)
            ckpt = load_checkpoint()
            if ckpt and ckpt.get("target_class_code") == TARGET_CLASS_CODE:
                ckpt_year = str(ckpt.get("year", ""))
                if ckpt_year == str(year_filter):
                    start_period_idx = pi
                    start_country_idx = ckpt.get("country_index", 0) or 0
                    resume_page = ckpt.get("current_page", 1)
                    resume_item_index = ckpt.get("next_item_index", 0)
                    if start_country_idx < total_countries:
                        print(f"[Repair] {sn} | Country {start_country_idx + 1}/{total_countries} "
                              f"({ckpt.get('nation', '?')}), page={resume_page}")
                    break

    # 5) 遍历半年
    for period_idx in range(start_period_idx, len(HALF_YEAR_PERIODS)):
        sheet_name, start_mmdd, end_mmdd = HALF_YEAR_PERIODS[period_idx]

        print(f"\n{'=' * 60}")
        print(f"Period {period_idx + 1}/{len(HALF_YEAR_PERIODS)}: {sheet_name} "
              f"({year_filter}-{start_mmdd} ~ {year_filter}-{end_mmdd})")
        print(f"{'=' * 60}")

        # 构建带日期范围的检索式
        search_word = (f"(分类号:({TARGET_CLASS_CODE})) and "
                       f"公开日:[{year_filter}-{start_mmdd} TO {year_filter}-{end_mmdd}]")

        # 加载当前半年的已有数据
        all_patents = load_existing_data(year_filter, sheet_name)
        global_idx = len(all_patents) + 1

        # 如果 checkpoint 中有 sheet_name 且匹配当前半年，用 checkpoint 的 global_idx
        if ckpt and period_idx == start_period_idx:
            ckpt_global = ckpt.get("global_idx", 1)
            if ckpt_global > 1:
                global_idx = ckpt_global

        # 确定当前半年的起始国家
        if period_idx == start_period_idx:
            country_start_idx = start_country_idx
        else:
            country_start_idx = 0

        # 6) 遍历国家
        for country_idx in range(country_start_idx, total_countries):
            nation_name, country_code = countries[country_idx]

            print(f"\n{'#' * 60}")
            print(f"[{sheet_name}] Country {country_idx + 1}/{total_countries}: "
                  f"{nation_name} ({country_code})")
            print(f"Year: {year_filter} | Class: {TARGET_CLASS_CODE}")
            print(f"{'#' * 60}")

            # 确定当前国家的起始页
            if period_idx == start_period_idx and country_idx == start_country_idx:
                sp = resume_page
                si = resume_item_index
            else:
                sp = 1
                si = 0

            # 获取第一页
            print(f"\nSearching: {search_word}")
            try:
                total, items = search_with_retry(
                    search_word, page=1, page_size=PAGE_SIZE,
                    year=None, country_code=country_code
                )
            except SearchError as e:
                print(f"FATAL: Initial search failed for {nation_name}: {e}")
                save_result(all_patents, year_filter, sheet_name)
                save_checkpoint(nation_name, year_filter, 1, 0, global_idx,
                                country_idx, sheet_name=sheet_name)
                continue

            if total == 0:
                print(f"  No results for {nation_name}, skipping.")
                save_checkpoint(nation_name, year_filter, 1, 0, global_idx,
                                country_idx, sheet_name=sheet_name)
                continue

            max_page = min(max_page_cap, MAX_API_PAGES,
                           (total + PAGE_SIZE - 1) // PAGE_SIZE)
            print(f"  Total: {total}, max pages: {max_page}")

            if sp > max_page:
                print(f"  Already fully crawled (start_page={sp} > max_page={max_page}), skipping.")
                save_checkpoint(nation_name, year_filter, sp, 0, global_idx,
                                country_idx, sheet_name=sheet_name)
                continue

            # 解析第一页
            if sp == 1:
                page_added = 0
                for i, item in enumerate(items):
                    item_num = i + 1
                    if item_num <= si:
                        continue
                    patent = parse_patent_item(
                        item, nation_name, year_filter, TARGET_CLASS_CODE,
                        1, item_num, global_idx
                    )
                    all_patents.append(patent)
                    global_idx += 1
                    page_added += 1
                print(f"  Page 1: +{page_added} items, total collected: {len(all_patents)}")
                save_result(all_patents, year_filter, sheet_name)
                save_checkpoint(nation_name, year_filter, 2, 0, global_idx,
                                country_idx, sheet_name=sheet_name)

            # 翻页 2+
            actual_start_page = max(2, sp)
            country_completed = True
            if max_page >= actual_start_page:
                all_patents, global_idx, completed = crawl(
                    nation_name, year_filter,
                    all_patents,
                    start_page=actual_start_page,
                    end_page=max_page,
                    start_item_index=si if sp > 1 else 0,
                    global_idx=global_idx,
                    search_word=search_word,
                    year_str=None,
                    country_code=country_code,
                    country_index=country_idx,
                    sheet_name=sheet_name,
                )
                country_completed = completed

            if country_completed:
                save_checkpoint(nation_name, year_filter, max_page + 1, 0, global_idx,
                                country_idx, sheet_name=sheet_name)
            else:
                print(f"  [WARN] {nation_name} not fully completed, checkpoint kept at current page.")

        # 半年完成
        save_result(all_patents, year_filter, sheet_name)
        print(f"\n[{sheet_name}] Finished: {len(all_patents)} records")

    # 7) 最终清理
    clear_checkpoint()
    save_cookies_to_file()

    print(f"\n{'=' * 50}")
    print(f"Phase 1 complete! File: {get_result_path(year_filter)}")

    if mode == "full":
        print(f"\n{'=' * 50}")
        print("Auto-starting Phase 2: Espacenet classification enrichment...")
        enrich_classifications(year_filter)

    print(f"\n{'=' * 50}")
    print(f"All done! File: {get_result_path(year_filter)}")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Wanfang Patent Scraper + Espacenet Enrichment")
    parser.add_argument("--pages", type=int, default=None, help="Limit pages (for testing)")
    parser.add_argument("--nation", type=str, default=None, help="Crawl single nation (requires --year)")
    parser.add_argument("--year", type=str, default=None, help="Year (skips GUI if provided)")
    parser.add_argument("--enrich", action="store_true", help="Phase 2 only: enrich from Espacenet")
    parser.add_argument("--enrich-rows", type=int, default=None, help="Max rows to enrich (for testing)")
    parser.add_argument("--full", action="store_true", help="Phase 1 + Phase 2 (requires --year)")
    args = parser.parse_args()

    # CLI 模式：有 --year 时跳过 GUI
    if args.year:
        if args.enrich:
            enrich_classifications(args.year, max_rows=args.enrich_rows)
        elif args.full:
            main(nation=args.nation, year=args.year, page_limit=args.pages, mode="full")
        else:
            main(nation=args.nation, year=args.year, page_limit=args.pages, mode="search")
    else:
        # 无参数 → GUI 模式
        year, mode = show_gui()
        if not year:
            sys.exit(1)
        if mode == "enrich":
            enrich_classifications(year)
        else:
            main(nation=None, year=year, mode=mode)