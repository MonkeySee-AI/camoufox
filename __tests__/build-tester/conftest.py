from collections.abc import AsyncIterator

import pytest
from playwright.async_api import Playwright, async_playwright


@pytest.fixture
async def playwright() -> AsyncIterator[Playwright]:
    async with async_playwright() as instance:
        yield instance
