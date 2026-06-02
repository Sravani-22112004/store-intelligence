import pytest
import sys
import os

# Make sure app and pipeline are importable from tests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'pipeline'))
