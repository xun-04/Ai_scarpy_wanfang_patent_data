import struct
import re
import time
import blackboxprotobuf as bbpb


# ===================== Protobuf 编码 =====================
def _encode_varint(val):
    buf = []
    while val > 0x7f:
        buf.append((val & 0x7f) | 0x80)
        val >>= 7
    buf.append(val)
    return bytes(buf)


def _encode_ld(tag_byte, data):
    return bytes([tag_byte]) + _encode_varint(len(data)) + data


def build_search_request(search_word, page=1, page_size=20, year=None, country_code=None):
    inner = b""
    inner += _encode_ld(0x0A, b"patent")
    inner += _encode_ld(0x12, search_word.encode("utf-8"))

    # Type=Patent
    s = _encode_ld(0x0A, b"Type") + _encode_ld(0x12, b"Patent")
    inner += _encode_ld(0x22, s)

    # ObtainWay=FOREIGN
    s = _encode_ld(0x0A, b"ObtainWay") + _encode_ld(0x12, b"FOREIGN")
    inner += _encode_ld(0x22, s)

    # 国家筛选
    if country_code:
        s = _encode_ld(0x0A, b"CountryOrganization") + _encode_ld(0x12, country_code.encode("utf-8"))
        inner += _encode_ld(0x22, s)

    # 年份筛选
    if year:
        s = _encode_ld(0x0A, b"PublishYear") + _encode_ld(0x12, year.encode("utf-8"))
        inner += _encode_ld(0x22, s)

    inner += bytes([0x28]) + _encode_varint(page)
    inner += bytes([0x30]) + _encode_varint(page_size)
    inner += _encode_ld(0x42, b"\x00")
    inner += bytes([0x48, 0x01])
    inner += _encode_ld(0x62, b"pc")
    inner += _encode_ld(0x6A, b"search")

    outer = b""
    outer += _encode_ld(0x0A, inner)
    outer += bytes([0x10, 0x03])
    outer += _encode_ld(0x22, b"AI_READ")
    outer += _encode_ld(0x22, b"AI_EXTRACT")

    return outer


# ===================== API 响应解析 =====================
class SearchError(Exception):
    def __init__(self, msg, retryable=True):
        super().__init__(msg)
        self.retryable = retryable


def _parse_search_response(proto):
    """解析搜索响应的外层 protobuf：提取 total（field 3）和 items（field 4）"""
    total = 0
    items = []
    pos = 0
    n = len(proto)

    while pos < n:
        tag_byte = proto[pos]
        field_num = tag_byte >> 3
        wire_type = tag_byte & 0x07
        pos += 1

        if wire_type == 0:
            val = 0
            shift = 0
            while pos < n:
                b = proto[pos]; pos += 1
                val |= (b & 0x7f) << shift
                if not (b & 0x80):
                    break
                shift += 7
            if field_num == 3:
                total = val

        elif wire_type == 2:
            length = 0; shift = 0
            while pos < n:
                b = proto[pos]; pos += 1
                length |= (b & 0x7f) << shift
                if not (b & 0x80):
                    break
                shift += 7

            if field_num == 4 and pos + length <= n:
                item_bytes = proto[pos:pos + length]
                try:
                    item, _ = bbpb.decode_message(item_bytes)
                    item["_raw_bytes"] = item_bytes
                    items.append(item)
                except Exception:
                    pass
            pos += length

        elif wire_type == 5:
            pos += 4
        elif wire_type == 1:
            pos += 8
        else:
            break

    return total, items


# ===================== 数据解析 =====================
# 专利详情 protobuf 字段号对照（手动 wire format 解析验证）：
#   field 1  — 组合 ID（格式: ZL_申请号_公开号）   field 2  — 专利名称
#   field 3  — 申请/专利号                          field 4  — 公开/公告号
#   field 5  — 发明/设计人                          field 7  — 申请/专利权人
#   field 12 — 专利摘要                             field 14 — 万方内部标记（"WF"）
#   field 15 — 申请日期                             field 16 — 公开/公告日
#   field 17 — 国别省市代码                         field 24 — 语言代码
#   field 53 — 申请号（冗余，同 field 3 去年份前缀）  field 56 — 公开号（冗余，同 field 4）
#   field 119— 详情包装字段（内层 protobuf，包含上述所有字段）
# 注意：该搜索 API 不返回 IPC 分类号，需要在详情页或另一端点获取

