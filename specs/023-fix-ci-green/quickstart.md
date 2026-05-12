# Quickstart: Verify CI Green Status Locally

**Feature**: 023-fix-ci-green  
**Purpose**: How to verify the CI fixes work before pushing

---

## Prerequisites

```bash
# Activate virtual environment
source .venv/bin/activate

# Install dependencies
pip install -e ".[dev]"

# Verify tools are available
python --version  # Python 3.11.x
pytest --version  # pytest 8.x
ruff --version    # ruff 0.4.x
```

---

## Verification Steps

### 1. Test Collection (DLQ Fix)

Verify tests can be collected without import errors:

```bash
# Should collect without errors
python -m pytest tests/unit/processing/test_dlq_sink.py --collect-only
```

**Expected**: Lists all test cases without `ImportError` or `AttributeError`

---

### 2. Run DLQ Tests

```bash
# Run the fixed DLQ tests
python -m pytest tests/unit/processing/test_dlq_sink.py -v
```

**Expected**: All tests pass (or skip if Kafka unavailable)

---

### 3. Check Linting

```bash
# Check for violations
ruff check .

# Auto-fix violations
ruff check --fix .

# Verify format
ruff format --check .

# Apply formatting
ruff format .
```

**Expected**: All commands exit with code 0

---

### 4. Run Security Tests

```bash
# Run security module tests
python -m pytest tests/integration/security/ -v --tb=short
```

**Expected**: Tests pass; no hardcoded cert material in output

---

### 5. Full Test Suite

```bash
# Run complete test suite
make test

# Or directly:
python -m pytest tests/ -v --tb=short
```

**Expected**: 
- Collection: No import errors
- Execution: All tests pass
- Coverage: ≥ 80%

---

### 6. Secret Scan (Local)

```bash
# Install gitguardian CLI (if not already)
pip install ggshield

# Scan for secrets
ggshield secret scan repo .
```

**Expected**: No findings (or only false positives marked)

---

### 7. CI Simulation

Run the same steps as CI:

```bash
#!/bin/bash
# ci-simulation.sh

set -e

echo "=== 1. Lint check ==="
ruff check .

echo "=== 2. Format check ==="
ruff format --check .

echo "=== 3. Test suite ==="
python -m pytest tests/ -v --tb=short

echo "=== All checks passed ==="
```

Run: `bash ci-simulation.sh`

---

### 8. Stress Test (Flakiness Check)

Run tests multiple times to verify stability:

```bash
#!/bin/bash
# stress-test.sh

for i in {1..10}; do
    echo "Run $i..."
    python -m pytest tests/integration/security/ -q || {
        echo "FAILED on run $i"
        exit 1
    }
done

echo "All 10 runs passed!"
```

---

## Troubleshooting

### Issue: DLQ tests still fail

**Check**: Verify `ProcessingDLQSink` exists in source:
```bash
grep -r "class ProcessingDLQSink" pipelines/
```

**Fix**: If class name is different, update both source and tests consistently.

---

### Issue: Ruff violations remain after --fix

**Check**: Some violations are not auto-fixable:
```bash
ruff check . --show-source
```

**Fix**: Manually address complex cases (e.g., circular imports, complex line wraps).

---

### Issue: Cert generation fails

**Check**: `cryptography` installed:
```bash
python -c "from cryptography import x509; print('OK')"
```

**Fix**: `pip install cryptography`

---

### Issue: Tests flaky locally

**Check**: Resource cleanup:
```bash
# Check for zombie processes
ps aux | grep pytest

# Check for temp files
ls -la /tmp/pytest-*
```

**Fix**: Ensure all fixtures use `tmp_path` and proper teardown.

---

## Git Workflow

```bash
# 1. Make changes
git checkout -b 023-fix-ci-green

# 2. Fix DLQ tests
# ... edit tests/unit/processing/test_dlq_sink.py ...

# 3. Fix linting
ruff check --fix .
ruff format .

# 4. Fix secrets
# ... edit test fixtures to use runtime generation ...

# 5. Commit
git add .
git commit -m "fix(ci): Restore green build

- Fix DLQ test class references (DLQKafkaProducer -> ProcessingDLQSink)
- Add runtime TLS cert generation for security tests
- Auto-fix ruff violations (E402, I001, UP037, F401, E501)
- Fix test flakiness in security/processing modules

Fixes: 023-fix-ci-green"

# 6. Push and verify CI
git push origin 023-fix-ci-green
```

---

## Expected CI Behavior

After fixes are applied, GitHub Actions should:

1. **Test job**: Collect and run all tests; exit 0
2. **Lint job**: `ruff check .` exits 0
3. **Format job**: `ruff format --check .` exits 0
4. **Secret scan**: GitGuardian reports zero findings
5. **Overall**: Green checkmark ✓

---

## Success Criteria

- [ ] `make test` passes locally
- [ ] `make lint` passes locally
- [ ] `make format` passes locally
- [ ] 10 consecutive test runs pass (stress test)
- [ ] GitGuardian scan clean
- [ ] CI passes on branch
- [ ] 5 consecutive green builds after merge
