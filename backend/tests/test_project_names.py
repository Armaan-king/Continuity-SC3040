import asyncio

import pytest

from fuze.api.project_names import project_name, upgrade_legacy_names
from tests.test_store import DB_URL, a_user, fresh


@pytest.mark.parametrize(("brief", "expected"), [
    ("I want to make a small temperature monitor with an OLED", "Temperature Monitor"),
    ("Can you help me build a USB sensor hub using an ESP32?", "USB Sensor Hub"),
    ("I'd like to design a LoRa GPS tracker, solar powered", "LoRa GPS Tracker"),
    ("Please create a BLE beacon that lasts a year", "BLE Beacon"),
    ("3.3V temperature sensor with I2C", "3.3V Temperature Sensor"),
    ("BOM validation", "BOM Validation"),
    ("Please make a small", "Small"),
    ("", "Circuit Design"),
])
def test_descriptive_titles(brief, expected):
    assert project_name(brief) == expected


def test_title_is_bounded_at_word_boundaries():
    assert len(project_name("temperature " * 100)) <= 64
    assert not project_name("temperature " * 100).endswith("Temper")


@pytest.mark.skipif(not DB_URL, reason="requires disposable FUZE_TEST_DB")
def test_first_brief_names_project_and_manual_renames_survive_later_runs():
    async def go():
        async with fresh() as store:
            user = await a_user(store)
            project = await store.create_project(user.id, "Untitled board")
            brief = "I want to make a small temperature monitor with a display"
            await store.create_thread("first", project.id, user.id, brief)
            assert (await store.project_for_user(project.id, user.id)).name == "Temperature Monitor"
            assert (await store.thread_for_user("first", user.id)).prompt == brief
            await store.rename_project(project.id, user.id, "Untitled board")
            await store.create_thread("second", project.id, user.id, "GPS tracker")
            assert (await store.project_for_user(project.id, user.id)).name == "Untitled board"
    asyncio.run(go())


@pytest.mark.skipif(not DB_URL, reason="requires disposable FUZE_TEST_DB")
def test_legacy_prompt_prefix_is_repaired_without_touching_custom_names():
    async def go():
        async with fresh() as store:
            user = await a_user(store)
            brief = "I want to make a small temperature monitor with a display"
            legacy = await store.create_project(user.id, "I want to make a small")
            custom = await store.create_project(user.id, "My Coursework")
            concise = await store.create_project(user.id, "Weather station")
            await store.create_thread("legacy", legacy.id, user.id, brief)
            await store.create_thread("custom", custom.id, user.id, brief)
            await store.create_thread("concise", concise.id, user.id, "Weather station")
            async with store.pool.connection() as conn:
                await upgrade_legacy_names(conn)
            assert (await store.project_for_user(legacy.id, user.id)).name == "Temperature Monitor"
            assert (await store.project_for_user(custom.id, user.id)).name == "My Coursework"
            assert (await store.project_for_user(concise.id, user.id)).name == "Weather station"
    asyncio.run(go())