def _b2s(val):
    """将 protobuf 值转为字符串，支持 bytes/dict/嵌套递归"""
    if isinstance(val, bytes):
        try:
            return val.decode("utf-8", errors="replace")
        except Exception:
            return str(val)
    if isinstance(val, dict):
        for v in val.values():
            s = _b2s(v)
            if s and not s.startswith("\n") and not s.startswith("\x12"):
                return s
        # fallback: 所有 value 都被过滤时取第一个非空值，避免丢数据
        for v in val.values():
            s = _b2s(v)
            if s:
                return s
        return ""
    return str(val) if val else ""


def _b2s_list(arr, sep="; "):
    """将值转为字符串，对 list 用分隔符拼接；处理 bytes→codepoint 的特殊编码"""
    if not arr:
        return ""
    if isinstance(arr, bytes):
        return _b2s(arr)
    if isinstance(arr, dict):
        return sep.join(_b2s(v) for v in arr.values() if _b2s(v))
    # 整数列表 → 可能是 codepoint 编码的字符串
    if arr and all(isinstance(v, int) for v in arr):
        chars = ''.join(chr(v) for v in arr if 0 < v < 0x110000)
        if '\x00' in chars:
            parts = [c.strip() for c in chars.split('\x00') if c.strip()]
            if parts:
                return '; '.join(parts)
        if chars.strip():
            return chars
        return ""
    return sep.join(_b2s(v) for v in arr)


def _try_decode_nested(val):
    """尝试将 bytes 值解码为嵌套 protobuf dict"""
    if isinstance(val, dict) and val:
        return val
    if isinstance(val, bytes) and val:
        try:
            decoded, _ = bbpb.decode_message(val)
            if isinstance(decoded, dict) and decoded:
                return decoded
        except Exception:
            pass
    return None


def _extract_field_raw(data, target_field):
    """从原始 protobuf bytes 中提取指定字段号的原始字节（跳过 blackboxprotobuf 直接读）"""
    pos = 0
    n = len(data)
    while pos < n:
        tag_val = 0; shift = 0
        while pos < n:
            b = data[pos]; pos += 1
            tag_val |= (b & 0x7f) << shift
            if not (b & 0x80):
                break
            shift += 7
        field_num = tag_val >> 3
        wire_type = tag_val & 0x07
        if wire_type == 0:
            while pos < n and data[pos] & 0x80:
                pos += 1
            pos += 1
        elif wire_type == 2:
            length = 0; shift = 0
            while pos < n:
                b = data[pos]; pos += 1
                length |= (b & 0x7f) << shift
                if not (b & 0x80): break
                shift += 7
            if field_num == target_field and pos + length <= n:
                return data[pos:pos + length]
            pos += length
        elif wire_type == 5:
            pos += 4
        elif wire_type == 1:
            pos += 8
        else:
            break
    return None


