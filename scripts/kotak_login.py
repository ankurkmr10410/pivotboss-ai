#!/usr/bin/env python3
"""
Helper to test Kotak connector login and fetch a sample quote.

Usage:
1. Copy `config/.env.example` -> `config/.env` and fill credentials.
2. Run: `python scripts/kotak_login.py`

This will use the mock connector if credentials are missing.
"""
import sys
from pathlib import Path
proj_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(proj_root))

from dotenv import load_dotenv
load_dotenv(str(proj_root / "config" / ".env"))

from backend.kotak_connector import get_connector, MockKotakConnector


def main():
    conn = get_connector(mock=False)

    print(f"Using environment: {conn.environment}")

    if isinstance(conn, MockKotakConnector):
        print("Using MOCK connector (no credentials found). Sample quote:")
        print(conn.get_quotes("NIFTY"))
        return

    # Initialize client and attempt login/initiate flow
    initiated = conn.login()

    # If legacy OTP initiation returned True, proceed with OTP verification
    if initiated and hasattr(conn, 'client') and hasattr(conn.client, 'login'):
        otp = input("Enter OTP received on your mobile: ").strip()
        if not conn.verify_otp(otp):
            print("OTP verification failed.")
            return

    # If login() didn't initiate legacy OTP but SDK supports TOTP, run TOTP flow
    elif hasattr(conn, 'client') and hasattr(conn.client, 'totp_login'):
        print("Detected SDK with TOTP flow.")
        ucc = input("Enter your UCC (from Kotak Neo profile): ").strip()
        totp = input("Enter TOTP (from authenticator app): ").strip()
        if not conn.totp_login(ucc=ucc, totp=totp):
            print("TOTP login initiation failed.")
            return
        
        # Show what MPIN is being used (for debugging)
        print(f"\n📝 DEBUG: MPIN from environment: '{conn.mpin}' (length: {len(conn.mpin)})")
        
        mpin = input("Enter your MPIN to complete login (or press Enter to use .env value): ").strip()
        if not mpin:
            mpin = conn.mpin
            print(f"Using MPIN from .env: {mpin}")
        
        if not conn.totp_validate(mpin=mpin):
            print("TOTP validate failed.")
            return

    else:
        print("Login initiation failed or unsupported SDK flow. Check config/.env and SDK.")
        return

    print("Logged in successfully. Fetching sample quote for NIFTY:")
    q = conn.get_quotes("NIFTY")
    print(q)


if __name__ == "__main__":
    main()
