import random

import pytest


@pytest.fixture
def rng():
    """Seeded random number generator for reproducible tests."""
    return random.Random(42)