def _manual_proto_to_dict(data, depth=0):
    """手动解析 protobuf wire format → dict（blackboxprotobuf 解析失败时的 fallback）。
    depth 限制递归深度，避免把不可解析的二进制字符串无限嵌套。"""
    result = {}
    pos = 0
    n = len(data)

    while pos < n:
        tag_val = 0; tag_shift = 0
        while pos < n:
            b = data[pos]; pos += 1
            tag_val |= (b & 0x7f) << tag_shift
            if not (b & 0x80):
                break
            tag_shift += 7
        field_num = tag_val >> 3
        wire_type = tag_val & 0x07

        if wire_type == 0:
            val = 0; shift = 0
            while pos < n:
                b = data[pos]; pos += 1
                val |= (b & 0x7f) << shift
                if not (b & 0x80):
                    break
                shift += 7
            key = str(field_num)
            if key not in result:
                result[key] = val
            elif isinstance(result[key], list):
                result[key].append(val)
            else:
                result[key] = [result[key], val]

        elif wire_type == 2:
            length = 0; shift = 0
            while pos < n:
                b = data[pos]; pos += 1
                length |= (b & 0x7f) << shift
                if not (b & 0x80):
                    break
                shift += 7

            if pos + length <= n:
                val_bytes = data[pos:pos + length]
                pos += length

                val_resolved = None
                if depth < 3 and len(val_bytes) > 2:
                    try:
                        nested = _manual_proto_to_dict(val_bytes, depth + 1)
                        if nested and len(nested) >= 1:
                            # 启发式：判断嵌套解析结果更像真实 protobuf 还是误解析的字符串
                            nested_digit_keys = sum(1 for k in nested if k.isdigit())
                            # 超过一半的 key 是数字且总 key 数 >= 2 → 很可能是真实嵌套
                            if nested_digit_keys >= 2 and nested_digit_keys >= len(nested) * 0.5:
                                val_resolved = nested
                            else:
                                # 检查是否有控制字符（真实 protobuf 嵌套通常会产生控制字符）
                                has_ctrl = False
                                for nv in nested.values():
                                    if isinstance(nv, str) and nv and nv[0] < ' ':
                                        has_ctrl = True
                                        break
                                if has_ctrl:
                                    val_resolved = nested
                    except Exception:
                        pass

                if val_resolved is None:
                    try:
                        val_resolved = val_bytes.decode("utf-8", errors="replace")
                    except Exception:
                        val_resolved = val_bytes

                key = str(field_num)
                if key not in result:
                    result[key] = val_resolved
                elif isinstance(result[key], list):
                    result[key].append(val_resolved)
                else:
                    result[key] = [result[key], val_resolved]
            else:
                break

        elif wire_type == 5:
            pos += 4
        elif wire_type == 1:
            pos += 8
        else:
            break

    return result


def _parse_field6(val6):
    """解析 field 6 复合字段，分隔符为 � 或 \x00。
    从拼接字符串中提取：公开日(→16)、国别代码(→17)、主分类号(→27)、IPC(→53)

    数据结构（4个 field 由 � 或 \x00 分隔）：
      [0] = 日期（2024-01-15）
      [1] = 国家代码（US）或语言代码（eng）
      [2] = 主分类号（C07J 1/00(甾族化合物)）
      [3+] = IPC 分类号列表（75/00C09B；1/32C09B；...）
    """
    extra = {}
    if isinstance(val6, list):
        first = val6[0] if val6 else ""
        raw = str(first) if isinstance(first, str) else ""
    elif isinstance(val6, str):
        raw = val6
    elif isinstance(val6, dict):
        raw = ""
    else:
        raw = str(val6) if val6 else ""

    if not raw:
        return extra

    sep = chr(0xFFFD)
    if '\x00' in raw:
        sep = '\x00'
    parts = raw.split(sep)

    cleaned = []
    for p in parts:
        while p and p[0] < ' ' and p[0] != '\n':
            p = p[1:]
        cleaned.append(p)
    cleaned = [c for c in cleaned if c]

    if len(cleaned) >= 1:
        date_match = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', cleaned[0])
        if not date_match:
            date_match = re.search(r'(\d{2})-(\d{1,2})-(\d{1,2})', cleaned[0])
        if date_match:
            y, m, d = date_match.groups()
            if len(y) == 2:
                y = '20' + y
            extra["16"] = f"{y}-{int(m):02d}-{int(d):02d} 00:00:00"

    # Index 1 = 国家代码（2位大写字母）或语言代码（3位小写字母）
    if len(cleaned) > 1:
        country = cleaned[1].strip()
        if len(country) == 2 and country.isalpha() and country.isupper():
            extra["17"] = country
        elif len(country) == 3 and country.isalpha() and country.islower():
            extra["24"] = country

    # Index 2 = 主分类号（如 C07J 1/00(甾族化合物)）
    if len(cleaned) > 2:
        main_raw = cleaned[2].strip()
        if main_raw:
            extra["27"] = main_raw

    # Index 3+ = IPC 列表（以分号分隔），收集全部分类号用 ； 拼接
    if len(cleaned) > 3:
        ipc_list = []
        for seg in cleaned[3:]:
            seg = seg.strip()
            if not seg:
                continue
            ipc_parts = re.split(r'；|;', seg)
            for ipc in ipc_parts:
                ipc = ipc.strip()
                if not ipc:
                    continue
                ipc_match = re.match(r'^([A-Z]?\w*\s*\d+\s*/\s*\d+\w*)', ipc)
                if ipc_match:
                    ipc_list.append(ipc_match.group(1))
                elif ipc:
                    ipc_list.append(ipc)
        if ipc_list:
            extra["53"] = "；".join(ipc_list)

    return extra


