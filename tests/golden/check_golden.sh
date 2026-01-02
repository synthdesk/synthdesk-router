#!/usr/bin/env bash
# Router v0.1 golden corpus gate
# Enforces frozen semantics via byte-identical replay

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CASES_DIR="$SCRIPT_DIR/cases"
EXPECTED_DIR="$SCRIPT_DIR/expected"
ROUTER_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "Router v0.1 Golden Corpus Gate"
echo "==============================="
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
    expected_file="$EXPECTED_DIR/$case_name.jsonl"

    if [ ! -f "$expected_file" ]; then
        echo -e "${YELLOW}⊘ $case_name (no expected output)${NC}"
        continue
    fi

    # Run router replay
    actual_output=$(mktemp)

    cd "$ROUTER_DIR"
    PYTHONPATH="$ROUTER_DIR" python3 -m router.main --replay "$case_file" "$actual_output" 2>/dev/null

    # Compare byte-for-byte
    if diff -q "$expected_file" "$actual_output" >/dev/null 2>&1; then
        echo -e "${GREEN}✓ $case_name${NC}"
        ((PASSED++))
    else
        echo -e "${RED}✗ $case_name${NC}"
        echo "  Expected: $expected_file"
        echo "  Actual:   $actual_output"
        echo "  Diff:"
        diff "$expected_file" "$actual_output" | head -20 | sed 's/^/    /'
        ((FAILED++))
    fi

    rm -f "$actual_output"
done

echo ""
echo "==============================="
echo "Results: $PASSED passed, $FAILED failed"

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✅ all golden cases passed${NC}"
    exit 0
else
    echo -e "${RED}❌ golden gate FAILED${NC}"
    exit 1
fi
