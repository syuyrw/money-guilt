#!/usr/bin/env python3
"""Print the total wasted dollars across all transactions.

The widget deliberately never shows this number; run this to see it:

    python3 wasted_total.py
"""
from database import get_total_wasted


if __name__ == '__main__':
    wasted = get_total_wasted()
    print(f"Total wasted: ${wasted['total']:,.2f} across {wasted['count']} transactions")
