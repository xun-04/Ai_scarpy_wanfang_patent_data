import sys
import tkinter as tk
from tkinter import ttk


def show_gui():
    """显示 GUI 窗口，返回 (year, mode)。
    mode: 'search' | 'enrich' | 'full'
    """
    root = tk.Tk()
    root.title("境外专利爬取工具")
    root.geometry("360x280")
    root.resizable(False, False)

    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="境外专利爬取 + Espacenet 分类号补充",
              font=("Microsoft YaHei", 11, "bold")).pack(pady=(0, 15))

    # 年份
    year_frame = ttk.Frame(frame)
    year_frame.pack(fill="x", pady=5)
    ttk.Label(year_frame, text="年份：", font=("Microsoft YaHei", 10)).pack(side="left")
    year_list = [str(y) for y in range(2026, 1899, -1)]
    year_combo = ttk.Combobox(year_frame, values=year_list, state="readonly", width=10)
    year_combo.pack(side="left", padx=10)
    year_combo.set(year_list[0])

    # 模式
    ttk.Label(frame, text="运行模式：", font=("Microsoft YaHei", 10)).pack(anchor="w", pady=(15, 5))
    mode_var = tk.StringVar(value="full")

    modes = [
        ("仅搜索爬取（Phase 1）——从万方获取专利列表", "search"),
        ("仅分类号补充（Phase 2）——从 Espacenet 获取 IPC", "enrich"),
        ("完整流程（推荐）——先搜索再补充", "full"),
    ]
    for text, value in modes:
        ttk.Radiobutton(frame, text=text, variable=mode_var, value=value).pack(anchor="w", padx=10)

    result = {"year": None, "mode": None}

    def on_start():
        y = year_combo.get()
        if not y:
            tk.messagebox.showwarning("提示", "请选择年份")
            return
        result["year"] = y
        result["mode"] = mode_var.get()
        root.destroy()

    ttk.Button(frame, text="开始运行", command=on_start).pack(pady=15)

    root.mainloop()
    return result["year"], result["mode"]


def get_countries_to_crawl(nation=None):
    from Ai_scrapy import COUNTRY_NAME_TO_CODE

    if nation:
        code = COUNTRY_NAME_TO_CODE.get(nation)
        if not code:
            print(f"ERROR: No country code mapping for '{nation}'")
            sys.exit(1)
        return [(nation, code)]
    return list(COUNTRY_NAME_TO_CODE.items())
