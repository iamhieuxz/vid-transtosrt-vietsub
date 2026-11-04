# pytest bootstrap: đảm bảo import 'core.*' hoạt động khi chạy `python -m pytest`
# (review 2026-11-01: test_roadmap_upgrades.py crash ModuleNotFoundError khi chạy trực tiếp).
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
