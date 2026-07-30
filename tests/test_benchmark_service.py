from sacm.core.benchmark_service import BenchmarkCase, BenchmarkService


def test_benchmark_records_measured_case_outcomes():
    report = BenchmarkService().run(
        [
            BenchmarkCase("completed", "Complete", "A completed case."),
            BenchmarkCase("failed", "Fail", "A failed case."),
        ],
        lambda case: {"status": "COMPLETED" if case.id == "completed" else "FAILED"},
    )

    assert report["case_count"] == 2
    assert report["passed_count"] == 1
    assert report["pass_rate"] == 0.5


def test_benchmark_comparison_uses_reported_metrics():
    baseline = {
        "schema_version": "sacm-benchmark/v1",
        "case_count": 2,
        "pass_rate": 0.5,
        "median_duration_ms": 200,
    }
    candidate = {
        "schema_version": "sacm-benchmark/v1",
        "case_count": 2,
        "pass_rate": 1.0,
        "median_duration_ms": 150,
    }

    comparison = BenchmarkService.compare(baseline, candidate)

    assert comparison["pass_rate_delta"] == 0.5
    assert comparison["median_duration_ms_delta"] == -50


def test_benchmark_suite_requires_unique_non_placeholder_cases():
    cases = [
        BenchmarkCase(f"case-{index}", f"Case {index}", "Deterministic task.")
        for index in range(50)
    ]

    readiness = BenchmarkService.validate_suite(cases)

    assert readiness["ready"] is True
    assert readiness["case_count"] == 50
