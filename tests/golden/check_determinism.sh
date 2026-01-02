#!/usr/bin/env bash
# Router v0.1 determinism gate
# Ensures byte-identical replay across runs

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CASES_DIR="$SCRIPT_DIR/cases"
ROUTER_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo "Router v0.1 Determinism Gate"
echo "============================"
echo ""

# Check router runtime exists
if [ ! -f "$ROUTER_DIR/router/main.py" ]; then
    echo -e "${RED}✗ router/main.py not found${NC}"
    exit 1
fi

# Find all test cases
CASES=$(find "$CASES_DIR" -name "*.jsonl" -type f | sort)
TOTAL_CASES=$(echo "$CASES" | wc -l | tr -d ' ')
PASSED=0
FAILED=0

echo "Found $TOTAL_CASES test cases"
echo ""

for case_file in $CASES; do
    case_name=$(basename "$case_file" .jsonl)

    # Run replay twice
    output1=$(mktemp)
    output2=$(mktemp)

    cd "$ROUTER_DIR"
    PYTHONPATH="$ROUTER_DIR" python3 -m router.main --replay "$case_file" "$output1" 2>/dev/null
    PYTHONPATH="$ROUTER_DIR" python3 -m router.main --replay "$case_file" "$output2" 2>/dev/null

    # Compare byte-for-byte
    if diff -q "$output1" "$output2" >/dev/null 2>&1; then
        echo -e "${GREEN}✓ $case_name (deterministic)${NC}"
        ((PASSED++))
    else
        echo -e "${RED}✗ $case_name (NON-DETERMINISTIC)${NC}"
        echo "  Run 1: $output1"
        echo "  Run 2: $output2"
        echo "  Diff:"
        diff "$output1" "$output2" | head -20 | sed 's/^/    /'
        ((FAILED++))
    fi

    rm -f "$output1" "$output2"
done

echo ""
echo "============================"
echo "Results: $PASSED passed, $FAILED failed"

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✅ determinism gate passed${NC}"
    exit 0
else
    echo -e "${RED}❌ determinism gate FAILED${NC}"
    exit 1
fi
