import httpx
import json
from agents.arbitrage.config import GAMMA_API_URL

def get_active_token():
    print(f"Fetching markets from {GAMMA_API_URL}...")
    try:
        # Gamma API endpoint for markets
        # Usually /events or /markets. Let's try /markets usually available in these APIs
        # Polymarket Gamma API often uses /events matching parameters.
        # Let's try a simple query for open markets.

        # According to docs (or common knowledge of Polymarket API), /events returns events.
        # /markets returns markets associated with events.

        url = f"{GAMMA_API_URL}/markets"
        params = {
            "limit": 5,
            "active": "true",
            "closed": "false"
        }

        response = httpx.get(url, params=params)
        response.raise_for_status()
        markets = response.json()

        if not markets:
            print("No markets found.")
            return

        # Handle list vs dict response (Gamma sometimes returns paginated)
        if isinstance(markets, dict) and 'data' in markets: # Unlikely for Gamma, but possible
            data = markets['data']
        else:
            data = markets # Usually a list

        print(f"Found {len(data)} markets. Extracting token IDs...")

        for m in data:
            # We want a clobTokenId usually
            print(f"Market: {m.get('question', 'Unknown')}")
            clob_token_ids = m.get('clobTokenIds', [])
            if clob_token_ids:
                print(f"  - Token IDs: {clob_token_ids}")
                # Use the first one
                return clob_token_ids[0]

            # Sometimes it's asset_id
            asset_id = m.get('asset_id')
            if asset_id:
                print(f"  - Asset ID: {asset_id}")
                return asset_id

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    token_id = get_active_token()
    if token_id:
        print(f"\nVALID_TOKEN_ID = \"{token_id}\"")
    else:
        print("\nCould not find a valid token ID.")
