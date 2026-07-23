"""Tests for the headless SWE harness helper functions.

These cover the pure, side-effect-free helpers (repo-name derivation, prompt
construction, metric extraction, artifact-path resolution). The subprocess and
git-clone paths are not exercised here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

# The harness filename uses hyphens, so import it by path rather than name.
_HARNESS_PATH = _SCRIPTS_DIR / "run-swe-headless.py"
_spec = importlib.util.spec_from_file_location("run_swe_headless", _HARNESS_PATH)
assert _spec is not None and _spec.loader is not None
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)

import unittest  # noqa: E402

from dataset_loader import Dataset, DatasetError, Task  # noqa: E402
from runner_config import RunnerConfig  # noqa: E402


def _task(**overrides: object) -> Task:
    """Build a Task with sensible defaults for testing."""
    data: dict[str, object] = {
        "id": "remove-faiss",
        "repo": "https://github.com/agentic-community/mcp-gateway-registry",
        "complexity": "medium",
        "tags": ["python"],
        "problem_statement": "Remove FAISS from the codebase.",
    }
    data.update(overrides)
    return Task.model_validate(data)


def _config(**overrides: object) -> RunnerConfig:
    """Build a RunnerConfig with sensible defaults for testing."""
    data: dict[str, object] = {
        "endpoint": "http://127.0.0.1:8000",
        "model": "qwen3.6-35b",
        "dataset": "dataset/example.yaml",
    }
    data.update(overrides)
    return RunnerConfig.model_validate(data)


class RepoNameTest(unittest.TestCase):
    def test_derives_basename(self) -> None:
        self.assertEqual(
            harness._repo_name("https://github.com/foo/mcp-gateway-registry"),
            "mcp-gateway-registry",
        )

    def test_strips_git_suffix_and_trailing_slash(self) -> None:
        self.assertEqual(harness._repo_name("https://github.com/foo/bar.git/"), "bar")


class BuildPromptTest(unittest.TestCase):
    def test_prompt_has_all_swe_keys(self) -> None:
        prompt = harness._build_prompt(
            _task(), Path("/tmp/x/mcp-gateway-registry"), "1.24.4", "qwen3.6-35b"
        )
        for key in ("repo:", "problem:", "model:", "answers:"):
            self.assertIn(key, prompt)
        self.assertIn("remove-faiss", prompt)

    def test_prompt_includes_issue_url_when_present(self) -> None:
        prompt = harness._build_prompt(
            _task(problem_issue_url="https://github.com/foo/bar/issues/1"),
            Path("/tmp/x/bar"),
            "main",
            "m",
        )
        self.assertIn("Reference issue:", prompt)

    def test_prompt_has_fallback_answers_when_absent(self) -> None:
        prompt = harness._build_prompt(
            _task(clarifying_answers=None), Path("/tmp/x/r"), "main", "m"
        )
        self.assertIn("best judgment", prompt)


class MetricsFromResultTest(unittest.TestCase):
    def test_extracts_six_metrics(self) -> None:
        result = {
            "num_turns": 12,
            "duration_ms": 45000,
            "total_cost_usd": 0.12,
            "is_error": False,
            "session_id": "abc",
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_read_input_tokens": 200,
                "cache_creation_input_tokens": 50,
            },
        }
        metrics = harness._metrics_from_result(result, elapsed=99.0)
        self.assertEqual(metrics["input_tokens"], 1000)
        self.assertEqual(metrics["output_tokens"], 500)
        self.assertEqual(metrics["cache_read_tokens"], 200)
        self.assertEqual(metrics["cache_creation_tokens"], 50)
        self.assertEqual(metrics["num_turns"], 12)
        # duration_ms wins over the measured elapsed time.
        self.assertEqual(metrics["latency_seconds"], 45.0)

    def test_falls_back_to_elapsed_without_duration(self) -> None:
        metrics = harness._metrics_from_result({"usage": {}}, elapsed=7.25)
        self.assertEqual(metrics["latency_seconds"], 7.2)
        self.assertEqual(metrics["num_turns"], 0)


class ArtifactDirTest(unittest.TestCase):
    def test_path_follows_skill_convention(self) -> None:
        path = harness._artifact_dir(_config(output_dir="swe-benchmark-data"), _task())
        self.assertEqual(
            path.parts[-4:],
            (
                "swe-benchmark-data",
                "qwen3.6-35b",
                "mcp-gateway-registry",
                "remove-faiss",
            ),
        )


class BuildClaudeCmdTest(unittest.TestCase):
    def test_never_uses_bypass_permissions(self) -> None:
        cmd = harness._build_claude_cmd(_config(), "prompt")
        joined = " ".join(cmd)
        self.assertNotIn("bypassPermissions", joined)
        self.assertNotIn("dangerously-skip-permissions", joined)
        self.assertIn("acceptEdits", cmd)

    def test_includes_json_output_and_max_turns(self) -> None:
        cmd = harness._build_claude_cmd(_config(max_turns=42), "prompt")
        self.assertIn("json", cmd)
        self.assertIn("42", cmd)

    def test_stream_uses_stream_json_and_verbose(self) -> None:
        cmd = harness._build_claude_cmd(_config(), "prompt", stream=True)
        self.assertIn("stream-json", cmd)
        self.assertIn("--verbose", cmd)

    def test_non_stream_omits_verbose(self) -> None:
        cmd = harness._build_claude_cmd(_config(), "prompt", stream=False)
        self.assertNotIn("--verbose", cmd)
        self.assertIn("json", cmd)

    def test_always_passes_settings(self) -> None:
        # --settings must always be present so it overrides a user's global
        # ~/.claude/settings.json (e.g. one that pins Bedrock routing).
        cmd = harness._build_claude_cmd(_config(), "prompt")
        self.assertIn("--settings", cmd)

    def test_add_dir_when_clone_path_given(self) -> None:
        from pathlib import Path

        clone = Path("/tmp/swe-abc/mcp-gateway-registry")
        cmd = harness._build_claude_cmd(_config(), "prompt", clone_path=clone)
        self.assertIn("--add-dir", cmd)
        self.assertEqual(cmd[cmd.index("--add-dir") + 1], str(clone))

    def test_no_add_dir_without_clone_path(self) -> None:
        cmd = harness._build_claude_cmd(_config(), "prompt")
        self.assertNotIn("--add-dir", cmd)


class BuildSettingsArgTest(unittest.TestCase):
    def test_inline_json_pins_routing_when_no_file(self) -> None:
        import json

        arg = harness._build_settings_arg(_config(endpoint="http://127.0.0.1:8000"))
        settings = json.loads(arg)
        self.assertEqual(settings["env"]["CLAUDE_CODE_USE_BEDROCK"], "0")
        self.assertEqual(settings["env"]["ANTHROPIC_BASE_URL"], "http://127.0.0.1:8000")

    def test_uses_settings_file_when_configured(self) -> None:
        arg = harness._build_settings_arg(
            _config(settings_file="self-hosted/vllm/config/claude-code.json")
        )
        self.assertTrue(arg.endswith("self-hosted/vllm/config/claude-code.json"))


def _dataset(n: int) -> Dataset:
    """Build a dataset with n tasks (ids task-0..task-{n-1})."""
    return Dataset.model_validate(
        {
            "schema_version": "1.0",
            "name": "d",
            "title": "D",
            "description": "test",
            "default_ref": "main",
            "metrics": ["input_tokens", "output_tokens", "num_turns"],
            "complexity_levels": ["low", "medium", "high"],
            "tasks": [
                {
                    "id": f"task-{i}",
                    "repo": "https://github.com/foo/bar",
                    "complexity": "low",
                    "tags": ["x"],
                    "problem_statement": "do the thing",
                }
                for i in range(n)
            ],
        }
    )


class SelectTasksTest(unittest.TestCase):
    def test_count_zero_returns_all(self) -> None:
        tasks = harness._select_tasks(_dataset(3), [], count=0)
        self.assertEqual([t.id for t in tasks], ["task-0", "task-1", "task-2"])

    def test_count_takes_first_n_in_order(self) -> None:
        tasks = harness._select_tasks(_dataset(3), [], count=1)
        self.assertEqual([t.id for t in tasks], ["task-0"])

    def test_count_larger_than_dataset_returns_all(self) -> None:
        tasks = harness._select_tasks(_dataset(2), [], count=99)
        self.assertEqual(len(tasks), 2)

    def test_count_applies_after_task_id_filter(self) -> None:
        tasks = harness._select_tasks(_dataset(4), ["task-1", "task-3"], count=1)
        self.assertEqual([t.id for t in tasks], ["task-1"])

    def test_negative_count_raises(self) -> None:
        with self.assertRaises(DatasetError):
            harness._select_tasks(_dataset(2), [], count=-1)


class FormatStreamEventTest(unittest.TestCase):
    def test_tool_use_event(self) -> None:
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Read"}]},
        }
        self.assertEqual(harness._format_stream_event(event), "[tool] Read")

    def test_assistant_text_event(self) -> None:
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Working on it"}]},
        }
        self.assertIn("Working on it", harness._format_stream_event(event) or "")

    def test_result_event_is_skipped(self) -> None:
        self.assertIsNone(harness._format_stream_event({"type": "result"}))

    def test_empty_content_returns_none(self) -> None:
        event = {"type": "assistant", "message": {"content": []}}
        self.assertIsNone(harness._format_stream_event(event))

    def test_tool_result_string_content_is_printed(self) -> None:
        event = {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "content": "3 matches found"}]
            },
        }
        line = harness._format_stream_event(event)
        self.assertEqual(line, "[tool_result] 3 matches found")

    def test_tool_result_block_list_content_is_printed(self) -> None:
        event = {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "content": [{"type": "text", "text": "line one\nline two"}],
                    }
                ]
            },
        }
        line = harness._format_stream_event(event)
        self.assertIn("line one", line or "")
        self.assertIn("line two", line or "")

    def test_tool_result_is_truncated(self) -> None:
        big = "x" * (harness.TOOL_RESULT_PREVIEW_CHARS + 50)
        event = {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "content": big}]},
        }
        line = harness._format_stream_event(event) or ""
        self.assertIn("+50 chars", line)
        self.assertLess(len(line), len(big))

    def test_verbose_shows_full_tool_result(self) -> None:
        big = "x" * (harness.TOOL_RESULT_PREVIEW_CHARS + 50)
        event = {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "content": big}]},
        }
        line = harness._format_stream_event(event, verbose=True) or ""
        self.assertIn(big, line)
        self.assertNotIn("chars)", line)

    def test_verbose_shows_full_assistant_text(self) -> None:
        big = "y" * 500
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": big}]},
        }
        line = harness._format_stream_event(event, verbose=True) or ""
        self.assertIn(big, line)

    def test_non_verbose_truncates_assistant_text(self) -> None:
        big = "y" * 500
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": big}]},
        }
        line = harness._format_stream_event(event) or ""
        self.assertIn("+300 chars", line)

    def test_tool_result_error_marker(self) -> None:
        event = {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "content": "boom", "is_error": True}
                ]
            },
        }
        self.assertEqual(
            harness._format_stream_event(event), "[tool_result:error] boom"
        )

    def test_empty_tool_result_still_shows_marker(self) -> None:
        event = {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "content": ""}]},
        }
        self.assertEqual(harness._format_stream_event(event), "[tool_result]")


_PROM_SAMPLE = """\
# HELP vllm:prefix_cache_queries_total Queries
# TYPE vllm:prefix_cache_queries_total counter
vllm:prefix_cache_queries_total{engine="0",model_name="m"} 100.0
# TYPE vllm:prefix_cache_hits_total counter
vllm:prefix_cache_hits_total{engine="0",model_name="m"} 40.0
vllm:prefix_cache_hits_total{engine="1",model_name="m"} 10.0
# TYPE vllm:kv_cache_usage_perc gauge
vllm:kv_cache_usage_perc{engine="0",model_name="m"} 0.5
# TYPE vllm:e2e_request_latency_seconds histogram
vllm:e2e_request_latency_seconds_count{engine="0",model_name="m"} 4.0
vllm:e2e_request_latency_seconds_sum{engine="0",model_name="m"} 16.0
vllm:e2e_request_latency_seconds_bucket{le="0.3",engine="0"} 1.0
# TYPE vllm:generation_tokens_created gauge
vllm:generation_tokens_created{engine="0",model_name="m"} 1.78e+09
notvllm:ignored_total{x="y"} 999.0
"""


class ParsePrometheusMetricsTest(unittest.TestCase):
    def test_reads_types_for_vllm_families_only(self) -> None:
        types, _ = harness._parse_prometheus_metrics(_PROM_SAMPLE)
        self.assertEqual(types["vllm:prefix_cache_queries_total"], "counter")
        self.assertEqual(types["vllm:kv_cache_usage_perc"], "gauge")
        self.assertEqual(types["vllm:e2e_request_latency_seconds"], "histogram")

    def test_sums_samples_across_label_sets(self) -> None:
        _, samples = harness._parse_prometheus_metrics(_PROM_SAMPLE)
        # Two engine series for hits (40 + 10) are summed.
        self.assertEqual(samples["vllm:prefix_cache_hits_total"], 50.0)
        self.assertEqual(samples["vllm:prefix_cache_queries_total"], 100.0)

    def test_keeps_histogram_sum_and_count_but_skips_buckets(self) -> None:
        _, samples = harness._parse_prometheus_metrics(_PROM_SAMPLE)
        self.assertEqual(samples["vllm:e2e_request_latency_seconds_count"], 4.0)
        self.assertEqual(samples["vllm:e2e_request_latency_seconds_sum"], 16.0)
        self.assertNotIn("vllm:e2e_request_latency_seconds_bucket", samples)

    def test_ignores_non_vllm_series(self) -> None:
        _, samples = harness._parse_prometheus_metrics(_PROM_SAMPLE)
        self.assertNotIn("notvllm:ignored_total", samples)

    def test_counter_helper_returns_summed_value(self) -> None:
        val = harness._parse_prometheus_counter(
            _PROM_SAMPLE, "vllm:prefix_cache_hits_total"
        )
        self.assertEqual(val, 50.0)

    def test_counter_helper_returns_none_when_absent(self) -> None:
        self.assertIsNone(harness._parse_prometheus_counter(_PROM_SAMPLE, "nope"))


def _snap(**samples: float) -> dict[str, object]:
    """Build a snapshot dict, inferring a type for each sample name."""
    types: dict[str, str] = {}
    for name in samples:
        if name.endswith(("_count", "_sum")):
            types[name.rsplit("_", 1)[0]] = "histogram"
        elif name.endswith("_total"):
            types[name] = "counter"
        else:
            types[name] = "gauge"
    return {"types": types, "samples": dict(samples)}


_FULL_BEFORE = _snap(
    **{
        "vllm:prefix_cache_queries_total": 100.0,
        "vllm:prefix_cache_hits_total": 40.0,
        "vllm:prompt_tokens_total": 1000.0,
        "vllm:prompt_tokens_cached_total": 600.0,
        "vllm:generation_tokens_total": 500.0,
        "vllm:kv_cache_usage_perc": 0.1,
        "vllm:e2e_request_latency_seconds_count": 10.0,
        "vllm:e2e_request_latency_seconds_sum": 30.0,
    }
)
_FULL_AFTER = _snap(
    **{
        "vllm:prefix_cache_queries_total": 300.0,
        "vllm:prefix_cache_hits_total": 140.0,
        "vllm:prompt_tokens_total": 3000.0,
        "vllm:prompt_tokens_cached_total": 2000.0,
        "vllm:generation_tokens_total": 750.0,
        "vllm:kv_cache_usage_perc": 0.0,
        "vllm:e2e_request_latency_seconds_count": 14.0,
        "vllm:e2e_request_latency_seconds_sum": 46.4,
    }
)


class VllmMetricsTest(unittest.TestCase):
    def test_derives_prefix_cache_hit_rate(self) -> None:
        m = harness._vllm_metrics(_FULL_BEFORE, _FULL_AFTER)
        self.assertTrue(m["available"])
        self.assertEqual(m["source"], "vllm_prometheus_window")
        self.assertEqual(m["derived"]["prefix_cache_hit_rate"], 0.5)

    def test_derives_prompt_tokens_cached_rate(self) -> None:
        m = harness._vllm_metrics(_FULL_BEFORE, _FULL_AFTER)
        self.assertEqual(m["derived"]["prompt_tokens_cached_rate"], 0.7)

    def test_reports_every_counter_delta_including_generation_tokens(self) -> None:
        m = harness._vllm_metrics(_FULL_BEFORE, _FULL_AFTER)
        self.assertEqual(m["counters"]["vllm:generation_tokens_total"], 250)
        self.assertEqual(m["counters"]["vllm:prefix_cache_hits_total"], 100)

    def test_histogram_reports_window_mean(self) -> None:
        m = harness._vllm_metrics(_FULL_BEFORE, _FULL_AFTER)
        hist = m["histograms"]["vllm:e2e_request_latency_seconds"]
        self.assertEqual(hist["count"], 4)
        self.assertEqual(hist["sum"], 16.4)
        self.assertEqual(hist["mean"], 4.1)

    def test_gauge_is_instantaneous_post_run_reading(self) -> None:
        m = harness._vllm_metrics(_FULL_BEFORE, _FULL_AFTER)
        # Gauge reports the "after" value, not a delta.
        self.assertEqual(m["gauges"]["vllm:kv_cache_usage_perc"], 0)

    def test_missing_snapshot_marks_unavailable(self) -> None:
        m = harness._vllm_metrics(None, None)
        self.assertFalse(m["available"])
        self.assertIsNone(m["derived"]["prefix_cache_hit_rate"])
        self.assertEqual(m["counters"], {})
        self.assertEqual(m["histograms"], {})

    def test_zero_queries_gives_null_rate_not_divide_by_zero(self) -> None:
        before = _snap(
            **{
                "vllm:prefix_cache_queries_total": 5.0,
                "vllm:prefix_cache_hits_total": 5.0,
            }
        )
        m = harness._vllm_metrics(before, before)
        self.assertEqual(m["counters"]["vllm:prefix_cache_queries_total"], 0)
        self.assertIsNone(m["derived"]["prefix_cache_hit_rate"])

    def test_drops_created_timestamp_series(self) -> None:
        before = _snap(**{"vllm:generation_tokens_created": 1.0})
        after = _snap(**{"vllm:generation_tokens_created": 2.0})
        m = harness._vllm_metrics(before, after)
        self.assertNotIn("vllm:generation_tokens_created", m["gauges"])


class SummaryMetricsTest(unittest.TestCase):
    def test_passes_through_api_tokens_latency_and_turns(self) -> None:
        metrics = {
            "input_tokens": 245870,
            "output_tokens": 6370,
            "latency_seconds": 49.7,
            "num_turns": 14,
        }
        s = harness._summary_metrics(metrics, harness._vllm_metrics(None, None), 128.2)
        self.assertEqual(s["input_tokens"], 245870)
        self.assertEqual(s["output_tokens"], 6370)
        self.assertEqual(s["latency_seconds"], 49.7)
        self.assertEqual(s["num_turns"], 14)
        self.assertEqual(s["generation_tokens_per_sec"], 128.2)

    def test_falls_back_to_vllm_for_cache_tokens_when_api_silent(self) -> None:
        # vLLM does not report per-request cache tokens, so the summary should
        # fall back to prompt_tokens_cached and derive cache-write from the gap.
        vllm = harness._vllm_metrics(
            _snap(
                **{
                    "vllm:prompt_tokens_total": 0.0,
                    "vllm:prompt_tokens_cached_total": 0.0,
                }
            ),
            _snap(
                **{
                    "vllm:prompt_tokens_total": 246414.0,
                    "vllm:prompt_tokens_cached_total": 208032.0,
                }
            ),
        )
        s = harness._summary_metrics({"input_tokens": 1, "output_tokens": 1}, vllm, 0.0)
        self.assertEqual(s["cache_read_tokens"], 208032)
        self.assertEqual(s["cache_write_tokens"], 246414 - 208032)
        self.assertIn("vllm_prometheus", s["sources"]["cache_read_tokens"])

    def test_prefers_api_cache_tokens_when_present(self) -> None:
        metrics = {"cache_read_tokens": 999, "cache_creation_tokens": 111}
        s = harness._summary_metrics(metrics, harness._vllm_metrics(None, None), 0.0)
        self.assertEqual(s["cache_read_tokens"], 999)
        self.assertEqual(s["cache_write_tokens"], 111)
        self.assertIn("claude_api", s["sources"]["cache_read_tokens"])

    def test_surfaces_prefix_hit_rate(self) -> None:
        vllm = harness._vllm_metrics(
            _snap(
                **{
                    "vllm:prefix_cache_queries_total": 0.0,
                    "vllm:prefix_cache_hits_total": 0.0,
                }
            ),
            _snap(
                **{
                    "vllm:prefix_cache_queries_total": 100.0,
                    "vllm:prefix_cache_hits_total": 84.0,
                }
            ),
        )
        s = harness._summary_metrics({}, vllm, 0.0)
        self.assertEqual(s["prefix_cache_hit_rate"], 0.84)

    def test_omits_kv_cache_utilization_from_headline(self) -> None:
        # KV-cache utilization is intentionally NOT a headline metric; the sampled
        # peak/mean lives in vllm_prometheus.gauges_sampled instead.
        vllm = harness._vllm_metrics(
            _snap(**{"vllm:kv_cache_usage_perc": 0.0}),
            _snap(**{"vllm:kv_cache_usage_perc": 0.5}),
        )
        s = harness._summary_metrics({}, vllm, 0.0)
        self.assertNotIn("kv_cache_utilization_perc", s)
        self.assertNotIn("kv_cache_utilization_perc", s["sources"])


class MarkAggregateTest(unittest.TestCase):
    def test_flags_available_block_as_aggregate(self) -> None:
        block = harness._vllm_metrics(_FULL_BEFORE, _FULL_AFTER)
        original_note = block["note"]
        harness._mark_aggregate(block)
        self.assertFalse(block["single_tenant"])
        self.assertIn("AGGREGATE", block["note"])
        self.assertIn(original_note, block["note"])
        # The measured numbers themselves are left untouched.
        self.assertEqual(block["derived"]["prefix_cache_hit_rate"], 0.5)

    def test_noop_when_block_unavailable(self) -> None:
        block = harness._vllm_metrics(None, None)
        harness._mark_aggregate(block)
        self.assertNotIn("single_tenant", block)
        self.assertNotIn("AGGREGATE", block["note"])


class GaugePollerTest(unittest.TestCase):
    def test_summary_reports_peak_and_mean_of_sampled_values(self) -> None:
        poller = harness._GaugePoller("http://127.0.0.1:8000")
        poller._samples["vllm:kv_cache_usage_perc"] = [0.1, 0.5, 0.3]
        summary = poller.summary()
        self.assertTrue(summary["available"])
        self.assertEqual(summary["source"], "vllm_prometheus_poll")
        kv = summary["gauges"]["vllm:kv_cache_usage_perc"]
        self.assertEqual(kv["peak"], 0.5)
        self.assertEqual(kv["mean"], 0.3)
        self.assertEqual(kv["samples"], 3)

    def test_summary_marks_unavailable_when_nothing_sampled(self) -> None:
        poller = harness._GaugePoller("http://127.0.0.1:8000")
        summary = poller.summary()
        self.assertFalse(summary["available"])
        self.assertEqual(summary["gauges"], {})

    def test_summary_reports_null_for_gauge_never_seen(self) -> None:
        poller = harness._GaugePoller("http://127.0.0.1:8000")
        poller._samples["vllm:kv_cache_usage_perc"] = [0.2]
        summary = poller.summary()
        # A gauge the endpoint never exposed is null, not absent.
        running = summary["gauges"]["vllm:num_requests_running"]
        self.assertIsNone(running["peak"])
        self.assertEqual(running["samples"], 0)


class MetricsErrorTest(unittest.TestCase):
    def test_captures_error_message_on_failure(self) -> None:
        result = {
            "is_error": True,
            "api_error_status": 400,
            "result": "API Error (qwen3.6-35b): 400 The provided model identifier is invalid..",
            "usage": {},
        }
        metrics = harness._metrics_from_result(result, elapsed=0.2)
        self.assertTrue(metrics["is_error"])
        self.assertEqual(metrics["api_error_status"], 400)
        self.assertIn("invalid", metrics["error"])

    def test_no_error_field_on_success(self) -> None:
        metrics = harness._metrics_from_result({"is_error": False, "usage": {}}, 1.0)
        self.assertNotIn("error", metrics)


if __name__ == "__main__":
    unittest.main()
