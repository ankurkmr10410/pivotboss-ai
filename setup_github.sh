#!/bin/bash
# ─────────────────────────────────────────────────────────────
#  PivotBoss AI — GitHub Setup Script
#  Run this ONCE to push your project to GitHub
#
#  Usage:
#    chmod +x setup_github.sh
#    ./setup_github.sh
# ─────────────────────────────────────────────────────────────

set -e   # Stop on any error

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║         PivotBoss AI — GitHub Setup                 ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── STEP 1: Check git is installed ───────────────────────────
if ! command -v git &> /dev/null; then
    echo "❌ Git not installed. Install from https://git-scm.com"
    exit 1
fi
echo "✅ Git found: $(git --version)"

# ── STEP 2: Get GitHub username ──────────────────────────────
echo ""
read -p "📝 Enter your GitHub username: " GITHUB_USER
read -p "📝 Enter repo name (default: pivotboss-ai): " REPO_NAME
REPO_NAME=${REPO_NAME:-pivotboss-ai}

echo ""
echo "Will create: https://github.com/$GITHUB_USER/$REPO_NAME"
echo ""

# ── STEP 3: Init git ─────────────────────────────────────────
cd "$(dirname "$0")"  # Go to project root

if [ ! -d ".git" ]; then
    git init
    echo "✅ Git initialized"
else
    echo "ℹ️  Git already initialized"
fi

# ── STEP 4: Make sure .env is NOT tracked ────────────────────
if [ -f "config/.env" ]; then
    git rm --cached config/.env 2>/dev/null || true
    echo "✅ .env removed from tracking (credentials safe)"
fi

# ── STEP 5: Create data/logs placeholder files ───────────────
mkdir -p logs data
touch logs/.gitkeep data/.gitkeep
echo "✅ Folders created"

# ── STEP 6: First commit ─────────────────────────────────────
git add .
git commit -m "🚀 Initial commit — PivotBoss AI CPR Trading System

- CPR Engine (TC, BC, Pivot, S1-S3, R1-R3, Camarilla)
- Signal Generator (10 Pivot Boss rules)
- Paper Trading Simulator with P&L tracking
- Kotak Neo API connector (mock + live)
- Trading Bot with morning setup + market scan
- Browser-based trading dashboard" 2>/dev/null || echo "ℹ️  Nothing new to commit"

echo "✅ Code committed"

# ── STEP 7: Connect to GitHub ────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════"
echo "  NEXT STEPS — Do these manually:"
echo "══════════════════════════════════════════════════════"
echo ""
echo "1. Go to https://github.com/new"
echo "   - Repository name: $REPO_NAME"
echo "   - Set to PRIVATE (keep your strategy private!)"
echo "   - Do NOT initialize with README (we have one)"
echo "   - Click 'Create repository'"
echo ""
echo "2. Then run these commands:"
echo ""
echo "   git remote add origin https://github.com/$GITHUB_USER/$REPO_NAME.git"
echo "   git branch -M main"
echo "   git push -u origin main"
echo ""
echo "3. For future updates, just run:"
echo "   git add ."
echo '   git commit -m "your message"'
echo "   git push"
echo ""
echo "══════════════════════════════════════════════════════"
echo "✅ Setup complete! Follow the 3 steps above."
echo "══════════════════════════════════════════════════════"
echo ""
