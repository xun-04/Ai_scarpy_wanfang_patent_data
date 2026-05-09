import os
import json
import time
import pandas as pd


def load_existing_data(year, sheet_name):
    from Ai_scrapy import get_result_path, ensure_dir
    ensure_dir()
    filepath = get_result_path(year)
    if os.path.exists(filepath):
        try:
            df = pd.read_excel(filepath, sheet_name=sheet_name)
            records = df.to_dict("records")
            print(f"[Load] {len(records)} existing records from {filepath} / {sheet_name}")
            return records
        except ValueError:
            return []
        except Exception as e:
            print(f"[WARN] Read existing file failed: {e}")
            return []
    return []


def save_result(all_patents, year, sheet_name):
    from Ai_scrapy import get_result_path, ensure_dir
    ensure_dir()
    if not all_patents:
        return
    filepath = get_result_path(year)
    df_new = pd.DataFrame(all_patents)

    if os.path.exists(filepath):
        with pd.ExcelWriter(filepath, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            df_new.to_excel(writer, sheet_name=sheet_name, index=False)
    else:
        with pd.ExcelWriter(filepath, engine="openpyxl", mode="w") as writer:
            df_new.to_excel(writer, sheet_name=sheet_name, index=False)
    print(f"[Save] {len(all_patents)} records -> {filepath} / {sheet_name}")


def load_checkpoint():
    from Ai_scrapy import CHECKPOINT_FILE, ensure_dir
    ensure_dir()
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                ckpt = json.load(f)
            print(f"[Checkpoint] Loaded: {ckpt}")
            return ckpt
        except Exception as e:
            print(f"[WARN] Checkpoint read failed: {e}")
    return None


def save_checkpoint(nation, year, current_page, next_item_index, global_idx, country_index=None, sheet_name=None):
    from Ai_scrapy import CHECKPOINT_FILE, TARGET_CLASS_CODE, ensure_dir
    ensure_dir()
    ckpt = {
        "nation": nation,
        "year": year,
        "target_class_code": TARGET_CLASS_CODE,
        "current_page": int(current_page),
        "next_item_index": int(next_item_index),
        "global_idx": int(global_idx),
        "country_index": country_index,
        "sheet_name": sheet_name,
        "update_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(ckpt, f, ensure_ascii=False, indent=2)
    print(f"[Checkpoint] Saved: page={current_page}, idx={global_idx}, country_idx={country_index}, sheet={sheet_name}")


def clear_checkpoint():
    from Ai_scrapy import CHECKPOINT_FILE
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)
        print("[Checkpoint] Cleared")


def repair_checkpoint_from_excel(year, sheet_name, nation=None):
    from Ai_scrapy import (get_result_path, CHECKPOINT_FILE, TARGET_CLASS_CODE,
                            PAGE_SIZE, COUNTRY_NAME_TO_CODE, ensure_dir)
    ensure_dir()
    filepath = get_result_path(year)
    if not os.path.exists(filepath):
        return
    try:
        df = pd.read_excel(filepath, sheet_name=sheet_name)
        if df.empty or "状态" not in df.columns:
            return
        if "国家" not in df.columns or "年份" not in df.columns:
            return

        mask = (df["状态"] == "成功") & (df["年份"].astype(str) == str(year))
        if nation:
            mask = mask & (df["国家"].astype(str) == str(nation))
        success_df = df[mask].copy()
        if success_df.empty:
            return

        last_success = success_df.iloc[-1]
        nation_name = str(last_success["国家"])
        page_num = int(last_success["页码"])
        item_num = int(last_success["页内序号"])
        global_idx = int(last_success["全局序号"])

        country_list = list(COUNTRY_NAME_TO_CODE.keys())
        try:
            country_index = country_list.index(nation_name)
        except ValueError:
            country_index = 0

        next_item_index = item_num
        if item_num >= PAGE_SIZE:
            page_num += 1
            next_item_index = 0

        ckpt = {
            "nation": nation_name,
            "year": year,
            "target_class_code": TARGET_CLASS_CODE,
            "current_page": page_num,
            "next_item_index": next_item_index,
            "global_idx": global_idx + 1,
            "country_index": country_index,
            "sheet_name": sheet_name,
            "update_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
            json.dump(ckpt, f, ensure_ascii=False, indent=2)
        print(f"[Checkpoint] Repaired from Excel: {ckpt}")
    except ValueError:
        return
    except Exception as e:
        print(f"[WARN] Repair checkpoint failed: {e}")


def _save_enriched_excel(df, filepath, sheet_name):
    if os.path.exists(filepath):
        with pd.ExcelWriter(filepath, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            df.to_excel(writer, sheet_name=sheet_name, index=False)
    else:
        with pd.ExcelWriter(filepath, engine="openpyxl", mode="w") as writer:
            df.to_excel(writer, sheet_name=sheet_name, index=False)


def _load_enrich_checkpoint():
    from Ai_scrapy import ENRICH_CHECKPOINT_FILE
    if os.path.exists(ENRICH_CHECKPOINT_FILE):
        try:
            with open(ENRICH_CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_enrich_checkpoint(sheet_name, last_row):
    from Ai_scrapy import ENRICH_CHECKPOINT_FILE, RESULT_DIR
    os.makedirs(RESULT_DIR, exist_ok=True)
    ckpt = {
        "sheet_name": sheet_name,
        "last_row": last_row,
        "update_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(ENRICH_CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(ckpt, f, ensure_ascii=False, indent=2)


def _clear_enrich_checkpoint():
    from Ai_scrapy import ENRICH_CHECKPOINT_FILE
    if os.path.exists(ENRICH_CHECKPOINT_FILE):
        os.remove(ENRICH_CHECKPOINT_FILE)
