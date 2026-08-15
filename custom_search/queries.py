import re
import frappe


@frappe.whitelist()
def tokenized_item_query(doctype, txt, searchfield, start, page_len, filters, as_dict=False):
    if isinstance(filters, str):
        filters = frappe.parse_json(filters)
    filters = filters or {}

    conditions = ""
    values = {"start": start, "page_len": page_len}


    for key, value in filters.items():
        if not re.match(r"^[a-zA-Z0-9_]+$", key):
            continue
        param = f"f_{key}"
        conditions += f" AND item.`{key}` = %({param})s"
        values[param] = value

    words = (txt or "").strip().split()
    for i, w in enumerate(words):
        conditions += f" AND (item.item_code LIKE %(w{i})s OR item.item_name LIKE %(w{i})s)"
        values[f"w{i}"] = f"%{w}%"

    return frappe.db.sql(f"""
        SELECT item.name, item.item_name
        FROM `tabItem` item
        WHERE item.disabled = 0 {conditions}
        ORDER BY item.item_name
        LIMIT %(page_len)s OFFSET %(start)s
    """, values, as_dict=as_dict)