def _sanitize_detail(detail):
    """清理 detail 中字符串值的控制字符"""
    for key in list(detail.keys()):
        val = detail[key]
        if isinstance(val, str):
            cleaned = val.strip("\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0b\x0c\x0e\x0f"
                                "\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f")
            if cleaned != val:
                detail[key] = cleaned
    return detail


# 专利详情 protobuf 中已知的字符串字段号（避免 bbpb 误判为嵌套 dict/数字）
_PATENT_STRING_FIELDS = frozenset({2, 3, 4, 5, 6, 7, 12, 15, 16, 17, 24, 27, 53, 56})


def _parse_proto_fields(data, known_strings=None):
    """手动解析 protobuf wire format → dict，不依赖 blackboxprotobuf。

    对 wire_type=2 的已知字段强制按 UTF-8 字符串解码，
    未知字段先尝试干净 UTF-8，失败则保留 bytes。
    known_strings 默认 = _PATENT_STRING_FIELDS。
    """
    if known_strings is None:
        known_strings = _PATENT_STRING_FIELDS

    result = {}
    pos = 0
    n = len(data)

    while pos < n:
        tag = 0; shift = 0
        while pos < n:
            b = data[pos]; pos += 1
            tag |= (b & 0x7f) << shift
            if not (b & 0x80): break
            shift += 7

        field_num = tag >> 3
        wire_type = tag & 0x07
        key = str(field_num)

        if wire_type == 0:  # varint
            val = 0; shift = 0
            while pos < n:
                b = data[pos]; pos += 1
                val |= (b & 0x7f) << shift
                if not (b & 0x80): break
                shift += 7
            _merge_field(result, key, val)

        elif wire_type == 2:  # length-delimited
            length = 0; shift = 0
            while pos < n:
                b = data[pos]; pos += 1
                length |= (b & 0x7f) << shift
                if not (b & 0x80): break
                shift += 7
            if pos + length > n:
                break
            chunk = data[pos:pos + length]
            pos += length

            if field_num in known_strings:
                val = chunk.decode("utf-8", errors="replace")
            else:
                try:
                    val = chunk.decode("utf-8")
                except UnicodeDecodeError:
                    val = chunk
            _merge_field(result, key, val)

        elif wire_type == 5:  # 32-bit
            if pos + 4 <= n:
                _merge_field(result, key, struct.unpack("<I", data[pos:pos+4])[0])
                pos += 4
        elif wire_type == 1:  # 64-bit
            if pos + 8 <= n:
                _merge_field(result, key, struct.unpack("<Q", data[pos:pos+8])[0])
                pos += 8
        else:
            break

    return result


def _merge_field(result, key, value):
    """处理 repeated 字段：同一 key 出现多次时合并为 list。"""
    if key not in result:
        result[key] = value
    elif isinstance(result[key], list):
        result[key].append(value)
    else:
        result[key] = [result[key], value]


