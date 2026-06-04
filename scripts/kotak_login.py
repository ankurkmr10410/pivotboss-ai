#!/usr/bin/env python3
"""
Helper to test Kotak Neo v2 TOTP login and fetch a sample quote.

Usage:
1. Copy `config/.env.example` to `config/.env` and fill credentials.
2. Run: `python scripts/kotak_login.py`
"""
import sys
from pathlib import Path

proj_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(proj_root))

from dotenv import load_dotenv

load_dotenv(str(proj_root / "config" / ".env"))

from backend.kotak_connector import MockKotakConnector, get_connector


def main():
    conn = get_connector(mock=False)

    print(f"Using environment: {conn.environment}")

    if isinstance(conn, MockKotakConnector):
        print("Using MOCK connector (no credentials found). Sample quote:")
        print(conn.get_quotes("NIFTY"))
        return

    ucc = input("Enter your UCC (or press Enter to use KOTAK_UCC): ").strip() or conn.ucc
    totp = input("Enter current TOTP from authenticator app: ").strip()
    if not conn.totp_login(ucc=ucc, totp=totp):
        print("TOTP login initiation failed. Check KOTAK_CONSUMER_KEY, KOTAK_MOBILE, KOTAK_UCC, and the current TOTP.")
        return

    env_mpin_status = "set" if conn.mpin else "not set"
    print(f"KOTAK_MPIN is {env_mpin_status} in .env.")

    mpin = input("Enter your 6-digit MPIN (or press Enter to use KOTAK_MPIN): ").strip() or conn.mpin
    if not conn.totp_validate(mpin=mpin):
        print("MPIN validation failed. Ensure it is the 6-digit Neo app MPIN, not your TOTP or banking PIN.")
        return

    print("Logged in successfully. Fetching sample quote for NIFTY:")
    print(conn.get_quotes("NIFTY"))


if __name__ == "__main__":
    main()
