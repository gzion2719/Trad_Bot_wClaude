from broker.ibkr_client import IBKRClient

def main():
    client = IBKRClient()
    try:
        client.connect()

        print("\n--- Account Summary ---")
        for item in client.get_account_summary():
            if item.tag in ("NetLiquidation", "TotalCashValue", "BuyingPower"):
                print(f"  {item.tag}: {item.value} {item.currency}")

    except Exception as e:
        print(f"Connection failed: {e}")
    finally:
        client.disconnect()

if __name__ == "__main__":
    main()
