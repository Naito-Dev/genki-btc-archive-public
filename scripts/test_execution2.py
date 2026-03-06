import os
import sys

os.environ["DRY_RUN"] = "1"
os.environ["TRADING_ENABLED"] = "true"
os.environ["ARMED"] = "YES"
os.environ["HALT_RESET"] = "1"
import json
try:
    with open("../secrets/.env.live") as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                os.environ[k] = v
except:
    pass

from execute_live_trade import main

for ratio in [0.0, 0.7, 1.0]:
    print(f"\n--- Testing Ratio: {ratio} ---")
    os.environ["FORCE_SYNC_TARGET_RATIO"] = str(ratio)
    os.environ["FORCE_SYNC"] = "1"
    os.environ["EXEC_FORCE_RERUN"] = "1"
    
    try:
        main()
    except Exception as e:
        print(f"Error: {e}")
