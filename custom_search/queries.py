import json

import frappe
from frappe import scrub
from frappe.query_builder import Case, Criterion, DocType
from frappe.query_builder.functions import Concat, Length, Locate, Substring
from frappe.utils import nowdate
from pypika import Order


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def tokenized_item_query(
    doctype: str,
    txt: str,
    searchfield: str,
    start: int,
    page_len: int,
    filters: dict | str | None = None,
    as_dict: bool = False,
):
    """
    Copy of erpnext.controllers.queries.item_query with the txt-matching
    condition replaced by a tokenized (AND-of-words, OR-of-fields) match.
    """
    doctype = "Item"

    if isinstance(filters, str):
        filters = json.loads(filters)
    filters = filters or {}

    # --- original customer/supplier "Party Specific Item" handling ---
    if filters and isinstance(filters, dict):
        if filters.get("customer") or filters.get("supplier"):
            party_type = "Customer" if filters.get("customer") else "Supplier"
            party = filters.get("customer") or filters.get("supplier")
            group = "Customer Group" if filters.get("customer") else "Supplier Group"

            item_rules_list = frappe.get_all(
                "Party Specific Item",
                filters={"party": ["!=", party], "party_type": party_type},
                fields=["restrict_based_on", "based_on_value"],
            )
            party_group_rules_list = frappe.get_all(
                "Party Specific Item",
                filters={"party_type": group},
                fields=["party as party_group", "restrict_based_on", "based_on_value"],
            )
            current_party_group = frappe.get_value(party_type, party, frappe.scrub(group))
            for rule in party_group_rules_list:
                if current_party_group != rule.party_group:
                    item_rules_list.append(rule)

            filters_dict = {}
            for rule in item_rules_list:
                if rule["restrict_based_on"] == "Item":
                    rule["restrict_based_on"] = "name"
                filters_dict[rule.restrict_based_on] = []
            for rule in item_rules_list:
                filters_dict[rule.restrict_based_on].append(rule.based_on_value)

            for filter in filters_dict:
                filters[scrub(filter)] = ["not in", filters_dict[filter]]

            if filters.get("customer"):
                del filters["customer"]
            else:
                del filters["supplier"]
        else:
            filters.pop("customer", None)
            filters.pop("supplier", None)

    item = DocType(doctype)

    eol = item.end_of_life
    date_conditions = [eol > nowdate(), eol.isnull()]
    if frappe.db.db_type not in ["postgres"]:
        date_conditions.append(eol == "0000-00-00")
    date_condition = Criterion.any(date_conditions)

    meta = frappe.get_meta("Item", cached=True)
    searchfields = meta.get_search_fields()

    query_select = []
    extra_searchfields = [f for f in searchfields if f not in ["name", "description"]]
    for field in extra_searchfields:
        query_select.append(item[field])

    if "description" in searchfields:
        description_col = (
            Case()
            .when(Length(item.description) > 40, Concat(Substring(item.description, 1, 40), "..."))
            .else_(item.description)
        ).as_("description")
        query_select.append(description_col)

    fields_to_process = list(
        dict.fromkeys(
            searchfields
            + [f for f in [searchfield or "name", "item_code", "item_group", "item_name"] if f not in searchfields]
        )
    )

    db_fields = [f.fieldname for f in meta.fields] + ["name"]

    # --- tokenized matching: AND across words, OR across fields per word ---
    words = (txt or "").strip().split()
    if not words:
        words = [""]

    barcode_tbl = DocType("Item Barcode")
    include_description = frappe.db.estimate_count("Item") < 50000 and "description" not in fields_to_process

    word_conditions = []
    for word in words:
        word_str = f"%{word}%"
        per_word_conditions = []
        for fieldname in fields_to_process:
            if fieldname in db_fields:
                per_word_conditions.append(item[fieldname].like(word_str))

        barcode_subquery = (
            frappe.qb.from_(barcode_tbl).select(barcode_tbl.parent).where(barcode_tbl.barcode.like(word_str))
        )
        per_word_conditions.append(item.item_code.isin(barcode_subquery))

        if include_description:
            per_word_conditions.append(item.description.like(word_str))

        word_conditions.append(Criterion.any(per_word_conditions))

    txt_no_percent = (txt or "").replace("%", "")

    query = (
        frappe.get_query(doctype, filters=filters, ignore_permissions=False)
        .select(*query_select)
        .where(item.docstatus < 2)
        .where(item.disabled == 0)
        .where(item.has_variants == 0)
        .where(date_condition)
        .where(Criterion.all(word_conditions))
        .orderby(
            Case().when(Locate(txt_no_percent, item.name) > 0, Locate(txt_no_percent, item.name)).else_(99999)
        )
        .orderby(
            Case()
            .when(Locate(txt_no_percent, item.item_name) > 0, Locate(txt_no_percent, item.item_name))
            .else_(99999)
        )
        .orderby(item.idx, order=Order.desc)
        .orderby(item.name)
        .orderby(item.item_name)
        .limit(page_len)
        .offset(start)
    )

    return query.run(as_dict=as_dict)