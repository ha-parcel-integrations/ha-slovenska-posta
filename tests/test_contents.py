"""Tests for the contents helpers and the per-entry store."""

from unittest.mock import patch

from custom_components.slovenska_posta.contents import (
    CONTENTS_MAX_LEN,
    CONTENTS_STORE_MAX_CODES,
    ContentsStore,
    clean_contents,
    join_contents,
    normalize_code,
)


def test_clean_contents_trims_and_collapses_whitespace():
    assert clean_contents("  Running \n  shoes\t") == "Running shoes"


def test_clean_contents_says_nothing_for_blank_or_non_text():
    assert clean_contents("   ") is None
    assert clean_contents(None) is None
    assert clean_contents(5) is None


def test_clean_contents_drops_filler_and_caps_length():
    assert clean_contents("N/A", frozenset({"n/a"})) is None
    assert len(clean_contents("x" * 500)) == CONTENTS_MAX_LEN


def test_join_contents_drops_empties_and_repeats():
    assert join_contents(["Shoes", " ", "Shoes", "Socks"]) == "Shoes; Socks"
    assert join_contents([None, ""]) is None


def test_normalize_code_trims_and_uppercases():
    assert normalize_code(" rr1sk ") == "RR1SK"


async def test_store_without_a_file_reads_as_empty(hass, hass_storage):
    store = ContentsStore(hass, "e1")
    await store.async_load()
    assert store.get("RR1SK") is None


async def test_store_loads_saved_values_and_ignores_junk(hass, hass_storage):
    hass_storage["slovenska_posta.contents.e1"] = {
        "version": 1,
        "data": {"by_code": {"rr1sk": " Shoes ", "RR2SK": 7, "RR3SK": ""}},
    }
    store = ContentsStore(hass, "e1")
    await store.async_load()
    assert store.get("RR1SK") == "Shoes"
    assert store.get("RR2SK") is None
    assert store.get("RR3SK") is None


async def test_store_with_a_malformed_file_reads_as_empty(hass, hass_storage):
    hass_storage["slovenska_posta.contents.e1"] = {
        "version": 1,
        "data": {"by_code": ["x"]},
    }
    store = ContentsStore(hass, "e1")
    await store.async_load()
    assert store.get("x") is None


async def test_store_set_replace_clear_report_changes(hass, hass_storage):
    store = ContentsStore(hass, "e1")
    await store.async_load()
    assert store.async_set("RR1SK", " Shoes ") is True
    assert store.async_set("rr1sk", "Shoes") is False
    assert store.async_set("RR1SK", "Socks") is True
    assert store.get("RR1SK") == "Socks"
    assert store.async_set("RR1SK", "  ") is True
    assert store.async_set("RR1SK", None) is False
    assert store.get("RR1SK") is None


async def test_store_cap_drops_the_oldest_at_201(hass, hass_storage):
    store = ContentsStore(hass, "e1")
    await store.async_load()
    for number in range(CONTENTS_STORE_MAX_CODES + 1):
        store.async_set(f"CODE{number}", "Shoes")
    assert store.get("CODE0") is None
    assert store.get("CODE1") == "Shoes"
    assert store.get(f"CODE{CONTENTS_STORE_MAX_CODES}") == "Shoes"


async def test_store_remove_deletes_the_file(hass, hass_storage):
    store = ContentsStore(hass, "e1")
    await store.async_load()
    store.async_set("RR1SK", "Shoes")
    with patch.object(store._store, "async_remove") as remove:
        await store.async_remove()
    remove.assert_awaited_once()
    assert store.get("RR1SK") is None
