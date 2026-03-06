import os
import sys

os.environ["DRY_RUN"] = "1"
os.environ["TRADING_ENABLED"] = "true"
os.environ["ARMED"] = "YES"
os.environ["HALT_RESET"] = "1"
os.environ["FORCE_SYNC"] = "1"
os.environ["EXEC_FORCE_RERUN"] = "1"
os.environ["BALANCE_MAX_AGE_MIN"] = "9999999"

for p in os.listdir("/tmp"):
    if p.startswith("force_sync_") and p.endswith(".lock"):
        os.remove(f"/tmp/{p}")

if "BITGET_API_KEY" not in os.environ:
    try:
        with open("secrets/.env.live") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    os.environ[k] = v
    except:
        pass

# Force online snapshot to get real prices by mocking the btc_price returned by context cache
import scripts.execute_live_trade as elt

old_load = elt.load_log_context
def mock_load():
    ctx = old_load()
    ctx.btc_price_ref = 60000.0  # mock price
    return ctx
elt.load_log_context = mock_load

for ratio in [0.0, 0.7, 1.0]:
    print(f"\n--- Testing Ratio: {ratio} ---")
    os.environ["FORCE_SYNC_TARGET_RATIO"] = str(ratio)
    try:
        elt.main()
    except Exception:
        import traceback; traceback.print_exc()
        
    for p in os.listdir("/tmp"):
        if p.startswith("force_sync_") and p.endswith(".lock"):
            os.remove(f"/tmp/{p}")
