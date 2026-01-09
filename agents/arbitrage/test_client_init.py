from py_clob_client.client import ClobClient
from agents.arbitrage.config import HOST, CHAIN_ID

try:
    print("Attempting to init ClobClient without keys...")
    client = ClobClient(host=HOST, chain_id=CHAIN_ID)
    print("Success!")
except Exception as e:
    print(f"Failed: {e}")
