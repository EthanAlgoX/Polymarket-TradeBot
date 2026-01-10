import asyncio
from agents.arbitrage.market_data import MarketDataEngine

async def main():
    print("Initializing MarketDataEngine...")
    engine = MarketDataEngine()

    # Use a known active market/token ID for testing
    # This is a sample token ID; in a real scenario, we'd fetch a list of active markets first
    # For now, let's try to fetch a known market or just fail gracefully if ID is wrong
    # We'll use a placeholder or try to find a valid one via the API if possible.
    # Since we don't have a dynamic list, let's just test the valid instantiation and a dummy call.

    # Valid token ID fetched from Gamma API
    token_id = "2853768819561879023657600399360829876689515906714535926781067187993853038980"

    print(f"Fetching orderbook for token: {token_id}")
    snapshot = engine.fetch_orderbook(token_id)

    if snapshot:
        print("Success!")
        print(f"Best Bid: {snapshot.best_bid}, Best Ask: {snapshot.best_ask}")
        print(f"Spread: {snapshot.spread}")
    else:
        print("Failed to fetch orderbook (or token ID invalid).")

if __name__ == "__main__":
    asyncio.run(main())
