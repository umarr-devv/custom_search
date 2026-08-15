__version__ = "1.0.1"

def _patch_item_query():
    import erpnext.controllers.queries as erp_queries
    from custom_search.queries import tokenized_item_query
    erp_queries.item_query = tokenized_item_query

_patch_item_query()
