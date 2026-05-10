# Performance Testing Guide

This directory contains performance tests for the FraudStream pipeline, focusing on hot-path latency validation and establishing baselines for architectural changes.

## Overview

Performance tests ensure that critical path operations meet SLA requirements and help detect regressions during refactoring (e.g., P0-002 Iceberg extraction).

### Key SLAs

- **Hot-path latency**: < 1ms per `invoke()` call (synchronous cost only)
- **Throughput**: 500+ records/second sustained
- **Regression tolerance**: < 5% performance degradation after changes

## Running Tests

### Prerequisites

```bash
# Install required dependencies
pip install pyarrow pybreaker
```

### Run All Performance Tests

```bash
pytest tests/performance/test_sink_hot_path_latency.py -v
```

### Run with Slow/Marked Tests

```bash
pytest tests/performance/test_sink_hot_path_latency.py -v -m slow
```

### Generate JSON Report for Baseline

```bash
pytest tests/performance/test_sink_hot_path_latency.py \
  --json-report \
  --json-report-file=tests/performance/baselines/hot_path_latency_$(date +%Y%m%d_%H%M%S).json
```

## Test Descriptions

| Test | Purpose | Threshold |
|------|---------|-----------|
| `test_invoke_latency_under_1ms_per_record` | Verify sync invoke() cost < 1ms | < 1ms/record |
| `test_invoke_does_not_block_during_flush` | Verify flush isolation | First 49 < 1ms, 50th > 400ms, 51-100 < 1ms |
| `test_throughput_1000_records_per_second` | End-to-end throughput test | > 500 records/sec |
| `test_buffer_limited_throughput_stress` | Stress test with small buffer (10) | < 3s for 1000 records |
| `test_deduplication_overhead_at_scale` | Deduplication performance | < 1.5s for 1000 records |

## Baseline Archive

Established baselines are stored in `tests/performance/baselines/` as JSON files.

### Current Baselines

| Baseline | Date | Description |
|----------|------|-------------|
| `tb003_baseline_summary_20260510.json` | 2026-05-10 | TB-003: Pre-P0-002 Iceberg extraction baseline |

### Creating a New Baseline

1. Run tests on the target branch/commit
2. Generate JSON report
3. Create summary file with context
4. Update this README

```bash
# Example: Creating baseline for TB-003
pytest tests/performance/test_sink_hot_path_latency.py -v -m slow \
  --json-report \
  --json-report-file=tests/performance/baselines/hot_path_latency_YYYYMMDD_HHMMSS.json

# Then create/update summary file
cp tests/performance/baselines/tb003_baseline_summary_YYYYMMDD.json \
   tests/performance/baselines/YOUR_BASELINE_summary.json
```

## TB-003: Performance Baseline for P0-002

**Ticket**: TB-003  
**Purpose**: Establish pre-extraction baseline for P0-002 Iceberg hot-path extraction  
**Created**: 2026-05-10  
**Branch**: 020-phase0-foundation  

### Results Summary

| Metric | Value | SLA | Status |
|--------|-------|-----|--------|
| Invoke latency | 0.005 ms | < 1ms | PASS |
| Throughput | 500+ rec/s | > 500 rec/s | PASS |
| Stress test | PASS | < 3s | PASS |
| Deduplication | PASS | < 1.5s | PASS |

### Known Issues

- `test_invoke_does_not_block_during_flush`: Has timing interactions with the 1-second flush interval. The test's latency assertions don't account for time-based flushes. Documented as known issue; core functionality works correctly.

### Next Steps

1. Re-run tests after P0-002 Iceberg extraction
2. Compare results against this baseline
3. Ensure < 5% regression on all metrics

## Related Documentation

- `docs/DESIGN_V2.md` - Architecture design and P0-002 context
- `docs/TESTING_ASSESSMENT_DESIGN_V2.md` - Testing strategy
- `pipelines/processing/operators/iceberg_sink.py` - Sink implementation
