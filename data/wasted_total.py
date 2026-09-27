#!/usr/bin/env python3
"""Print the total wasted dollars across all transactions.

The widget deliberately never shows this number; run this to see it:

    python3 data/wasted_total.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.database import get_total_wasted


if __name__ == '__main__':
    wasted = get_total_wasted()
    print(f"Total wasted: ${wasted['total']:,.2f} across {wasted['count']} transactions")