def _extract_detail(item):
    """从单条专利 item 中提取详情 dict（多层 fallback 解析 protobuf）。

    优先路径：从 _raw_bytes 手动提取 field 119 并解析 wire format（无 bbpb 依赖）。
    Fallback 1：blackboxprotobuf 解码 field 119。
    Fallback 2：手动解析 item["119"] bytes。
    Fallback 3：扫描其他字段。"""
    detail = None

    # Primary: manually parse field 119 from raw protobuf bytes
    raw_item = item.get("_raw_bytes")
    if raw_item:
        raw_119 = _extract_field_raw(raw_item, 119)
        if raw_119:
            detail = _parse_proto_fields(raw_119)
            if detail and any(k.isdigit() for k in detail):
                detail = _sanitize_detail(detail)
                if "6" in detail:
                    extra = _parse_field6(detail["6"])
                    for k, v in extra.items():
                        if k in ("27", "53"):
                            detail[k] = v
                        elif k not in detail or not str(detail.get(k, "")).strip(
                            "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0b\x0c\x0e\x0f"
                            "\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f"
                        ):
                            detail[k] = v
                return detail

    # Fallback 1: bbpb-decoded field 119
    detail = _try_decode_nested(item.get("119"))

    if not detail:
        # Fallback 2: manual parse field 119 bytes
        val_119 = item.get("119")
        if isinstance(val_119, bytes) and val_119:
            detail = _parse_proto_fields(val_119)

    if not detail:
        # Fallback 3: scan other fields for nested dict with field 2
        for key in sorted(item.keys(), key=lambda k: (0 if str(k).isdigit() else 1, k)):
            candidate = _try_decode_nested(item[key])
            if candidate and "2" in candidate:
                detail = candidate
                break

    if not detail:
        return None

    # Supplement from field 6 — always overwrite 27/53 from field 6
    if "6" in detail:
        extra = _parse_field6(detail["6"])
        for k, v in extra.items():
            if k in ("27", "53"):
                detail[k] = v
            elif k not in detail or not str(detail.get(k, "")).strip(
                "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0b\x0c\x0e\x0f"
                "\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f"
            ):
                detail[k] = v

    detail = _sanitize_detail(detail)
    return detail


def _is_numeric_val(v):
    """检查值是否实际上是一个数字（用于过滤被误解析为字符串的纯数字字段）"""
    if isinstance(v, int):
        return True
    if isinstance(v, str) and v.strip().isdigit():
        return True
    if isinstance(v, dict):
        for vv in v.values():
            if _is_numeric_val(vv):
                return True
    return False


def _recover_field_from_raw_bytes(item, field_num):
    """当 bbpb 误解析某字段时，从原始 bytes 中提取正确的值"""
    raw_119 = None
    val_119 = item.get("119")
    if isinstance(val_119, bytes) and val_119:
        try:
            manual = _manual_proto_to_dict(val_119)
            if manual and str(field_num) in manual:
                return manual[str(field_num)]
        except Exception:
            pass
    if "_raw_bytes" in item:
        raw_119 = _extract_field_raw(item["_raw_bytes"], 119)
    elif isinstance(val_119, bytes):
        raw_119 = val_119
    if raw_119:
        raw_f = _extract_field_raw(raw_119, field_num)
        if raw_f:
            try:
                return raw_f.decode("utf-8", errors="replace").strip("\x00")
            except Exception:
                pass
    return None


def parse_patent_item(item, nation, year_str, class_code, page_num, item_num, global_idx):
    """解析单条专利，返回与 chrome.py 兼容的字段 dict"""
    from Ai_scrapy import TARGET_CLASS_CODE
    detail = _extract_detail(item)
    if detail is None:
        return {
            "国家": nation, "年份": year_str, "检索分类号": class_code,
            "全局序号": global_idx, "页码": page_num, "页内序号": item_num,
            "专利名称": "", "状态": "失败：无法解析protobuf",
            "爬取时间": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    # 主分类号和分类号来自 Espacenet（Phase 2），Phase 1 留空

    patent_name = _b2s(detail.get("2", ""))

    applicant_raw = detail.get("7", "")

    patent = {
        "国家": nation,
        "年份": year_str,
        "检索分类号": class_code,
        "全局序号": global_idx,
        "页码": page_num,
        "页内序号": item_num,
        "专利名称": patent_name,
        "专利摘要": _b2s(detail.get("12", "")),
        "申请/专利号": _b2s(detail.get("3", "")),
        "申请日期": _b2s(detail.get("15", "")),
        "公开/公告号": _b2s(detail.get("4", "")),
        "公开/公告日": _b2s(detail.get("16", "")),
        "主分类号": "",   # Phase 2 从 Espacenet 填入
        "分类号": "",     # Phase 2 从 Espacenet 填入
        "申请/专利权人": (_b2s_list(applicant_raw)
                          if isinstance(applicant_raw, list)
                          else _b2s(applicant_raw)),
        "发明/设计人": _b2s_list(detail.get("5", [])),
        "国别省市代码": _b2s(detail.get("17", "")),
        "状态": "成功" if patent_name else "失败：无专利名称",
        "爬取时间": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    return patent
